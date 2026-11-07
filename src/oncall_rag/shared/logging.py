"""Logging setup and failure messages shared by the ingestion command and the API."""

import logging
from collections.abc import Sequence

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(log_format: str = LOG_FORMAT, filters: Sequence[logging.Filter] = ()) -> None:
    handler = logging.StreamHandler()
    for record_filter in filters:
        handler.addFilter(record_filter)
    logging.basicConfig(level=logging.INFO, format=log_format, handlers=[handler])
    # The openai SDK's HTTP client logs every request at INFO; keep the output readable.
    logging.getLogger("httpx2").setLevel(logging.WARNING)


def failure_summary(error: Exception) -> str:
    """The error text, after the call the adapters noted on it, e.g. "embedding call to <model>"."""
    # Exceptions without notes (database errors, a missing folder) have no `__notes__` attribute.
    notes: list[str] = getattr(error, "__notes__", [])
    return ": ".join([*notes, str(error)])
