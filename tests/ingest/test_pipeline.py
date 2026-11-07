import logging
from pathlib import Path

import pytest

from oncall_rag.ingest.chunker import ChunkingConfig
from oncall_rag.ingest.pipeline import IngestionPipeline
from oncall_rag.shared.adapters.embeddings.openai_compatible import OpenAICompatibleEmbedder
from oncall_rag.shared.ports import Embedder
from tests.fakes import FakeEmbedder, FakeEmbeddingsApi, InMemoryVectorStore

VALID_SOURCES = ["no-front-matter.md", "notes/database-failover.txt", "payments-outage.md"]
CHUNKING = ChunkingConfig(max_chars=500, overlap_chars=50)
PIPELINE_LOGGER = "oncall_rag.ingest.pipeline"
API_DIMENSION = 4


def make_pipeline(embedder: Embedder, store: InMemoryVectorStore) -> IngestionPipeline:
    return IngestionPipeline(embedder=embedder, store=store, chunking=CHUNKING)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def pipeline(embedder: FakeEmbedder, store: InMemoryVectorStore) -> IngestionPipeline:
    return make_pipeline(embedder, store)


async def test_first_run_indexes_all_valid_documents(
    docs_dir: Path, pipeline: IngestionPipeline, embedder: FakeEmbedder, store: InMemoryVectorStore
) -> None:

    report = await pipeline.run(docs_dir)

    assert report.indexed == VALID_SOURCES
    assert report.unchanged == []
    assert [s.source for s in report.skipped] == [
        "bad-front-matter.md",
        "diagram.svg",
        "empty.md",
        "invalid-utf8.md",
    ]
    assert report.deleted == []
    assert sorted(store.documents) == VALID_SOURCES
    assert store.initialize_calls == 1

    stored = store.documents["payments-outage.md"]
    assert stored.embedding_model == embedder.model_name
    assert len(stored.chunks) == len(stored.embeddings) > 1
    # The embedder gets context-prefixed text; the store keeps the raw chunk text.
    rollback = next(c for c in stored.chunks if c.heading_path.endswith("Rollback"))
    assert rollback.text == "Roll back to the previous release with the deploy tool."
    assert rollback.heading_path == "Payments API outage > Mitigation > Rollback"
    assert (
        "Payments API outage > Mitigation > Rollback\n\n" + rollback.text in embedder.embedded_texts
    )


async def test_second_run_does_no_embedding_work(
    docs_dir: Path, pipeline: IngestionPipeline, embedder: FakeEmbedder, store: InMemoryVectorStore
) -> None:
    await pipeline.run(docs_dir)
    calls_after_first_run = len(embedder.document_calls)
    snapshot = dict(store.documents)

    report = await pipeline.run(docs_dir)

    assert len(embedder.document_calls) == calls_after_first_run
    assert report.indexed == []
    assert report.unchanged == VALID_SOURCES
    assert report.deleted == []
    assert store.documents == snapshot


async def test_changed_file_is_reindexed(
    docs_dir: Path, pipeline: IngestionPipeline, embedder: FakeEmbedder, store: InMemoryVectorStore
) -> None:
    await pipeline.run(docs_dir)
    embedder.document_calls.clear()

    (docs_dir / "no-front-matter.md").write_text("# Cache warmup\n\nNew procedure text.\n")
    report = await pipeline.run(docs_dir)

    assert report.indexed == ["no-front-matter.md"]
    assert report.unchanged == ["notes/database-failover.txt", "payments-outage.md"]
    assert embedder.embedded_texts == ["no-front-matter > Cache warmup\n\nNew procedure text."]
    [chunk] = store.documents["no-front-matter.md"].chunks
    assert chunk.text == "New procedure text."


async def test_deleted_file_is_pruned(
    docs_dir: Path, pipeline: IngestionPipeline, store: InMemoryVectorStore
) -> None:
    await pipeline.run(docs_dir)

    (docs_dir / "notes" / "database-failover.txt").unlink()
    report = await pipeline.run(docs_dir)

    assert report.deleted == ["notes/database-failover.txt"]
    assert "notes/database-failover.txt" not in store.documents


async def test_previously_indexed_file_that_becomes_invalid_is_pruned(
    docs_dir: Path, pipeline: IngestionPipeline, store: InMemoryVectorStore
) -> None:
    await pipeline.run(docs_dir)

    (docs_dir / "payments-outage.md").write_bytes(b"\xff\xfe broken")
    report = await pipeline.run(docs_dir)

    assert "payments-outage.md" in {s.source for s in report.skipped}
    assert report.deleted == ["payments-outage.md"]
    assert "payments-outage.md" not in store.documents


async def test_embedding_model_change_rebuilds_the_index_with_one_warning(
    docs_dir: Path, store: InMemoryVectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    first = make_pipeline(FakeEmbedder(model_name="old-model"), store)
    await first.run(docs_dir)

    second = make_pipeline(FakeEmbedder(model_name="new-model"), store)
    with caplog.at_level(logging.WARNING, logger=PIPELINE_LOGGER):
        report = await second.run(docs_dir)

    assert store.reset_calls == 1
    assert report.indexed == VALID_SOURCES
    assert await store.list_embedding_models() == {"new-model"}
    [warning] = [record for record in caplog.records if record.name == PIPELINE_LOGGER]
    assert "old-model" in warning.getMessage()
    assert "new-model" in warning.getMessage()


async def test_embedding_provider_change_rebuilds_the_index(
    docs_dir: Path, store: InMemoryVectorStore
) -> None:
    api = FakeEmbeddingsApi(API_DIMENSION)

    for provider in ["nebius", "deepinfra"]:
        embedder = OpenAICompatibleEmbedder(
            api.client(),
            model="qwen/qwen3-embedding-8b",
            provider=provider,
            dimension=API_DIMENSION,
            batch_size=8,
        )
        report = await make_pipeline(embedder, store).run(docs_dir)

    assert store.reset_calls == 1
    assert report.indexed == VALID_SOURCES
    assert await store.list_embedding_models() == {"qwen/qwen3-embedding-8b@deepinfra"}


async def test_same_embedding_model_does_not_rebuild(
    docs_dir: Path, pipeline: IngestionPipeline, store: InMemoryVectorStore
) -> None:
    await pipeline.run(docs_dir)

    report = await pipeline.run(docs_dir)

    assert store.reset_calls == 0
    assert report.unchanged == VALID_SOURCES


async def test_reindex_rebuilds_with_the_new_model(
    docs_dir: Path, store: InMemoryVectorStore
) -> None:
    first = make_pipeline(FakeEmbedder(model_name="old-model"), store)
    await first.run(docs_dir)

    second = make_pipeline(FakeEmbedder(model_name="new-model"), store)
    report = await second.run(docs_dir, reindex=True)

    assert store.reset_calls == 1
    assert report.indexed == VALID_SOURCES
    assert await store.list_embedding_models() == {"new-model"}


async def test_missing_docs_folder_fails_without_touching_the_index(
    tmp_path: Path, pipeline: IngestionPipeline, store: InMemoryVectorStore
) -> None:

    with pytest.raises(NotADirectoryError):
        await pipeline.run(tmp_path / "missing", reindex=True)

    assert store.reset_calls == 0
    assert store.initialize_calls == 0
