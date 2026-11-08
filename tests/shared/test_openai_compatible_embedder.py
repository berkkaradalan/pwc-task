import openai
import pytest

from oncall_rag.shared.adapters.embeddings.openai_compatible import (
    MAX_RETRIES,
    TIMEOUT,
    OpenAICompatibleEmbedder,
    build_embedding_client,
)
from oncall_rag.shared.errors import ProviderResponseError
from tests.fakes import FakeEmbeddingsApi

DIMENSION = 4


def make_embedder(
    handler: FakeEmbeddingsApi,
    model: str = "qwen3-embedding:0.6b",
    provider: str | None = None,
    batch_size: int = 2,
) -> OpenAICompatibleEmbedder:
    return OpenAICompatibleEmbedder(
        handler.client(), model=model, provider=provider, dimension=DIMENSION, batch_size=batch_size
    )


async def test_documents_are_embedded_in_batches_without_prefix() -> None:
    handler = FakeEmbeddingsApi(DIMENSION)

    vectors = await make_embedder(handler).embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [p["input"] for p in handler.payloads] == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert all(p["model"] == "qwen3-embedding:0.6b" for p in handler.payloads)
    assert all(p["encoding_format"] == "float" for p in handler.payloads)
    assert all(p["dimensions"] == DIMENSION for p in handler.payloads)
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


@pytest.mark.parametrize("model", ["qwen3-embedding:0.6b", "qwen/qwen3-embedding-8b"])
async def test_qwen3_queries_get_an_instruction_prefix(model: str) -> None:
    handler = FakeEmbeddingsApi(DIMENSION)

    await make_embedder(handler, model=model).embed_query("how do I roll back?")

    [text] = handler.payloads[0]["input"]
    assert text.startswith("Instruct: ")
    assert text.endswith("\nQuery:how do I roll back?")


async def test_other_models_get_the_plain_query() -> None:
    handler = FakeEmbeddingsApi(DIMENSION)

    await make_embedder(handler, model="nomic-embed-text").embed_query("how do I roll back?")

    assert handler.payloads[0]["input"] == ["how do I roll back?"]


async def test_wrong_vector_dimension_is_rejected() -> None:
    handler = FakeEmbeddingsApi(DIMENSION + 1)

    with pytest.raises(ProviderResponseError, match="EMBEDDING_DIM is 4"):
        await make_embedder(handler).embed_documents(["a"])


async def test_pinned_provider_is_sent_without_fallbacks() -> None:
    handler = FakeEmbeddingsApi(DIMENSION)

    await make_embedder(handler, provider="deepinfra").embed_query("how do I roll back?")

    assert handler.payloads[0]["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}


async def test_without_a_provider_no_routing_field_is_sent() -> None:
    handler = FakeEmbeddingsApi(DIMENSION)

    await make_embedder(handler).embed_query("how do I roll back?")

    assert "provider" not in handler.payloads[0]


@pytest.mark.parametrize(
    ("handler", "error_type"),
    [
        pytest.param(FakeEmbeddingsApi(DIMENSION, status_code=404), openai.NotFoundError, id="sdk"),
        pytest.param(FakeEmbeddingsApi(DIMENSION + 1), ProviderResponseError, id="unusable"),
    ],
)
async def test_errors_propagate_unchanged_with_a_note_naming_the_call(
    handler: FakeEmbeddingsApi, error_type: type[Exception]
) -> None:
    embedder = make_embedder(handler, model="qwen/qwen3-embedding-8b", provider="deepinfra")

    with pytest.raises(error_type) as error:
        await embedder.embed_query("how do I roll back?")

    assert error.value.__notes__ == ["embedding call to qwen/qwen3-embedding-8b@deepinfra"]


async def test_client_gets_the_embedding_timeout_and_retries() -> None:
    async with build_embedding_client("http://embeddings.test/v1", "test-key") as client:
        assert client.timeout == TIMEOUT
        assert client.max_retries == MAX_RETRIES
