import pytest
from fastapi.testclient import TestClient

from oncall_rag.query.dependencies import get_answer_service, get_passage_search
from oncall_rag.query.main import create_app
from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.services.answer_service import AnswerService
from tests.fakes import FakeEmbedder, FakeLanguageModel, InMemoryVectorStore

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
