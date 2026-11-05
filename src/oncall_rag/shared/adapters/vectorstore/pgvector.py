"""PostgreSQL + pgvector vector store.

The schema is created by the application (not by sql/init.sql) because the vector
column dimension comes from EMBEDDING_DIM.
"""

from collections.abc import AsyncGenerator, Collection, Sequence
from contextlib import asynccontextmanager

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from oncall_rag.shared.errors import (
    ConfigError,
    EmbeddingModelMismatchError,
    IndexSchemaMismatchError,
    VectorStoreUnavailableError,
)
from oncall_rag.shared.models import Chunk, Document, Passage

# Also the longest wait for a free pooled connection.
CONNECT_TIMEOUT_SECONDS = 10
# pgvector's HNSW index supports at most this many dimensions for the `vector` type.
MAX_HNSW_DIMENSIONS = 2000

_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector"

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text NOT NULL UNIQUE,
    title           text NOT NULL,
    content_hash    text NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    embedding_model text NOT NULL,
    indexed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index  integer NOT NULL,
    heading_path text NOT NULL DEFAULT '',
    text         text NOT NULL,
    metadata     jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    embedding    vector({dimension}) NOT NULL,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
"""

_DROP_SCHEMA = "DROP TABLE IF EXISTS chunks, documents"

_STORED_DIMENSION = """
SELECT atttypmod
FROM pg_attribute
WHERE attrelid = to_regclass('chunks') AND attname = 'embedding' AND NOT attisdropped
"""

_UPSERT_DOCUMENT = """
INSERT INTO documents (source, title, content_hash, metadata, embedding_model, indexed_at)
VALUES (%(source)s, %(title)s, %(content_hash)s, %(metadata)s, %(embedding_model)s, now())
ON CONFLICT (source) DO UPDATE SET
    title = EXCLUDED.title,
    content_hash = EXCLUDED.content_hash,
    metadata = EXCLUDED.metadata,
    embedding_model = EXCLUDED.embedding_model,
    indexed_at = EXCLUDED.indexed_at
RETURNING id
"""

_INSERT_CHUNK = """
INSERT INTO chunks (document_id, chunk_index, heading_path, text, metadata, embedding)
VALUES (%s, %s, %s, %s, %s, %s)
"""

# `<=>` is cosine distance; ordering by it lets the HNSW index serve the query.
_SEARCH = """
SELECT d.source, d.title, c.heading_path, c.text, 1 - (c.embedding <=> %(vector)s) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
ORDER BY c.embedding <=> %(vector)s
LIMIT %(top_k)s
"""

Connection = psycopg.AsyncConnection


@asynccontextmanager
async def connect_pgvector_store(
    database_url: str, *, dimension: int, pool_max_size: int
) -> AsyncGenerator["PgVectorStore"]:
    if dimension > MAX_HNSW_DIMENSIONS:
        raise ConfigError(
            f"EMBEDDING_DIM must be at most {MAX_HNSW_DIMENSIONS} (pgvector HNSW index limit)"
        )
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=pool_max_size,
        kwargs={"autocommit": True, "connect_timeout": CONNECT_TIMEOUT_SECONDS},
        configure=_configure_connection,
        # Replaces connections broken by a database restart before they are handed out.
        check=AsyncConnectionPool.check_connection,
        timeout=CONNECT_TIMEOUT_SECONDS,
        open=False,
    )
    async with _database_errors():
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS)
    async with pool:
        yield PgVectorStore(pool, dimension=dimension)


async def _configure_connection(connection: Connection) -> None:
    await connection.execute(_CREATE_EXTENSION)
    await register_vector_async(connection)


@asynccontextmanager
async def _database_errors() -> AsyncGenerator[None]:
    try:
        yield
    except psycopg.OperationalError as error:
        raise VectorStoreUnavailableError(f"PostgreSQL is unavailable: {error}") from error


class PgVectorStore:
    def __init__(self, pool: AsyncConnectionPool[Connection], *, dimension: int) -> None:
        self._pool = pool
        self._dimension = dimension

    async def initialize(self) -> None:
        async with self._connection() as connection:
            stored = await self._stored_dimension(connection)
            if stored is not None and stored != self._dimension:
                raise IndexSchemaMismatchError(stored, self._dimension)
            async with connection.transaction():
                await self._create_schema(connection)

    async def reset(self) -> None:
        async with self._connection() as connection, connection.transaction():
            await connection.execute(_DROP_SCHEMA)
            await self._create_schema(connection)

    async def ping(self) -> None:
        async with self._connection() as connection:
            await connection.execute("SELECT 1")

    async def search(self, vector: Sequence[float], top_k: int) -> list[Passage]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                _SEARCH, {"vector": Vector(list(vector)), "top_k": top_k}
            )
            rows = await cursor.fetchall()
        return [
            Passage(source=source, title=title, heading_path=heading, text=text, score=score)
            for source, title, heading, text, score in rows
        ]

    async def list_sources(self) -> set[str]:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT source FROM documents")
            return {row[0] for row in await cursor.fetchall()}

    async def ensure_compatible(self, embedding_model: str) -> None:
        foreign = await self._stored_embedding_models() - {embedding_model}
        if foreign:
            raise EmbeddingModelMismatchError(foreign, embedding_model)

    async def get_content_hash(self, source: str) -> str | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT content_hash FROM documents WHERE source = %s", (source,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None:
        async with self._connection() as connection, connection.transaction():
            document_id = await self._upsert_document(connection, document, embedding_model)
            await connection.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            await self._insert_chunks(connection, document_id, chunks, embeddings)

    async def delete_documents(self, sources: Collection[str]) -> None:
        async with self._connection() as connection:
            await connection.execute(
                "DELETE FROM documents WHERE source = ANY(%s)", (list(sources),)
            )

    @asynccontextmanager
    async def _connection(self) -> AsyncGenerator[Connection]:
        async with _database_errors(), self._pool.connection() as connection:
            yield connection

    async def _upsert_document(
        self, connection: Connection, document: Document, embedding_model: str
    ) -> int:
        cursor = await connection.execute(
            _UPSERT_DOCUMENT,
            {
                "source": document.source,
                "title": document.title,
                "content_hash": document.content_hash,
                "metadata": Jsonb(dict(document.metadata)),
                "embedding_model": embedding_model,
            },
        )
        row = await cursor.fetchone()
        assert row is not None  # narrows the type: RETURNING always yields a row
        return int(row[0])

    async def _insert_chunks(
        self,
        connection: Connection,
        document_id: int,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        rows = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            metadata = Jsonb(dict(chunk.metadata))
            rows.append(
                (
                    document_id,
                    chunk.chunk_index,
                    chunk.heading_path,
                    chunk.text,
                    metadata,
                    Vector(list(embedding)),
                )
            )
        async with connection.cursor() as cursor:
            await cursor.executemany(_INSERT_CHUNK, rows)

    async def _stored_embedding_models(self) -> set[str]:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT DISTINCT embedding_model FROM documents")
            return {row[0] for row in await cursor.fetchall()}

    async def _stored_dimension(self, connection: Connection) -> int | None:
        cursor = await connection.execute(_STORED_DIMENSION)
        row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def _create_schema(self, connection: Connection) -> None:
        statement = sql.SQL(_CREATE_SCHEMA).format(dimension=sql.Literal(self._dimension))
        await connection.execute(statement)
