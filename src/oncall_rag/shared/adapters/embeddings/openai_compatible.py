"""Embeddings through an OpenAI-compatible API: Ollama locally, OpenRouter in the cloud."""

from collections.abc import Sequence
from typing import Final

import openai

from oncall_rag.shared.errors import ProviderResponseError
from oncall_rag.shared.ports import Embedder

# Generous read timeout: the first request loads the model, and CPU-only hosts are slow.
TIMEOUT = openai.Timeout(120.0, connect=5.0)
# The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx: three attempts in total.
MAX_RETRIES = 2
# Ollama's float output equals its native /api/embed vectors; the SDK default (base64) does not.
ENCODING_FORMAT: Final = "float"
# Joins model and provider in the identity stored with every vector, e.g. "model@provider".
PROVIDER_SEPARATOR = "@"

# Qwen3-Embedding is instruction-aware: queries get an instruction prefix, documents do not.
# The name is part of both the Ollama tag and the OpenRouter id.
QWEN3_MODEL_NAME = "qwen3-embedding"
QWEN3_QUERY_INSTRUCTION = (
    "Given an on-call engineer's question, retrieve runbook passages that answer it"
)


def build_embedding_client(base_url: str, api_key: str) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=MAX_RETRIES
    )


class OpenAICompatibleEmbedder(Embedder):
    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        provider: str | None,
        dimension: int,
        batch_size: int,
    ) -> None:
        self._client = client
        self._model = model
        self._provider = provider
        self._dimension = dimension
        self._batch_size = batch_size

    @property
    def embedding_identity(self) -> str:
        if self._provider is None:
            return self._model
        return f"{self._model}{PROVIDER_SEPARATOR}{self._provider}"

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self._embed([self._format_query(text)])
        return vector

    def _format_query(self, text: str) -> str:
        if QWEN3_MODEL_NAME in self._model:
            return f"Instruct: {QWEN3_QUERY_INSTRUCTION}\nQuery:{text}"
        return text

    async def _embed(self, inputs: Sequence[str]) -> list[list[float]]:
        # Qwen3-Embedding is trained to be truncated (the 8B model is 4096-dimensional), and both
        # providers return the first `dimensions` values, normalized again.
        response = await self._client.embeddings.create(
            model=self._model,
            input=list(inputs),
            encoding_format=ENCODING_FORMAT,
            dimensions=self._dimension,
            extra_body=self._provider_routing(),
        )
        vectors = [item.embedding for item in response.data]
        for vector in vectors:
            self._check_dimension(vector)
        return vectors

    def _provider_routing(self) -> dict[str, object] | None:
        """OpenRouter's routing field: only the pinned provider, failing instead of falling back."""
        if self._provider is None:
            return None
        return {"provider": {"order": [self._provider], "allow_fallbacks": False}}

    def _check_dimension(self, vector: list[float]) -> None:
        actual = len(vector)
        if actual != self._dimension:
            raise ProviderResponseError(
                f"Model '{self._model}' returned {actual}-dimensional vectors, "
                f"but EMBEDDING_DIM is {self._dimension}"
            )
