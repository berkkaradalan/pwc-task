import httpx2
import openai
import psycopg
import psycopg_pool
import pytest
from fastapi.testclient import TestClient

from oncall_rag.query.dependencies import get_answer_service
from oncall_rag.query.main import create_app
from oncall_rag.query.rag.prompts import NO_CONTEXT_ANSWER
from oncall_rag.query.schemas.ask import EXCERPT_MAX_CHARS, QUESTION_MAX_CHARS, AskResponse
from oncall_rag.shared.errors import ProviderResponseError
from oncall_rag.shared.models import Passage
from tests.fakes import FakeLanguageModel, InMemoryVectorStore

LONG_TEXT = "Check the queue depth. " * 40
LLM_REQUEST = httpx2.Request("POST", "http://llm.internal/v1/chat/completions")


def status_error(error_type: type[openai.APIStatusError], status_code: int) -> Exception:
    response = httpx2.Response(status_code, request=LLM_REQUEST)
    return error_type("error from llm.internal", response=response, body=None)


def passage(source: str, score: float) -> Passage:
    return Passage(
        source=source,
        title="Payments worker queue backlog",
        heading_path="Mitigation",
        text=LONG_TEXT,
        score=score,
    )


def test_answered_question_returns_answer_and_sources(
    client: TestClient, store: InMemoryVectorStore, llm: FakeLanguageModel
) -> None:
    store.search_results = [passage("backlog.md", 0.82), passage("other.md", 0.1)]

    response = client.post("/ask", json={"question": "  How do I drain the backlog?  "})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Scale out the workers."
    assert body["status"] == "answered"
    [source] = body["sources"]
    assert source["source"] == "backlog.md"
    assert source["title"] == "Payments worker queue backlog"
    assert source["heading_path"] == "Mitigation"
    assert source["score"] == pytest.approx(0.82)
    assert len(source["excerpt"]) <= EXCERPT_MAX_CHARS
    assert LONG_TEXT.startswith(source["excerpt"].removesuffix(" ..."))
    [(_, user_prompt)] = llm.calls
    assert "Question: How do I drain the backlog?" in user_prompt


def test_scores_are_rounded_to_three_decimals(
    client: TestClient, store: InMemoryVectorStore
) -> None:
    store.search_results = [passage("backlog.md", 0.8112061923951416)]

    response = client.post("/ask", json={"question": "How do I drain the backlog?"})

    [source] = response.json()["sources"]
    assert source["score"] == 0.811


def test_no_relevant_passages_returns_fixed_answer_without_calling_the_llm(
    client: TestClient, store: InMemoryVectorStore, llm: FakeLanguageModel
) -> None:
    store.search_results = [passage("backlog.md", 0.2)]

    response = client.post("/ask", json={"question": "What is the capital of France?"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": NO_CONTEXT_ANSWER,
        "status": "no_relevant_context",
        "sources": [],
    }
    assert llm.calls == []


@pytest.mark.parametrize(
    "question",
    ["", "   ", "x" * (QUESTION_MAX_CHARS + 1)],
    ids=["empty", "whitespace", "too-long"],
)
def test_invalid_question_is_rejected_with_the_error_body(
    client: TestClient, question: str
) -> None:
    response = client.post("/ask", json={"question": question})

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "question"]


def test_missing_body_is_rejected_with_the_error_body(client: TestClient) -> None:
    response = client.post("/ask")

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body"]


class FailingAnswerService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def answer(self, question: str) -> AskResponse:
        raise self.error


def client_with_failing_service(error: Exception) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_answer_service] = lambda: FailingAnswerService(error)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (openai.APIConnectionError(request=LLM_REQUEST), 503),
        (openai.APITimeoutError(request=LLM_REQUEST), 504),
    ],
)
def test_llm_failures_map_to_gateway_errors(
    client: TestClient,
    store: InMemoryVectorStore,
    llm: FakeLanguageModel,
    error: Exception,
    status_code: int,
) -> None:
    store.search_results = [passage("backlog.md", 0.9)]
    llm.error = error

    response = client.post("/ask", json={"question": "How do I drain the backlog?"})

    assert response.status_code == status_code
    assert "llm.internal" not in response.json()["detail"]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        pytest.param(openai.APITimeoutError(request=LLM_REQUEST), 504, id="timeout"),
        pytest.param(openai.APIConnectionError(request=LLM_REQUEST), 503, id="connection"),
        pytest.param(status_error(openai.RateLimitError, 429), 503, id="rate-limit"),
        pytest.param(status_error(openai.InternalServerError, 500), 503, id="server-error"),
        pytest.param(psycopg.OperationalError("db.internal down"), 503, id="database-down"),
        pytest.param(psycopg_pool.PoolTimeout("db.internal pool"), 503, id="pool-timeout"),
        pytest.param(status_error(openai.AuthenticationError, 401), 502, id="rejected"),
        pytest.param(ProviderResponseError("llm.internal empty"), 502, id="unusable-answer"),
    ],
)
def test_service_failures_map_to_http_errors_without_internal_details(
    error: Exception, status_code: int
) -> None:
    client = client_with_failing_service(error)

    response = client.post("/ask", json={"question": "How do I drain the backlog?"})

    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert "llm.internal" not in detail
    assert "db.internal" not in detail


def test_unexpected_error_returns_a_generic_500() -> None:
    client = client_with_failing_service(RuntimeError("secret internal detail"))

    response = client.post("/ask", json={"question": "anything"})

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
