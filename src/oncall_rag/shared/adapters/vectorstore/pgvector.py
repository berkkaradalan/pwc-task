"""PostgreSQL + pgvector vector store.

The schema is created by the application (not by sql/init.sql) because the vector
column dimension comes from EMBEDDING_DIM.
"""

from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg.types.json import Jsonb

from oncall_rag.shared.errors import (
    ConfigError,
    IndexSchemaMismatchError,
    VectorStoreUnavailableError,
)
from oncall_rag.shared.models import Chunk, Document

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


@asynccontextmanager
async def connect_pgvector_store(database_url: str, *, dimension: int) -> AsyncIterator["PgVectorStore"]:
    if dimension > MAX_HNSW_DIMENSIONS:
        raise ConfigError(
            f"EMBEDDING_DIM must be at most {MAX_HNSW_DIMENSIONS} (pgvector HNSW index limit)"
        )
    try:
        connection = await psycopg.AsyncConnection.connect(
            database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
        )
    except psycopg.OperationalError as error:
        raise VectorStoreUnavailableError(f"Cannot connect to PostgreSQL: {error}") from error
    async with connection:
        yield PgVectorStore(connection, dimension=dimension)


@asynccontextmanager
async def _database_errors() -> AsyncIterator[None]:
    try:
        yield
    except psycopg.OperationalError as error:
        raise VectorStoreUnavailableError(f"PostgreSQL error: {error}") from error


class PgVectorStore:
    def __init__(self, connection: psycopg.AsyncConnection, *, dimension: int) -> None:
        self._connection = connection
        self._dimension = dimension

    async def initialize(self) -> None:
        async with _database_errors():
            await self._connection.execute(_CREATE_EXTENSION)
            await register_vector_async(self._connection)
            stored = await self._stored_dimension()
            if stored is not None and stored != self._dimension:
                raise IndexSchemaMismatchError(stored, self._dimension)
            async with self._connection.transaction():
                await self._create_schema()

    async def reset(self) -> None:
        async with _database_errors():
            async with self._connection.transaction():
                await self._connection.execute(_CREATE_EXTENSION)
                await self._connection.execute(_DROP_SCHEMA)
                await self._create_schema()
            await register_vector_async(self._connection)

    async def list_sources(self) -> set[str]:
        async with _database_errors():
            cursor = await self._connection.execute("SELECT source FROM documents")
            return {row[0] for row in await cursor.fetchall()}

    async def list_embedding_models(self) -> set[str]:
        async with _database_errors():
            cursor = await self._connection.execute("SELECT DISTINCT embedding_model FROM documents")
            return {row[0] for row in await cursor.fetchall()}

    async def get_content_hash(self, source: str) -> str | None:
        async with _database_errors():
            cursor = await self._connection.execute(
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
        async with _database_errors(), self._connection.transaction():
            document_id = await self._upsert_document(document, embedding_model)
            await self._connection.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            await self._insert_chunks(document_id, chunks, embeddings)

    async def delete_documents(self, sources: Collection[str]) -> None:
        async with _database_errors():
            await self._connection.execute(
                "DELETE FROM documents WHERE source = ANY(%s)", (list(sources),)
            )

    async def _upsert_document(self, document: Document, embedding_model: str) -> int:
        cursor = await self._connection.execute(
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
        return row[0]

    async def _insert_chunks(
        self,
        document_id: int,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        rows = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            metadata = Jsonb(dict(chunk.metadata))
            rows.append(
                (document_id, chunk.chunk_index, chunk.heading_path, chunk.text, metadata, Vector(list(embedding)))
            )
        async with self._connection.cursor() as cursor:
            await cursor.executemany(_INSERT_CHUNK, rows)

    async def _stored_dimension(self) -> int | None:
        cursor = await self._connection.execute(_STORED_DIMENSION)
        row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def _create_schema(self) -> None:
        statement = sql.SQL(_CREATE_SCHEMA).format(dimension=sql.Literal(self._dimension))
        await self._connection.execute(statement)
