"""Composition root: the only place where concrete adapters are built from settings."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from oncall_rag.ingest.chunker import ChunkingConfig
from oncall_rag.ingest.pipeline import IngestionPipeline
from oncall_rag.shared.adapters.embeddings.ollama import OllamaEmbedder, build_http_client
from oncall_rag.shared.adapters.vectorstore.pgvector import connect_pgvector_store
from oncall_rag.shared.config import IngestSettings

# Ingestion runs one statement at a time.
INGEST_CONNECTIONS = 1


@asynccontextmanager
async def ingestion_pipeline(settings: IngestSettings) -> AsyncGenerator[IngestionPipeline]:
    async with (
        build_http_client(settings.embedding_base_url) as http_client,
        connect_pgvector_store(
            settings.database_url.get_secret_value(),
            dimension=settings.embedding_dim,
            pool_max_size=INGEST_CONNECTIONS,
        ) as store,
    ):
        embedder = OllamaEmbedder(
            http_client,
            model=settings.embedding_model,
            dimension=settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
        )
        chunking = ChunkingConfig(
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        yield IngestionPipeline(embedder=embedder, store=store, chunking=chunking)
