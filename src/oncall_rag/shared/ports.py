"""Interfaces the application depends on. Concrete providers live in `adapters/`."""

from collections.abc import Collection, Sequence
from typing import Protocol

from oncall_rag.shared.models import Chunk, Document


class Embedder(Protocol):
    @property
    def model_name(self) -> str:
        """Identifier stored with every vector so different models are never mixed."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for indexing. Returns one vector per input, in order."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query. May format the query differently from documents."""
        ...


class VectorStore(Protocol):
    async def initialize(self) -> None:
        """Create the schema if missing and check it matches the configuration."""
        ...

    async def reset(self) -> None:
        """Drop all indexed data and recreate the schema from the current configuration."""
        ...

    async def list_sources(self) -> set[str]: ...

    async def list_embedding_models(self) -> set[str]: ...

    async def get_content_hash(self, source: str) -> str | None:
        """Stored content hash for `source`, or None if it is not indexed."""
        ...

    async def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None:
        """Atomically upsert the document and replace all of its chunks."""
        ...

    async def delete_documents(self, sources: Collection[str]) -> None:
        """Delete documents together with their chunks."""
        ...
