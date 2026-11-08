"""The ingestion pipeline on the real store: skipping unchanged files and pruning deleted ones
live in the pipeline, and depend on what the store returns."""

from pathlib import Path

import pytest

from oncall_rag.ingest.chunker import ChunkingConfig
from oncall_rag.ingest.pipeline import IngestionPipeline
from oncall_rag.shared.adapters.vectorstore.pgvector import connect_pgvector_store
from tests.fakes import FAKE_DIMENSION, FakeEmbedder
from tests.integration.conftest import TEST_POOL_SIZE

pytestmark = pytest.mark.integration

VALID_SOURCES = {"no-front-matter.md", "notes/database-failover.txt", "payments-outage.md"}
CHUNKING = ChunkingConfig(max_chars=500, overlap_chars=50)


async def test_a_second_run_skips_unchanged_files_and_prunes_deleted_ones(
    database_url: str, docs_dir: Path
) -> None:
    embedder = FakeEmbedder()
    async with connect_pgvector_store(
        database_url, dimension=FAKE_DIMENSION, pool_max_size=TEST_POOL_SIZE
    ) as store:
        pipeline = IngestionPipeline(embedder=embedder, store=store, chunking=CHUNKING)
        first = await pipeline.run(docs_dir)
        embedded_after_first_run = len(embedder.embedded_texts)
        (docs_dir / "payments-outage.md").unlink()

        second = await pipeline.run(docs_dir)

        assert set(first.indexed) == VALID_SOURCES
        assert second.indexed == []
        assert set(second.unchanged) == VALID_SOURCES - {"payments-outage.md"}
        assert second.deleted == ["payments-outage.md"]
        assert len(embedder.embedded_texts) == embedded_after_first_run
        assert await store.list_sources() == VALID_SOURCES - {"payments-outage.md"}
