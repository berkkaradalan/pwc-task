"""The API's startup check against a real database. Only the SDK clients are built; no model
is called."""

import pytest
from pydantic import SecretStr

from oncall_rag.query.core.wiring import answer_service_and_search
from oncall_rag.shared.adapters.vectorstore.pgvector import PgVectorStore
from oncall_rag.shared.config import QuerySettings, RunMode
from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import Chunk, Document
from tests.integration.conftest import TEST_DIMENSION

pytestmark = pytest.mark.integration

LOCAL_EMBEDDING_MODEL = "local-embedding"


def query_settings(database_url: str) -> QuerySettings:
    return QuerySettings(
        run_mode=RunMode.LOCAL,
        database_url=SecretStr(database_url),
        ollama_base_url="http://ollama.test",
        local_embedding_model=LOCAL_EMBEDDING_MODEL,
        cloud_embedding_model="cloud-embedding",
        local_llm_model="local-llm",
        cloud_llm_model="cloud-llm",
        embedding_dim=TEST_DIMENSION,
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option, unknown to type checkers
    )


async def index_one_document(store: PgVectorStore, embedding_identity: str) -> None:
    document = Document(source="a.md", title="A", content="", content_hash="h", metadata={})
    chunk = Chunk(document_source="a.md", chunk_index=0, heading_path="", text="Restart.")
    await store.replace_document(document, [chunk], [[1.0, 0.0, 0.0]], embedding_identity)


async def test_api_refuses_to_start_before_the_index_is_built(database_url: str) -> None:
    with pytest.raises(ReindexRequiredError, match="has not been built yet"):
        async with answer_service_and_search(query_settings(database_url)):
            pass


async def test_api_refuses_to_start_on_an_index_with_another_identity(
    store: PgVectorStore, database_url: str
) -> None:
    await index_one_document(store, "other-model")

    with pytest.raises(ReindexRequiredError, match=r"\[other-model\].*'local-embedding'"):
        async with answer_service_and_search(query_settings(database_url)):
            pass


async def test_api_starts_on_an_index_with_its_identity(
    store: PgVectorStore, database_url: str
) -> None:
    await index_one_document(store, LOCAL_EMBEDDING_MODEL)

    async with answer_service_and_search(query_settings(database_url)) as (_, passage_search):
        await passage_search.ping()
