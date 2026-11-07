"""Composition root: the only place where concrete adapters are built from settings."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.adapters.embeddings.openai_compatible import (
    OpenAICompatibleEmbedder,
    build_embedding_client,
)
from oncall_rag.shared.adapters.llm.openai_compatible import (
    OpenAICompatibleLLM,
    build_llm_client,
)
from oncall_rag.shared.adapters.vectorstore.pgvector import connect_pgvector_store
from oncall_rag.shared.config import QuerySettings
from oncall_rag.shared.ports import PassageSearch


@asynccontextmanager
async def answer_service_and_search(
    settings: QuerySettings,
) -> AsyncGenerator[tuple[AnswerService, PassageSearch]]:
    """The answer service and the passage search it uses; every client closes on exit."""
    async with (
        build_embedding_client(settings.embedding_base_url) as embedding_client,
        connect_pgvector_store(
            settings.database_url.get_secret_value(),
            dimension=settings.embedding_dim,
            pool_max_size=settings.db_pool_max_size,
        ) as store,
        build_llm_client(
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            settings.llm_timeout_seconds,
        ) as llm_client,
    ):
        embedder = OpenAICompatibleEmbedder(
            embedding_client,
            model=settings.embedding_model,
            dimension=settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
        )
        llm = OpenAICompatibleLLM(
            llm_client, model=settings.llm_model, max_concurrency=settings.llm_max_concurrency
        )
        retrieval = RetrievalConfig(
            top_k=settings.retrieval_top_k, min_similarity=settings.retrieval_min_similarity
        )
        graph = build_graph(embedder, store, llm, retrieval)
        yield AnswerService(graph), store
