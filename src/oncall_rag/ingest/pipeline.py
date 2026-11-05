"""Ingestion: load -> hash -> skip unchanged -> chunk -> embed -> replace; then prune."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from oncall_rag.ingest.chunker import ChunkingConfig, build_embedding_text, chunk_document
from oncall_rag.ingest.loaders import SkippedFile, load_documents
from oncall_rag.shared.models import Document
from oncall_rag.shared.ports import Embedder, VectorStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestionReport:
    indexed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def log_summary(self) -> None:
        logger.info(
            "Ingestion finished: indexed=%d unchanged=%d skipped=%d deleted=%d",
            len(self.indexed),
            len(self.unchanged),
            len(self.skipped),
            len(self.deleted),
        )
        # Skip reasons were already logged as warnings by the loader.
        for source in self.deleted:
            logger.info("  deleted %s", source)


class IngestionPipeline:
    def __init__(self, embedder: Embedder, store: VectorStore, chunking: ChunkingConfig) -> None:
        self._embedder = embedder
        self._store = store
        self._chunking = chunking

    async def run(self, docs_dir: Path, *, reindex: bool = False) -> IngestionReport:
        # Load first: a missing folder must fail before anything is reset or pruned.
        loaded = load_documents(docs_dir)
        await self._prepare_store(reindex=reindex)

        report = IngestionReport(skipped=list(loaded.skipped))
        await self._index_changed(loaded.documents, report)
        report.deleted = await self._prune_except(loaded.documents)

        report.log_summary()
        return report

    async def _prepare_store(self, *, reindex: bool) -> None:
        if reindex:
            logger.info("Re-index requested: dropping and recreating the index")
            await self._store.reset()
        else:
            await self._store.initialize()
        await self._store.ensure_compatible(self._embedder.model_name)

    async def _index_changed(self, documents: list[Document], report: IngestionReport) -> None:
        for document in documents:
            stored_hash = await self._store.get_content_hash(document.source)
            if stored_hash == document.content_hash:
                report.unchanged.append(document.source)
                continue
            await self._index(document)
            report.indexed.append(document.source)

    async def _prune_except(self, documents: list[Document]) -> list[str]:
        """Delete indexed documents not loaded in this run (removed from disk or now invalid)."""
        loaded_sources = {document.source for document in documents}
        stale = sorted(await self._store.list_sources() - loaded_sources)
        if stale:
            await self._store.delete_documents(stale)
        return stale

    async def _index(self, document: Document) -> None:
        chunks = chunk_document(document, self._chunking)
        texts = [build_embedding_text(document.title, chunk) for chunk in chunks]
        embeddings = await self._embedder.embed_documents(texts)
        await self._store.replace_document(document, chunks, embeddings, self._embedder.model_name)
        logger.info("Indexed %s (%d chunks)", document.source, len(chunks))
