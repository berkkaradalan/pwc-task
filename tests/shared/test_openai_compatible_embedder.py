import json

import httpx2
import openai
import pytest

from oncall_rag.shared.adapters.embeddings.openai_compatible import OpenAICompatibleEmbedder
from oncall_rag.shared.errors import ProviderResponseError

DIMENSION = 4


class FakeEmbeddingsApi:
    """httpx2.MockTransport handler for `/v1/embeddings`; records request payloads."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        self.dimension = dimension
        self.payloads: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        data = [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(len(text))] * self.dimension,
            }
            for index, text in enumerate(payload["input"])
        ]
        usage = {"prompt_tokens": 0, "total_tokens": 0}
        return httpx2.Response(
            200, json={"object": "list", "data": data, "model": payload["model"], "usage": usage}
        )


def make_embedder(
    handler: FakeEmbeddingsApi, model: str = "qwen3-embedding:0.6b", batch_size: int = 2
) -> OpenAICompatibleEmbedder:
    client = openai.AsyncOpenAI(
        base_url="http://ollama.test/v1",
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAICompatibleEmbedder(client, model=model, dimension=DIMENSION, batch_size=batch_size)


async def test_documents_are_embedded_in_batches_without_prefix() -> None:
    handler = FakeEmbeddingsApi()

    vectors = await make_embedder(handler).embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [p["input"] for p in handler.payloads] == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert all(p["model"] == "qwen3-embedding:0.6b" for p in handler.payloads)
    assert all(p["encoding_format"] == "float" for p in handler.payloads)
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_qwen3_queries_get_an_instruction_prefix() -> None:
    handler = FakeEmbeddingsApi()

    await make_embedder(handler).embed_query("how do I roll back?")

    [text] = handler.payloads[0]["input"]
    assert text.startswith("Instruct: ")
    assert text.endswith("\nQuery:how do I roll back?")


async def test_other_models_get_the_plain_query() -> None:
    handler = FakeEmbeddingsApi()

    await make_embedder(handler, model="nomic-embed-text").embed_query("how do I roll back?")

    assert handler.payloads[0]["input"] == ["how do I roll back?"]


async def test_wrong_vector_dimension_is_rejected() -> None:
    handler = FakeEmbeddingsApi(dimension=DIMENSION + 1)

    with pytest.raises(ProviderResponseError, match="EMBEDDING_DIM is 4"):
        await make_embedder(handler).embed_documents(["a"])
