"""Application settings, loaded from environment variables and an optional `.env` file."""

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from oncall_rag.shared.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: SecretStr
    embedding_base_url: str

    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dim: int = Field(default=1024, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)

    chunk_max_chars: int = Field(default=1500, ge=100)
    chunk_overlap_chars: int = Field(default=200, ge=0)

    docs_dir: Path = Path("data/docs")

    @model_validator(mode="after")
    def _check_chunk_overlap(self) -> Self:
        if self.chunk_overlap_chars >= self.chunk_max_chars:
            raise ValueError("CHUNK_OVERLAP_CHARS must be smaller than CHUNK_MAX_CHARS")
        return self


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as error:
        # `from None`: the original error embeds input values such as DATABASE_URL
        # credentials, so it must not surface in tracebacks either.
        raise ConfigError(_describe_validation_error(error)) from None


def _describe_validation_error(error: ValidationError) -> str:
    problems: list[str] = []
    for detail in error.errors(include_input=False, include_url=False):
        name = _setting_name(detail["loc"])
        message = detail["msg"]
        problems.append(f"{name}: {message}")
    return "Invalid configuration: " + "; ".join(problems)


def _setting_name(location: tuple[int | str, ...]) -> str:
    # Model-level validators report an empty location.
    if not location:
        return "settings"
    return ".".join(str(part) for part in location).upper()
