from pathlib import Path

import pytest
from pydantic import ValidationError

from oncall_rag.shared.config import (
    OPENROUTER_BASE_URL,
    IngestSettings,
    QuerySettings,
    RunMode,
)

SECRET_URL = "postgresql://user:top-secret-password@db/test"
SECRET_KEY = "test-openrouter-key"


@pytest.fixture(autouse=True)
def required_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run outside the repository so a developer's .env is never read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RUN_MODE", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test")
    monkeypatch.setenv("LOCAL_EMBEDDING_MODEL", "local-embedding")
    monkeypatch.setenv("CLOUD_EMBEDDING_MODEL", "cloud-embedding")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-llm")
    monkeypatch.setenv("CLOUD_LLM_MODEL", "cloud-llm")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"CHUNK_MAX_CHARS": "1500", "CHUNK_OVERLAP_CHARS": "5000"},
            "CHUNK_OVERLAP_CHARS must be smaller than CHUNK_MAX_CHARS",
        ),
        ({"EMBEDDING_DIM": "not-a-number"}, "embedding_dim"),
        ({"RUN_MODE": "cloud"}, "OPENROUTER_API_KEY is required when RUN_MODE is cloud"),
        (
            {"RUN_MODE": "cloud", "OPENROUTER_API_KEY": "not-a-number"},
            "EMBEDDING_PROVIDER is required when RUN_MODE is cloud",
        ),
    ],
)
def test_invalid_settings_raise_without_input_values(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], expected: str
) -> None:
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as error:
        IngestSettings()  # type: ignore[call-arg]  # values come from the environment

    message = str(error.value)
    assert expected in message
    assert "top-secret-password" not in message
    assert "not-a-number" not in message


def test_valid_settings_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_DIM", "768")

    settings = IngestSettings()  # type: ignore[call-arg]  # values come from the environment

    assert settings.embedding_dim == 768
    assert settings.database_url.get_secret_value() == SECRET_URL


def test_local_mode_is_the_default_and_uses_ollama() -> None:
    settings = QuerySettings()  # type: ignore[call-arg]  # values come from the environment

    assert settings.run_mode is RunMode.LOCAL
    for endpoint, model in [
        (settings.embedding_endpoint, "local-embedding"),
        (settings.llm_endpoint, "local-llm"),
    ]:
        assert endpoint.base_url == "http://ollama.test/v1"
        assert endpoint.model == model
        assert endpoint.provider is None


def test_cloud_mode_uses_openrouter_with_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_MODE", "cloud")
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET_KEY)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "deepinfra")

    settings = QuerySettings()  # type: ignore[call-arg]  # values come from the environment

    for endpoint, model in [
        (settings.embedding_endpoint, "cloud-embedding"),
        (settings.llm_endpoint, "cloud-llm"),
    ]:
        assert endpoint.base_url == OPENROUTER_BASE_URL
        assert endpoint.api_key.get_secret_value() == SECRET_KEY
        assert endpoint.model == model
    assert settings.embedding_endpoint.provider == "deepinfra"
    assert settings.llm_endpoint.provider is None
