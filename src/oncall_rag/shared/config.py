"""Application settings, loaded from environment variables and an optional `.env` file."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_API_PATH = "/v1"
# Ollama ignores the key, but the OpenAI SDK requires one.
OLLAMA_API_KEY = SecretStr("ollama")


class RunMode(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    """An OpenAI-compatible API, the model to call on it, and the OpenRouter provider to pin."""

    base_url: str
    api_key: SecretStr
    model: str
    provider: str | None = None


class CommonSettings(BaseSettings):
    """Settings every service needs: the index database and the embedding model."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors are logged; input values such as DATABASE_URL must not be.
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    ollama_base_url: str

    run_mode: RunMode = RunMode.LOCAL
    openrouter_api_key: SecretStr | None = None
    embedding_provider: str | None = None

    local_embedding_model: str
    cloud_embedding_model: str
    embedding_dim: int = Field(default=1024, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)

    @model_validator(mode="after")
    def _require_cloud_settings(self) -> Self:
        if self.run_mode is RunMode.LOCAL:
            return self
        if self.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is required when RUN_MODE is cloud")
        if self.embedding_provider is None:
            raise ValueError("EMBEDDING_PROVIDER is required when RUN_MODE is cloud")
        return self

    @property
    def embedding_endpoint(self) -> ModelEndpoint:
        # Pinned: providers serve different weights (bf16, fp8), and vectors must not mix.
        return self._endpoint(
            self.local_embedding_model, self.cloud_embedding_model, self.embedding_provider
        )

    def _endpoint(
        self, local_model: str, cloud_model: str, cloud_provider: str | None = None
    ) -> ModelEndpoint:
        """The only place that turns RUN_MODE into a provider."""
        if self.run_mode is RunMode.LOCAL:
            ollama_api_url = f"{self.ollama_base_url}{OLLAMA_API_PATH}"
            return ModelEndpoint(ollama_api_url, OLLAMA_API_KEY, local_model)
        assert self.openrouter_api_key is not None  # narrows the type: the validator requires it
        return ModelEndpoint(
            OPENROUTER_BASE_URL, self.openrouter_api_key, cloud_model, cloud_provider
        )


class IngestSettings(CommonSettings):
    chunk_max_chars: int = Field(default=1500, ge=100)
    chunk_overlap_chars: int = Field(default=200, ge=0)

    docs_dir: Path = Path("data/docs")

    @model_validator(mode="after")
    def _check_chunk_overlap(self) -> Self:
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            raise ValueError("CHUNK_OVERLAP_CHARS must be smaller than CHUNK_MAX_CHARS")
        return self


class QuerySettings(CommonSettings):
    local_llm_model: str
    cloud_llm_model: str
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_concurrency: int = Field(default=4, ge=1)
    # The whole /ask request, including the wait for a language model slot. A local follow-up
    # makes two language model calls of 30-50 s each on CPU.
    ask_deadline_seconds: float = Field(default=180, gt=0)

    retrieval_top_k: int = Field(default=4, ge=1)
    retrieval_min_similarity: float = Field(default=0.5, ge=-1, le=1)

    db_pool_max_size: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_ask_deadline(self) -> Self:
        # Otherwise the deadline always expires first, and the per-attempt timeout and its
        # retry never take effect.
        if self.ask_deadline_seconds <= self.llm_timeout_seconds:
            raise ValueError("ASK_DEADLINE_SECONDS must be greater than LLM_TIMEOUT_SECONDS")
        return self

    @property
    def llm_endpoint(self) -> ModelEndpoint:
        return self._endpoint(self.local_llm_model, self.cloud_llm_model)
