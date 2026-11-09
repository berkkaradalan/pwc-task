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
    """The answer service and the passage search it uses; every client closes on exit.

    Refuses to start on a missing index or one built with other embeddings or dimension.
    """
    embedding_endpoint = settings.embedding_endpoint
    llm_endpoint = settings.llm_endpoint
    async with (
        build_embedding_client(
            embedding_endpoint.base_url, embedding_endpoint.api_key.get_secret_value()
        ) as embedding_client,
        connect_pgvector_store(
            settings.database_url.get_secret_value(),
            dimension=settings.embedding_dim,
            pool_max_size=settings.db_pool_max_size,
        ) as store,
        build_llm_client(
            llm_endpoint.base_url,
            llm_endpoint.api_key.get_secret_value(),
            settings.llm_timeout_seconds,
        ) as llm_client,
    ):
        embedder = OpenAICompatibleEmbedder(
            embedding_client,
            model=embedding_endpoint.model,
            provider=embedding_endpoint.provider,
            dimension=settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
        )
        llm = OpenAICompatibleLLM(
            llm_client, model=llm_endpoint.model, max_concurrency=settings.llm_max_concurrency
        )
        retrieval = RetrievalConfig(
            top_k=settings.retrieval_top_k, min_similarity=settings.retrieval_min_similarity
        )
        await store.ensure_compatible(embedder.embedding_identity)
        graph = build_graph(embedder, store, llm, retrieval)
        yield AnswerService(graph, deadline_seconds=settings.ask_deadline_seconds), store
