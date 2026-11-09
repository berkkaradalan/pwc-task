"""The deadline for a whole /ask request, with the real language model adapter and its
semaphore on a fake chat API. Requests go through ASGI on the test's event loop, so a test
can hold the only language model slot while a request waits for it."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

import httpx2
import openai
import pytest
from asgi_correlation_id import CorrelationIdFilter

from oncall_rag.query.core.dependencies import get_answer_service
from oncall_rag.query.core.middleware import API_LOG_FORMAT, REQUEST_ID_HEADER
from oncall_rag.query.main import create_app
from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.adapters.llm.openai_compatible import OpenAICompatibleLLM
from oncall_rag.shared.models import Passage
from tests.fakes import FAKE_PROVIDER_API_URL, FakeChatApi, FakeEmbedder, InMemoryVectorStore

DEADLINE_SECONDS = 0.2
# Far beyond the deadline; a request that waits this long has not been cut off.
SLOW_MODEL_SECONDS = 30
# Scheduling slack on top of the deadline.
DEADLINE_TOLERANCE_SECONDS = 0.5
TIMEOUT_DETAIL = "A required service did not answer in time."
ROUTER_LOGGER = "oncall_rag.query.routers.ask"
QUESTION = {"question": "How do I fix the backlog?"}


@pytest.fixture
def chat_api() -> FakeChatApi:
    return FakeChatApi("Scale out the workers.")


@pytest.fixture
def llm(chat_api: FakeChatApi) -> OpenAICompatibleLLM:
    """One slot, so a single call holds every slot."""
    client = openai.AsyncOpenAI(
        base_url=FAKE_PROVIDER_API_URL,
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(chat_api)),
    )
    return OpenAICompatibleLLM(client, model="test-model", max_concurrency=1)


@pytest.fixture
async def api(llm: OpenAICompatibleLLM) -> AsyncIterator[httpx2.AsyncClient]:
    """The API with a passage that always passes the gate, so every question calls the model."""
    store = InMemoryVectorStore()
    store.vector_results = [
        Passage(source="a.md", title="A", heading_path="Fix", text="Scale out.", score=0.9)
    ]
    graph = build_graph(FakeEmbedder(), store, llm, RetrievalConfig(top_k=4, min_similarity=0.5))
    answer_service = AnswerService(graph, deadline_seconds=DEADLINE_SECONDS)
    app = create_app()
    app.dependency_overrides[get_answer_service] = lambda: answer_service
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


async def ask_timed(api: httpx2.AsyncClient) -> tuple[httpx2.Response, float]:
    started = time.monotonic()
    response = await api.post("/ask", json=QUESTION)
    return response, time.monotonic() - started


async def test_a_slow_model_gets_504_with_the_fixed_detail_when_the_deadline_expires(
    api: httpx2.AsyncClient, chat_api: FakeChatApi, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.handler.addFilter(CorrelationIdFilter())
    caplog.handler.setFormatter(logging.Formatter(API_LOG_FORMAT))
    caplog.set_level(logging.WARNING, logger=ROUTER_LOGGER)
    chat_api.delay_seconds = SLOW_MODEL_SECONDS

    response, elapsed = await ask_timed(api)

    assert response.status_code == 504
    assert response.json() == {"detail": TIMEOUT_DETAIL}
    assert elapsed < DEADLINE_SECONDS + DEADLINE_TOLERANCE_SECONDS
    [line] = [line for line in caplog.text.splitlines() if "Answering failed" in line]
    assert f"[{response.headers[REQUEST_ID_HEADER]}]" in line
    assert "the request deadline of 0.2 s expired" in line


async def test_a_request_inside_the_deadline_is_answered(
    api: httpx2.AsyncClient, chat_api: FakeChatApi
) -> None:
    response = await api.post("/ask", json=QUESTION)

    assert response.status_code == 200
    assert response.json()["answer"] == chat_api.answer


async def test_the_language_model_slot_is_free_again_after_a_deadline_expiry(
    api: httpx2.AsyncClient, chat_api: FakeChatApi
) -> None:
    chat_api.delay_seconds = SLOW_MODEL_SECONDS
    expired = await api.post("/ask", json=QUESTION)
    chat_api.delay_seconds = 0

    answered = await api.post("/ask", json=QUESTION)

    assert expired.status_code == 504
    assert answered.status_code == 200


async def test_the_deadline_covers_the_wait_for_a_language_model_slot(
    api: httpx2.AsyncClient, chat_api: FakeChatApi, llm: OpenAICompatibleLLM
) -> None:
    chat_api.delay_seconds = SLOW_MODEL_SECONDS
    slot_holder = asyncio.create_task(llm.complete("system", "holds the only slot"))
    async with asyncio.timeout(DEADLINE_TOLERANCE_SECONDS):
        while not chat_api.payloads:
            await asyncio.sleep(0)

    try:
        response, elapsed = await ask_timed(api)
    finally:
        slot_holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await slot_holder

    assert response.status_code == 504
    assert elapsed < DEADLINE_SECONDS + DEADLINE_TOLERANCE_SECONDS
    # Only the slot holder reached the model; the request never got past the semaphore.
    assert len(chat_api.payloads) == 1
