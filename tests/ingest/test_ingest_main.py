import logging

import pytest
from pydantic import SecretStr

from oncall_rag.ingest import __main__ as ingest_main
from oncall_rag.ingest.__main__ import ExitCode
from oncall_rag.shared.config import IngestSettings
from oncall_rag.shared.errors import (
    ConfigError,
    EmbedderUnavailableError,
    EmbeddingModelMismatchError,
    VectorStoreUnavailableError,
)


def valid_settings(settings_class: type[IngestSettings]) -> IngestSettings:
    return settings_class(
        database_url=SecretStr("postgresql://user:password@db/test"),
        embedding_base_url="http://ollama.test",
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings init option, unknown to type checkers
    )


class FakeRunIngestion:
    """Stands in for run_ingestion; records the call and optionally raises."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[IngestSettings, bool]] = []

    async def __call__(self, settings: IngestSettings, *, reindex: bool) -> None:
        self.calls.append((settings, reindex))
        if self.error is not None:
            raise self.error


@pytest.fixture
def run_ingestion(monkeypatch: pytest.MonkeyPatch) -> FakeRunIngestion:
    fake = FakeRunIngestion()
    monkeypatch.setattr(ingest_main, "run_ingestion", fake)
    monkeypatch.setattr(ingest_main, "load_settings", valid_settings)
    return fake


def test_success_exits_with_0(run_ingestion: FakeRunIngestion) -> None:
    assert ingest_main.main([]) == ExitCode.OK == 0
    [(_, reindex)] = run_ingestion.calls
    assert reindex is False


def test_reindex_flag_is_passed_through(run_ingestion: FakeRunIngestion) -> None:
    assert ingest_main.main(["--reindex"]) == ExitCode.OK
    [(_, reindex)] = run_ingestion.calls
    assert reindex is True


def test_config_error_exits_with_2_without_running_ingestion(
    run_ingestion: FakeRunIngestion,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_settings(settings_class: type[IngestSettings]) -> IngestSettings:
        raise ConfigError("Invalid configuration: DATABASE_URL: Field required")

    monkeypatch.setattr(ingest_main, "load_settings", broken_settings)

    with caplog.at_level(logging.ERROR):
        assert ingest_main.main([]) == ExitCode.CONFIG_ERROR == 2

    assert run_ingestion.calls == []
    assert "DATABASE_URL: Field required" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        EmbedderUnavailableError("Ollama is unavailable"),
        VectorStoreUnavailableError("Cannot connect to PostgreSQL"),
    ],
)
def test_ingestion_error_exits_with_1(
    run_ingestion: FakeRunIngestion, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    run_ingestion.error = error

    with caplog.at_level(logging.ERROR):
        assert ingest_main.main([]) == ExitCode.FAILURE == 1

    assert f"Ingestion failed: {error}" in caplog.text


def test_reindex_required_error_exits_with_1_and_suggests_reindex(
    run_ingestion: FakeRunIngestion, caplog: pytest.LogCaptureFixture
) -> None:
    run_ingestion.error = EmbeddingModelMismatchError({"old-model"}, "new-model")

    with caplog.at_level(logging.ERROR):
        assert ingest_main.main([]) == ExitCode.FAILURE

    assert "--reindex" in caplog.text
