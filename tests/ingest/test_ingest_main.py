import logging
from pathlib import Path

import httpx2
import openai
import psycopg
import pytest
from pydantic import SecretStr

from oncall_rag.ingest import __main__ as ingest_main
from oncall_rag.ingest.__main__ import ExitCode
from oncall_rag.shared.config import IngestSettings
from oncall_rag.shared.errors import ProviderResponseError, ReindexRequiredError

EMBEDDING_REQUEST = httpx2.Request("POST", "http://ollama.test/v1/embeddings")


def valid_settings() -> IngestSettings:
    return IngestSettings(
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
    monkeypatch.setattr(ingest_main, "IngestSettings", valid_settings)
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
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The real settings class, outside the repository so a developer's .env is never read.
    monkeypatch.setattr(ingest_main, "IngestSettings", IngestSettings)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://ollama.test")

    with caplog.at_level(logging.ERROR):
        assert ingest_main.main([]) == ExitCode.CONFIG_ERROR == 2

    assert run_ingestion.calls == []
    assert "database_url" in caplog.text
    assert "Field required" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(openai.APIConnectionError(request=EMBEDDING_REQUEST), id="embedder-down"),
        pytest.param(psycopg.OperationalError("connection refused"), id="database-down"),
        pytest.param(ProviderResponseError("wrong vector length"), id="unusable-response"),
        pytest.param(NotADirectoryError("Documents folder not found"), id="missing-docs"),
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
    run_ingestion.error = ReindexRequiredError("Index models [old-model], configured new-model")

    with caplog.at_level(logging.ERROR):
        assert ingest_main.main([]) == ExitCode.FAILURE

    assert "--reindex" in caplog.text
