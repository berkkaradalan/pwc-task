"""Ollama embedding adapter (`POST /api/embed`)."""

import logging
from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from oncall_rag.shared.errors import EmbedderUnavailableError, EmbeddingError

logger = logging.getLogger(__name__)

EMBED_PATH = "/api/embed"
# Generous read timeout: the first request loads the model, and CPU-only hosts are slow.
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5
MAX_ERROR_DETAIL_CHARS = 300

# Qwen3-Embedding is instruction-aware: queries get an instruction prefix, documents do not.
QWEN3_MODEL_PREFIX = "qwen3-embedding"
QWEN3_QUERY_INSTRUCTION = (
    "Given an on-call engineer's question, retrieve runbook passages that answer it"
)


class _ServerError(Exception):
    """A 5xx response, raised only so that it is retried."""


_RETRYABLE_ERRORS = (
    httpx.NetworkError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
    _ServerError,
)


class _EmbedResponse(BaseModel):
    embeddings: list[list[float]]


def build_http_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=DEFAULT_TIMEOUT)


class OllamaEmbedder:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        model: str,
        dimension: int,
        batch_size: int,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._backoff_seconds = backoff_seconds

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
        response = await self._post({"model": self._model, "input": list(inputs)})
        if response.is_error:
            detail = _error_detail(response)
            raise EmbeddingError(
                f"Ollama rejected the embedding request (HTTP {response.status_code}): "
                f"{detail}. Is '{self._model}' pulled on the Ollama server?"
            )
        return self._parse_embeddings(response, expected_count=len(inputs))

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        """POST with bounded retries for connection errors and 5xx responses."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=wait_exponential(multiplier=self._backoff_seconds),
            retry=retry_if_exception_type(_RETRYABLE_ERRORS),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        base_url = self._client.base_url
        try:
            return await retrying(self._post_once, payload)
        except RetryError as error:
            cause = error.last_attempt.exception()
            raise EmbedderUnavailableError(
                f"Ollama at {base_url} is unavailable after {MAX_ATTEMPTS} attempts: {cause!r}"
            ) from cause
        except httpx.HTTPError as error:
            raise EmbedderUnavailableError(f"Ollama request to {base_url} failed: {error!r}") from error

    async def _post_once(self, payload: dict[str, object]) -> httpx.Response:
        response = await self._client.post(EMBED_PATH, json=payload)
        if response.is_server_error:
            detail = _error_detail(response)
            raise _ServerError(f"HTTP {response.status_code}: {detail}")
        return response

    def _parse_embeddings(self, response: httpx.Response, expected_count: int) -> list[list[float]]:
        try:
            embeddings = _EmbedResponse.model_validate_json(response.content).embeddings
        except ValidationError as error:
            raise EmbeddingError("Ollama returned a response without valid 'embeddings'") from error
        returned_count = len(embeddings)
        if returned_count != expected_count:
            raise EmbeddingError(
                f"Ollama returned {returned_count} embeddings for {expected_count} inputs"
            )
        for vector in embeddings:
            self._check_dimension(vector)
        return embeddings

    def _check_dimension(self, vector: list[float]) -> None:
        actual = len(vector)
        if actual != self._dimension:
            raise EmbeddingError(
                f"Model '{self._model}' returned {actual}-dimensional vectors, "
                f"but EMBEDDING_DIM is {self._dimension}"
            )


def _error_detail(response: httpx.Response) -> str:
    return response.text[:MAX_ERROR_DETAIL_CHARS]
