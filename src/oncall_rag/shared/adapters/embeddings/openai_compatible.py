"""Embeddings through an OpenAI-compatible API (Ollama's `/v1/embeddings`)."""

from collections.abc import Sequence
from typing import Final

import openai

from oncall_rag.shared.errors import ProviderResponseError
from oncall_rag.shared.ports import Embedder

OPENAI_API_PATH = "/v1"
# Ollama ignores the key, but the SDK requires one.
PLACEHOLDER_API_KEY = "ollama"
# Generous read timeout: the first request loads the model, and CPU-only hosts are slow.
TIMEOUT = openai.Timeout(120.0, connect=5.0)
# The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx: three attempts in total.
MAX_RETRIES = 2
DEFAULT_BATCH_SIZE = 32
# Ollama's float output equals its native /api/embed vectors; the SDK default (base64) does not.
ENCODING_FORMAT: Final = "float"

# Qwen3-Embedding is instruction-aware: queries get an instruction prefix, documents do not.
QWEN3_MODEL_PREFIX = "qwen3-embedding"
QWEN3_QUERY_INSTRUCTION = (
    "Given an on-call engineer's question, retrieve runbook passages that answer it"
)


def build_embedding_client(base_url: str) -> openai.AsyncOpenAI:
    api_url = f"{base_url}{OPENAI_API_PATH}"
    return openai.AsyncOpenAI(
        base_url=api_url, api_key=PLACEHOLDER_API_KEY, timeout=TIMEOUT, max_retries=MAX_RETRIES
    )


class OpenAICompatibleEmbedder(Embedder):
    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        dimension: int,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._client = client
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size

    @property
    def model_name(self) -> str:
        return self._model

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
        if self._model.startswith(QWEN3_MODEL_PREFIX):
            return f"Instruct: {QWEN3_QUERY_INSTRUCTION}\nQuery:{text}"
        return text

    async def _embed(self, inputs: Sequence[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self._model, input=list(inputs), encoding_format=ENCODING_FORMAT
        )
        vectors = [item.embedding for item in response.data]
        for vector in vectors:
            self._check_dimension(vector)
        return vectors

    def _check_dimension(self, vector: list[float]) -> None:
        actual = len(vector)
        if actual != self._dimension:
            raise ProviderResponseError(
                f"Model '{self._model}' returned {actual}-dimensional vectors, "
                f"but EMBEDDING_DIM is {self._dimension}"
            )
