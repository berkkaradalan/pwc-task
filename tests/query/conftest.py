import pytest
from fastapi.testclient import TestClient

from oncall_rag.query.dependencies import get_answer_service, get_vector_store
from oncall_rag.query.main import create_app
from oncall_rag.query.rag.graph import RetrievalConfig, build_graph
from oncall_rag.query.services.answer_service import AnswerService
from tests.fakes import FakeEmbedder, FakeLanguageModel, InMemoryVectorStore

RETRIEVAL = RetrievalConfig(top_k=4, min_similarity=0.5)


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def llm() -> FakeLanguageModel:
    return FakeLanguageModel(answer="Scale out the workers.")


@pytest.fixture
def client(store: InMemoryVectorStore, llm: FakeLanguageModel) -> TestClient:
    """The API wired to fakes. Not used as a context manager, so the real lifespan never runs."""
    app = create_app()
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)
    answer_service = AnswerService(graph)
    app.dependency_overrides[get_answer_service] = lambda: answer_service
    app.dependency_overrides[get_vector_store] = lambda: store
    return TestClient(app)
