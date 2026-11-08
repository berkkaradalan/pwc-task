import logging
import uuid
from collections.abc import Callable

import pytest
from asgi_correlation_id import CorrelationIdFilter
from fastapi.testclient import TestClient

from oncall_rag.query.core.middleware import REQUEST_ID_HEADER, access_logger


@pytest.fixture
def access_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Access log records, with the request ID filter the API's log handler also has."""
    caplog.handler.addFilter(CorrelationIdFilter())
    caplog.set_level(logging.INFO, logger=access_logger.name)
    return caplog


def access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == access_logger.name]


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_an_incoming_request_id_is_echoed_back(client: TestClient) -> None:
    request_id = str(uuid.uuid4())

    response = client.get("/health", headers={REQUEST_ID_HEADER: request_id})

    assert response.headers[REQUEST_ID_HEADER] == request_id


def test_access_log_line_names_method_path_status_and_request_id(
    client: TestClient, access_log: pytest.LogCaptureFixture
) -> None:
    response = client.get("/health")

    [record] = access_records(access_log)
    assert record.getMessage().startswith("GET /health 200 ")
    assert record.getMessage().endswith(" ms")
    assert record.correlation_id == response.headers[REQUEST_ID_HEADER]  # type: ignore[attr-defined]  # added by CorrelationIdFilter


def test_unexpected_error_is_logged_as_500_with_its_request_id(
    failing_client: Callable[[Exception], TestClient], access_log: pytest.LogCaptureFixture
) -> None:
    client = failing_client(RuntimeError("secret internal detail"))

    response = client.post("/ask", json={"question": "anything"})

    [record] = access_records(access_log)
    assert record.getMessage().startswith("POST /ask 500 ")
    assert record.correlation_id == response.json()["request_id"]  # type: ignore[attr-defined]  # added by CorrelationIdFilter
