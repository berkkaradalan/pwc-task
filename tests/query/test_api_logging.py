import logging
import re
from collections.abc import Callable, Iterator

import pytest
from asgi_correlation_id import correlation_id

from oncall_rag.query.core.middleware import UVICORN_LOGGER, configure_api_logging

APP_LOGGER = "oncall_rag.query.test"
TIMESTAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"


@pytest.fixture
def configure() -> Iterator[Callable[[], None]]:
    """Runs configure_api_logging as at API startup, then restores every logger it changed.

    At startup the root logger has no handlers. Under pytest it has pytest's capture handlers,
    and logging.basicConfig does nothing while it has any, so they are set aside for the call.
    """
    root = logging.getLogger()
    uvicorn_logger = logging.getLogger(UVICORN_LOGGER)
    httpx_logger = logging.getLogger("httpx2")
    saved_root_level = root.level
    saved_uvicorn = (list(uvicorn_logger.handlers), uvicorn_logger.propagate)
    saved_httpx_level = httpx_logger.level
    added: list[logging.Handler] = []

    def run() -> None:
        pytest_handlers = list(root.handlers)
        root.handlers.clear()
        configure_api_logging()
        added.extend(root.handlers)
        root.handlers.extend(pytest_handlers)

    yield run

    for handler in added:
        root.removeHandler(handler)
    root.setLevel(saved_root_level)
    uvicorn_logger.handlers[:], uvicorn_logger.propagate = saved_uvicorn
    httpx_logger.setLevel(saved_httpx_level)


def log_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().err.splitlines()


def test_a_line_has_time_level_request_id_logger_and_message(
    configure: Callable[[], None], capsys: pytest.CaptureFixture[str]
) -> None:
    configure()
    token = correlation_id.set("request-1")
    try:
        logging.getLogger(APP_LOGGER).warning("disk almost full")
    finally:
        correlation_id.reset(token)

    [line] = log_lines(capsys)
    assert re.fullmatch(rf"{TIMESTAMP} WARNING \[request-1\] {APP_LOGGER}: disk almost full", line)


def test_a_line_outside_a_request_has_a_placeholder_request_id(
    configure: Callable[[], None], capsys: pytest.CaptureFixture[str]
) -> None:
    configure()

    logging.getLogger(APP_LOGGER).info("started")

    [line] = log_lines(capsys)
    assert re.fullmatch(rf"{TIMESTAMP} INFO \[-\] {APP_LOGGER}: started", line)


def test_info_is_logged_and_debug_is_not(
    configure: Callable[[], None], capsys: pytest.CaptureFixture[str]
) -> None:
    configure()

    logging.getLogger(APP_LOGGER).debug("hidden")
    logging.getLogger(APP_LOGGER).info("shown")

    [line] = log_lines(capsys)
    assert line.endswith(": shown")


def test_uvicorn_lines_go_through_the_api_handler_with_the_request_id(
    configure: Callable[[], None], capsys: pytest.CaptureFixture[str]
) -> None:
    uvicorn_own_handler = logging.StreamHandler()
    logging.getLogger(UVICORN_LOGGER).addHandler(uvicorn_own_handler)

    configure()
    token = correlation_id.set("request-2")
    try:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    finally:
        correlation_id.reset(token)

    assert uvicorn_own_handler not in logging.getLogger(UVICORN_LOGGER).handlers
    [line] = log_lines(capsys)
    assert re.fullmatch(
        rf"{TIMESTAMP} ERROR \[request-2\] uvicorn.error: Exception in ASGI application", line
    )


def test_http_client_requests_are_not_logged(
    configure: Callable[[], None], capsys: pytest.CaptureFixture[str]
) -> None:
    configure()

    logging.getLogger("httpx2").info("HTTP Request: POST http://ollama.test/v1/embeddings")

    assert log_lines(capsys) == []
