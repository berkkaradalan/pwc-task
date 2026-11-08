from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oncall_rag.query import main
from oncall_rag.query.main import create_app
from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import Document
from tests.fakes import FAKE_DIMENSION, FakeProviders

DATABASE_URL = "postgresql://user:password@db/test"
LOCAL_EMBEDDING_MODEL = "local-embedding"


@pytest.fixture
def api_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings from the environment, as in production. Logging setup is tested on its own."""
    # Run outside the repository so a developer's .env is never read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RUN_MODE", raising=False)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test")
    monkeypatch.setenv("LOCAL_EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL)
    monkeypatch.setenv("CLOUD_EMBEDDING_MODEL", "cloud-embedding")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-llm")
    monkeypatch.setenv("CLOUD_LLM_MODEL", "cloud-llm")
    monkeypatch.setenv("EMBEDDING_DIM", str(FAKE_DIMENSION))
    # Keeps pytest's log capture in place.
    monkeypatch.setattr(main, "configure_api_logging", lambda: None)


@pytest.mark.usefixtures("api_environment")
def test_api_serves_requests_after_starting_with_settings_from_the_environment(
    fake_providers: FakeProviders,
) -> None:
    with TestClient(create_app()) as client:
        health = client.get("/health")
        answer = client.post("/ask", json={"question": "How do I fix the backlog?"})

    assert health.status_code == 200
    assert answer.status_code == 200
    assert answer.json()["answer"] == fake_providers.chat_api.answer
    [connection] = fake_providers.connector.connections
    assert connection.database_url == DATABASE_URL
    assert fake_providers.store.compatibility_checks == [LOCAL_EMBEDDING_MODEL]


@pytest.mark.usefixtures("api_environment")
async def test_api_refuses_to_start_on_an_index_with_another_identity(
    fake_providers: FakeProviders,
) -> None:
    document = Document(source="a.md", title="A", content="", content_hash="h", metadata={})
    await fake_providers.store.replace_document(document, [], [], "other-model")

    with pytest.raises(ReindexRequiredError, match="other-model"), TestClient(create_app()):
        pass
