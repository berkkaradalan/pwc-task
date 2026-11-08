from collections.abc import Callable, Sequence

import pytest
from fastapi.testclient import TestClient

from oncall_rag.query.core import wiring
from oncall_rag.query.core.dependencies import get_answer_service, get_passage_search
from oncall_rag.query.main import create_app
from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.schemas.ask import AskResponse, HistoryMessage
from oncall_rag.query.services.answer_service import AnswerService
from oncall_rag.shared.models import Passage
from tests.fakes import (
    FAKE_DIMENSION,
    FakeChatApi,
    FakeClientBuilder,
    FakeEmbedder,
    FakeEmbeddingsApi,
    FakeLanguageModel,
    FakeProviders,
    FakeStoreConnector,
    InMemoryVectorStore,
)

RETRIEVAL = RetrievalConfig(top_k=4, min_similarity=0.5)


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLanguageModel:
    return FakeLanguageModel("Scale out the workers.")


@pytest.fixture
def client(
    embedder: FakeEmbedder, store: InMemoryVectorStore, llm: FakeLanguageModel
) -> TestClient:
    """The API wired to fakes. Not used as a context manager, so the real lifespan never runs."""
    app = create_app()
    graph = build_graph(embedder, store, llm, RETRIEVAL)
    answer_service = AnswerService(graph)
    app.dependency_overrides[get_answer_service] = lambda: answer_service
    app.dependency_overrides[get_passage_search] = lambda: store
    return TestClient(app)


class FailingAnswerService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def answer(self, question: str, history: Sequence[HistoryMessage]) -> AskResponse:
        raise self.error


@pytest.fixture
def failing_client() -> Callable[[Exception], TestClient]:
    """Builds an API whose answer service raises `error`; server errors become responses."""

    def build(error: Exception) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_answer_service] = lambda: FailingAnswerService(error)
        return TestClient(app, raise_server_exceptions=False)

    return build


@pytest.fixture
def fake_providers(monkeypatch: pytest.MonkeyPatch, store: InMemoryVectorStore) -> FakeProviders:
    """The real query wiring, on fakes. Every search finds one passage that passes the gate."""
    relevant = Passage(
        source="redis.md", title="Redis", heading_path="Mitigation", text="Scale out.", score=0.9
    )
    store.vector_results = [relevant]
    embeddings_api = FakeEmbeddingsApi(FAKE_DIMENSION)
    chat_api = FakeChatApi("Scale out the workers.")
    providers = FakeProviders(
        store=store,
        connector=FakeStoreConnector(store),
        embeddings_api=embeddings_api,
        chat_api=chat_api,
        embedding_clients=FakeClientBuilder(embeddings_api),
        llm_clients=FakeClientBuilder(chat_api),
    )
    monkeypatch.setattr(wiring, "connect_pgvector_store", providers.connector)
    monkeypatch.setattr(wiring, "build_embedding_client", providers.embedding_clients)
    monkeypatch.setattr(wiring, "build_llm_client", providers.llm_clients)
    return providers
