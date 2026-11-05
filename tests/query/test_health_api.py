from fastapi.testclient import TestClient

from tests.fakes import FakeLanguageModel, InMemoryVectorStore


def test_health_is_ok_when_the_database_answers(client: TestClient, llm: FakeLanguageModel) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert llm.calls == []


def test_health_is_503_when_the_database_is_down(
    client: TestClient, store: InMemoryVectorStore
) -> None:
    store.is_down = True

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "failed"}
