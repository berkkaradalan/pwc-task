from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from oncall_rag.ingest import wiring
from oncall_rag.ingest.wiring import INGEST_CONNECTIONS, ingestion_pipeline
from oncall_rag.shared.config import OPENROUTER_BASE_URL, IngestSettings, RunMode
from tests.fakes import (
    FAKE_DIMENSION,
    FakeClientBuilder,
    FakeEmbeddingsApi,
    FakeStoreConnector,
    InMemoryVectorStore,
    StoreConnection,
)

DATABASE_URL = "postgresql://user:password@db/test"
OLLAMA_BASE_URL = "http://ollama.test"
OPENROUTER_KEY = "test-openrouter-key"
EMBEDDING_PROVIDER = "deepinfra"
LOCAL_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
CLOUD_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
# The smallest allowed value; with the default, a fixture document has a longer chunk.
CHUNK_MAX_CHARS = 100
PINNED_ROUTING = {"order": [EMBEDDING_PROVIDER], "allow_fallbacks": False}


@dataclass(frozen=True, slots=True)
class FakeIngestProviders:
    store: InMemoryVectorStore
    connector: FakeStoreConnector
    embeddings_api: FakeEmbeddingsApi
    embedding_clients: FakeClientBuilder


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> FakeIngestProviders:
    """The real ingest wiring, on fakes instead of the database pool and the SDK client."""
    store = InMemoryVectorStore()
    embeddings_api = FakeEmbeddingsApi(FAKE_DIMENSION)
    fakes = FakeIngestProviders(
        store=store,
        connector=FakeStoreConnector(store),
        embeddings_api=embeddings_api,
        embedding_clients=FakeClientBuilder(embeddings_api),
    )
    monkeypatch.setattr(wiring, "connect_pgvector_store", fakes.connector)
    monkeypatch.setattr(wiring, "build_embedding_client", fakes.embedding_clients)
    return fakes


def ingest_settings(run_mode: RunMode) -> IngestSettings:
    """Both modes' settings are set, so a value taken from the wrong mode shows up."""
    return IngestSettings(
        run_mode=run_mode,
        database_url=SecretStr(DATABASE_URL),
        ollama_base_url=OLLAMA_BASE_URL,
        openrouter_api_key=SecretStr(OPENROUTER_KEY),
        embedding_provider=EMBEDDING_PROVIDER,
        local_embedding_model=LOCAL_EMBEDDING_MODEL,
        cloud_embedding_model=CLOUD_EMBEDDING_MODEL,
        embedding_dim=FAKE_DIMENSION,
        chunk_max_chars=CHUNK_MAX_CHARS,
        chunk_overlap_chars=0,
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option, unknown to type checkers
    )


@pytest.mark.parametrize(
    ("run_mode", "embedding_client", "model", "provider_routing", "embedding_identity"),
    [
        pytest.param(
            RunMode.LOCAL,
            (f"{OLLAMA_BASE_URL}/v1", "ollama"),
            LOCAL_EMBEDDING_MODEL,
            None,
            LOCAL_EMBEDDING_MODEL,
            id="local",
        ),
        pytest.param(
            RunMode.CLOUD,
            (OPENROUTER_BASE_URL, OPENROUTER_KEY),
            CLOUD_EMBEDDING_MODEL,
            PINNED_ROUTING,
            "qwen/qwen3-embedding-8b@deepinfra",
            id="cloud",
        ),
    ],
)
async def test_documents_are_embedded_with_the_model_and_endpoint_of_the_mode(
    providers: FakeIngestProviders,
    fixture_docs: Path,
    run_mode: RunMode,
    embedding_client: tuple[object, ...],
    model: str,
    provider_routing: dict[str, Any] | None,
    embedding_identity: str,
) -> None:
    async with ingestion_pipeline(ingest_settings(run_mode)) as pipeline:
        await pipeline.run(fixture_docs)

    assert providers.embedding_clients.calls == [embedding_client]
    requests = providers.embeddings_api.payloads
    assert requests
    assert all(request["model"] == model for request in requests)
    assert all(request["dimensions"] == FAKE_DIMENSION for request in requests)
    assert all(request.get("provider") == provider_routing for request in requests)
    stored = providers.store.documents.values()
    assert {document.embedding_identity for document in stored} == {embedding_identity}


async def test_store_gets_one_connection_and_the_dimension(
    providers: FakeIngestProviders, fixture_docs: Path
) -> None:
    async with ingestion_pipeline(ingest_settings(RunMode.LOCAL)) as pipeline:
        await pipeline.run(fixture_docs)

    assert providers.connector.connections == [
        StoreConnection(DATABASE_URL, FAKE_DIMENSION, INGEST_CONNECTIONS)
    ]


async def test_chunking_settings_reach_the_pipeline(
    providers: FakeIngestProviders, fixture_docs: Path
) -> None:
    async with ingestion_pipeline(ingest_settings(RunMode.LOCAL)) as pipeline:
        await pipeline.run(fixture_docs)

    chunks = [chunk for stored in providers.store.documents.values() for chunk in stored.chunks]
    assert chunks
    assert all(len(chunk.text) <= CHUNK_MAX_CHARS for chunk in chunks)


async def test_embedding_client_is_closed_on_exit(
    providers: FakeIngestProviders, fixture_docs: Path
) -> None:
    async with ingestion_pipeline(ingest_settings(RunMode.LOCAL)) as pipeline:
        await pipeline.run(fixture_docs)

    [client] = providers.embedding_clients.clients
    assert client.is_closed()
