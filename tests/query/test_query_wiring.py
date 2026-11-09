from typing import Any

import pytest
from pydantic import SecretStr

from oncall_rag.query.core.wiring import answer_service_and_search
from oncall_rag.shared.config import OPENROUTER_BASE_URL, QuerySettings, RunMode
from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import Document
from tests.fakes import FAKE_DIMENSION, FakeProviders, StoreConnection

DATABASE_URL = "postgresql://user:password@db/test"
OLLAMA_BASE_URL = "http://ollama.test"
OLLAMA_API_URL = f"{OLLAMA_BASE_URL}/v1"
OLLAMA_KEY = "ollama"
OPENROUTER_KEY = "test-openrouter-key"
EMBEDDING_PROVIDER = "deepinfra"
LOCAL_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
CLOUD_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
LOCAL_LLM_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
CLOUD_LLM_MODEL = "qwen/qwen3-30b-a3b-instruct-2507"
LLM_TIMEOUT_SECONDS = 7.0
ASK_DEADLINE_SECONDS = 8.0
DB_POOL_MAX_SIZE = 3
PINNED_ROUTING = {"order": [EMBEDDING_PROVIDER], "allow_fallbacks": False}
QUESTION = "How do I fix the backlog?"


def query_settings(
    run_mode: RunMode,
    *,
    llm_timeout_seconds: float = LLM_TIMEOUT_SECONDS,
    ask_deadline_seconds: float = ASK_DEADLINE_SECONDS,
) -> QuerySettings:
    """Both modes' settings are set, so a value taken from the wrong mode shows up."""
    return QuerySettings(
        run_mode=run_mode,
        database_url=SecretStr(DATABASE_URL),
        ollama_base_url=OLLAMA_BASE_URL,
        openrouter_api_key=SecretStr(OPENROUTER_KEY),
        embedding_provider=EMBEDDING_PROVIDER,
        local_embedding_model=LOCAL_EMBEDDING_MODEL,
        cloud_embedding_model=CLOUD_EMBEDDING_MODEL,
        local_llm_model=LOCAL_LLM_MODEL,
        cloud_llm_model=CLOUD_LLM_MODEL,
        embedding_dim=FAKE_DIMENSION,
        llm_timeout_seconds=llm_timeout_seconds,
        ask_deadline_seconds=ask_deadline_seconds,
        db_pool_max_size=DB_POOL_MAX_SIZE,
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option, unknown to type checkers
    )


def stored_document(source: str) -> Document:
    return Document(source=source, title="A", content="", content_hash="hash", metadata={})


@pytest.mark.parametrize(
    ("run_mode", "embedding_model", "llm_model", "provider_routing"),
    [
        pytest.param(RunMode.LOCAL, LOCAL_EMBEDDING_MODEL, LOCAL_LLM_MODEL, None, id="local"),
        pytest.param(
            RunMode.CLOUD, CLOUD_EMBEDDING_MODEL, CLOUD_LLM_MODEL, PINNED_ROUTING, id="cloud"
        ),
    ],
)
async def test_each_model_reaches_its_own_client(
    fake_providers: FakeProviders,
    run_mode: RunMode,
    embedding_model: str,
    llm_model: str,
    provider_routing: dict[str, Any] | None,
) -> None:
    async with answer_service_and_search(query_settings(run_mode)) as (answer_service, _):
        response = await answer_service.answer(QUESTION, [])

    assert response.answer == fake_providers.chat_api.answer
    [embedding_request] = fake_providers.embeddings_api.payloads
    assert embedding_request["model"] == embedding_model
    assert embedding_request["dimensions"] == FAKE_DIMENSION
    assert embedding_request.get("provider") == provider_routing
    [chat_request] = fake_providers.chat_api.payloads
    assert chat_request["model"] == llm_model
    assert "provider" not in chat_request


@pytest.mark.parametrize(
    ("run_mode", "embedding_client", "llm_client"),
    [
        pytest.param(
            RunMode.LOCAL,
            (OLLAMA_API_URL, OLLAMA_KEY),
            (OLLAMA_API_URL, OLLAMA_KEY, LLM_TIMEOUT_SECONDS),
            id="local",
        ),
        pytest.param(
            RunMode.CLOUD,
            (OPENROUTER_BASE_URL, OPENROUTER_KEY),
            (OPENROUTER_BASE_URL, OPENROUTER_KEY, LLM_TIMEOUT_SECONDS),
            id="cloud",
        ),
    ],
)
async def test_clients_get_the_endpoint_and_key_of_the_mode_and_the_llm_timeout(
    fake_providers: FakeProviders,
    run_mode: RunMode,
    embedding_client: tuple[object, ...],
    llm_client: tuple[object, ...],
) -> None:
    async with answer_service_and_search(query_settings(run_mode)):
        pass

    assert fake_providers.embedding_clients.calls == [embedding_client]
    assert fake_providers.llm_clients.calls == [llm_client]


async def test_store_gets_the_database_url_dimension_and_pool_size(
    fake_providers: FakeProviders,
) -> None:
    async with answer_service_and_search(query_settings(RunMode.LOCAL)):
        pass

    assert fake_providers.connector.connections == [
        StoreConnection(DATABASE_URL, FAKE_DIMENSION, DB_POOL_MAX_SIZE)
    ]


@pytest.mark.parametrize(
    ("run_mode", "embedding_identity"),
    [
        pytest.param(RunMode.LOCAL, LOCAL_EMBEDDING_MODEL, id="local"),
        pytest.param(RunMode.CLOUD, "qwen/qwen3-embedding-8b@deepinfra", id="cloud"),
    ],
)
async def test_index_is_checked_against_the_embedding_identity(
    fake_providers: FakeProviders, run_mode: RunMode, embedding_identity: str
) -> None:
    async with answer_service_and_search(query_settings(run_mode)):
        pass

    assert fake_providers.store.compatibility_checks == [embedding_identity]


async def test_refuses_to_start_on_an_index_with_another_identity_and_closes_the_clients(
    fake_providers: FakeProviders,
) -> None:
    await fake_providers.store.replace_document(stored_document("a.md"), [], [], "other-model")

    with pytest.raises(ReindexRequiredError, match="other-model"):
        async with answer_service_and_search(query_settings(RunMode.LOCAL)):
            pass

    clients = [*fake_providers.embedding_clients.clients, *fake_providers.llm_clients.clients]
    assert len(clients) == 2
    assert all(client.is_closed() for client in clients)


async def test_every_client_is_closed_on_exit(fake_providers: FakeProviders) -> None:
    async with answer_service_and_search(query_settings(RunMode.LOCAL)):
        pass

    clients = [*fake_providers.embedding_clients.clients, *fake_providers.llm_clients.clients]
    assert len(clients) == 2
    assert all(client.is_closed() for client in clients)


async def test_the_ask_deadline_reaches_the_answer_service(fake_providers: FakeProviders) -> None:
    # The fake client builder ignores the LLM timeout, so only the deadline can end the call.
    settings = query_settings(RunMode.LOCAL, llm_timeout_seconds=0.1, ask_deadline_seconds=0.2)
    fake_providers.chat_api.delay_seconds = 30

    async with answer_service_and_search(settings) as (answer_service, _):
        with pytest.raises(TimeoutError, match=r"the request deadline of 0\.2 s expired"):
            await answer_service.answer(QUESTION, [])
