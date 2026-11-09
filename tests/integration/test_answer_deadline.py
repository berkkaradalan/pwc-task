"""A request deadline that expires while a search waits inside PostgreSQL must give the pooled
connection back, or the next request would wait for a connection the pool no longer has."""

import asyncio

import psycopg
import pytest

from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.adapters.vectorstore.pgvector import connect_pgvector_store
from tests.fakes import FAKE_DIMENSION, FakeEmbedder, FakeLanguageModel

pytestmark = pytest.mark.integration

DEADLINE_SECONDS = 0.3
# Far below the pool's own 10 s wait for a free connection.
CONNECTION_WAIT_SECONDS = 2
QUESTION = "How do I restart the worker?"


async def test_a_deadline_during_a_search_returns_the_pooled_connection(database_url: str) -> None:
    async with connect_pgvector_store(
        database_url, dimension=FAKE_DIMENSION, pool_max_size=1
    ) as store:
        await store.initialize()
        retrieval = RetrievalConfig(top_k=4, min_similarity=0.5)
        graph = build_graph(FakeEmbedder(), store, FakeLanguageModel(), retrieval)
        answer_service = AnswerService(graph, deadline_seconds=DEADLINE_SECONDS)

        async with await psycopg.AsyncConnection.connect(database_url) as blocker:
            # The search reads chunks, so it waits for this lock until the deadline expires.
            await blocker.execute("LOCK TABLE chunks IN ACCESS EXCLUSIVE MODE")
            with pytest.raises(TimeoutError, match=r"the request deadline of 0\.3 s expired"):
                await answer_service.answer(QUESTION, [])
            await blocker.rollback()

        async with asyncio.timeout(CONNECTION_WAIT_SECONDS):
            await store.ping()
            candidates = await store.search([0.0] * FAKE_DIMENSION, QUESTION, limit=1)
        assert candidates.by_vector == []
