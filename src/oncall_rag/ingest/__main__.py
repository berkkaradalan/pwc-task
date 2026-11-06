"""Index the documents folder: `python -m oncall_rag.ingest [--reindex]`."""

import argparse
import asyncio
import logging
import sys
from enum import IntEnum

import openai
import psycopg
from pydantic import ValidationError

from oncall_rag.ingest.wiring import ingestion_pipeline
from oncall_rag.shared.config import IngestSettings
from oncall_rag.shared.errors import ProviderResponseError, ReindexRequiredError
from oncall_rag.shared.logging import configure_logging

logger = logging.getLogger("oncall_rag.ingest")

REINDEX_HINT = "Run `python -m oncall_rag.ingest --reindex` to rebuild the index."


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    CONFIG_ERROR = 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    try:
        settings = IngestSettings()  # type: ignore[call-arg]  # values come from the environment
        asyncio.run(run_ingestion(settings, reindex=args.reindex))
    except ValidationError as error:
        logger.error("Invalid configuration: %s", error)
        return ExitCode.CONFIG_ERROR
    except ReindexRequiredError as error:
        logger.error("Ingestion failed: %s %s", error, REINDEX_HINT)
        return ExitCode.FAILURE
    except (NotADirectoryError, ProviderResponseError, openai.OpenAIError, psycopg.Error) as error:
        logger.error("Ingestion failed: %s", error)
        return ExitCode.FAILURE
    return ExitCode.OK


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m oncall_rag.ingest",
        description="Load, chunk, embed and store the documents in DOCS_DIR.",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="drop and recreate the index (using EMBEDDING_DIM), then index every document",
    )
    return parser.parse_args(argv)


async def run_ingestion(settings: IngestSettings, *, reindex: bool) -> None:
    logger.info("Ingesting %s with embedding model %s", settings.docs_dir, settings.embedding_model)
    async with ingestion_pipeline(settings) as pipeline:
        await pipeline.run(settings.docs_dir, reindex=reindex)


if __name__ == "__main__":
    sys.exit(main())
