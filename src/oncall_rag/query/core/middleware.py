"""Request tracing: a request ID on every response and log line, one access log line per
request, and a JSON body for unexpected errors."""

import logging
import time

from asgi_correlation_id import CorrelationIdFilter, CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from oncall_rag.shared.logging import configure_logging

REQUEST_ID_HEADER = "X-Request-ID"
API_LOG_FORMAT = "%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s"
# Shown on log lines outside a request, such as startup and shutdown.
NO_REQUEST_ID = "-"
UVICORN_LOGGER = "uvicorn"
MILLISECONDS_PER_SECOND = 1000

access_logger = logging.getLogger("oncall_rag.query.access")


def install_request_tracing(app: FastAPI) -> None:
    app.add_middleware(BaseHTTPMiddleware, dispatch=log_access)
    # Added last, so it runs first: the request ID is set before log_access runs.
    app.add_middleware(CorrelationIdMiddleware, header_name=REQUEST_ID_HEADER)
    app.add_exception_handler(Exception, internal_server_error)


def configure_api_logging() -> None:
    configure_logging(API_LOG_FORMAT, [CorrelationIdFilter(default_value=NO_REQUEST_ID)])
    # uvicorn's own handler has no request ID; route its lines, including the traceback of an
    # unexpected error, through the handler above instead.
    uvicorn_logger = logging.getLogger(UVICORN_LOGGER)
    uvicorn_logger.handlers.clear()
    uvicorn_logger.propagate = True


async def log_access(request: Request, call_next: RequestResponseEndpoint) -> Response:
    started = time.perf_counter()
    # If the route raises, the client gets the 500 from internal_server_error.
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
        access_logger.info(
            "%s %s %d %.0f ms", request.method, request.url.path, status_code, duration_ms
        )


async def internal_server_error(request: Request, error: Exception) -> JSONResponse:
    """Starlette re-raises the error afterwards; uvicorn logs the traceback with the same ID."""
    request_id = correlation_id.get()
    assert request_id is not None  # narrows the type: CorrelationIdMiddleware wraps every route
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error", "request_id": request_id},
        # Sent outside CorrelationIdMiddleware, which adds the header to every other response.
        headers={REQUEST_ID_HEADER: request_id},
    )
