"""Logging setup for command-line entry points."""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # httpx logs every request at INFO; keep the ingestion output readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)
