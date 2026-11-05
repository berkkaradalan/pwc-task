import json

import httpx
import pytest

from oncall_rag.shared.adapters.embeddings.ollama import OllamaEmbedder
from oncall_rag.shared.errors import EmbedderUnavailableError, EmbeddingError

DIMENSION = 4


class FakeOllama:
    """httpx.MockTransport handler: scripted responses, records request payloads."""

    def __init__(self, failures: list[httpx.Response | Exception] | None = None, dimension: int = DIMENSION) -> None:
        self.failures = list(failures or [])
        self.dimension = dimension
        self.payloads: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        vectors = [[float(len(text))] * self.dimension for text in payload["input"]]
        return httpx.Response(200, json={"model": payload["model"], "embeddings": vectors})


def make_embedder(handler: FakeOllama, model: str = "qwen3-embedding:0.6b", batch_size: int = 2) -> OllamaEmbedder:
    client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaEmbedder(
        client, model=model, dimension=DIMENSION, batch_size=batch_size, backoff_seconds=0
    )


async def test_documents_are_embedded_in_batches_without_prefix() -> None:
    handler = FakeOllama()

    vectors = await make_embedder(handler).embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [p["input"] for p in handler.payloads] == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert all(p["model"] == "qwen3-embedding:0.6b" for p in handler.payloads)
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_qwen3_queries_get_an_instruction_prefix() -> None:
    handler = FakeOllama()

    await make_embedder(handler).embed_query("how do I roll back?")

    [text] = handler.payloads[0]["input"]
    assert text.startswith("Instruct: ")
    assert text.endswith("\nQuery:how do I roll back?")


async def test_other_models_get_the_plain_query() -> None:
    handler = FakeOllama()

    await make_embedder(handler, model="nomic-embed-text").embed_query("how do I roll back?")

    assert handler.payloads[0]["input"] == ["how do I roll back?"]


async def test_connection_errors_and_5xx_are_retried() -> None:
    handler = FakeOllama(
        failures=[httpx.ConnectError("refused"), httpx.Response(503, json={"error": "loading"})]
    )

    vectors = await make_embedder(handler).embed_documents(["a"])

    assert len(handler.payloads) == 3
    assert vectors == [[1.0] * DIMENSION]


async def test_gives_up_after_bounded_retries() -> None:
    handler = FakeOllama(failures=[httpx.ConnectError("refused")] * 5)

    with pytest.raises(EmbedderUnavailableError, match="after 3 attempts"):
        await make_embedder(handler).embed_documents(["a"])

    assert len(handler.payloads) == 3


async def test_client_errors_are_not_retried() -> None:
    handler = FakeOllama(failures=[httpx.Response(404, json={"error": "model not found"})])

    with pytest.raises(EmbeddingError, match="model not found"):
        await make_embedder(handler).embed_documents(["a"])

    assert len(handler.payloads) == 1


async def test_wrong_vector_dimension_is_rejected() -> None:
    handler = FakeOllama(dimension=DIMENSION + 1)

    with pytest.raises(EmbeddingError, match="EMBEDDING_DIM is 4"):
        await make_embedder(handler).embed_documents(["a"])
