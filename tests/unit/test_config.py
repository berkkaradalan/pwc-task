from pathlib import Path

import pytest

from oncall_rag.shared.config import load_settings
from oncall_rag.shared.errors import ConfigError

SECRET_URL = "postgresql://user:top-secret-password@db/test"


@pytest.fixture(autouse=True)
def required_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run outside the repository so a developer's .env is never read.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://ollama.test")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"CHUNK_MAX_CHARS": "1500", "CHUNK_OVERLAP_CHARS": "5000"},
            "CHUNK_OVERLAP_CHARS must be smaller than CHUNK_MAX_CHARS",
        ),
    ],
)
def test_invalid_settings_raise_config_error_without_input_values(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], expected: str
) -> None:
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError) as error:
        load_settings()

    message = str(error.value)
    assert message.startswith("Invalid configuration: ")
    assert expected in message
    assert "top-secret-password" not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_valid_settings_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_DIM", "768")

    settings = load_settings()

    assert settings.embedding_dim == 768
    assert settings.database_url.get_secret_value() == SECRET_URL
