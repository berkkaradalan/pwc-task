"""Interfaces the application depends on. Concrete providers live in `adapters/`."""

from collections.abc import Collection, Sequence
from typing import Protocol

from oncall_rag.shared.models import Chunk, Document, Passage


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

    async def ping(self) -> None:
        """Run a trivial query. Raises VectorStoreUnavailableError if the database is down."""
        ...

    async def search(self, vector: Sequence[float], top_k: int) -> list[Passage]:
        """The `top_k` chunks closest to `vector`, most similar first."""
        ...

    async def list_sources(self) -> set[str]: ...

    async def ensure_compatible(self, embedding_model: str) -> None:
        """Raise EmbeddingModelMismatchError if the index holds vectors of another model."""
        ...

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


class LanguageModel(Protocol):
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """The model's answer to one system + user message pair."""
        ...
