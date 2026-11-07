"""Interfaces the application depends on. Concrete providers live in `adapters/`."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence

from oncall_rag.shared.models import ChatMessage, Chunk, Document, SearchCandidates


class Embedder(ABC):
    """Implemented by OpenAICompatibleEmbedder; FakeEmbedder in tests."""

    @property
    @abstractmethod
    def embedding_identity(self) -> str:
        """The model, plus the provider when one is pinned (`model@provider`).

        Stored with every vector, so vectors from different weights are never mixed.
        """

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for indexing. Returns one vector per input, in order."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query. May format the query differently from documents."""


class DocumentIndex(ABC):
    """Implemented by PgVectorStore; InMemoryVectorStore in tests."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create the schema if it is missing."""

    @abstractmethod
    async def reset(self) -> None:
        """Drop all indexed data and recreate the schema from the current configuration."""

    @abstractmethod
    async def ensure_compatible(self, embedding_identity: str) -> None:
        """Raise ReindexRequiredError unless the index exists with this identity and dimension."""

    @abstractmethod
    async def list_sources(self) -> set[str]:
        """Sources of all indexed documents."""

    @abstractmethod
    async def list_embedding_identities(self) -> set[str]:
        """Embedding identities of all indexed documents."""

    @abstractmethod
    async def get_content_hash(self, source: str) -> str | None:
        """Stored content hash for `source`, or None if it is not indexed."""

    @abstractmethod
    async def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_identity: str,
    ) -> None:
        """Atomically upsert the document and replace all of its chunks."""

    @abstractmethod
    async def delete_documents(self, sources: Collection[str]) -> None:
        """Delete documents together with their chunks."""


class PassageSearch(ABC):
    """Implemented by PgVectorStore; InMemoryVectorStore in tests."""

    @abstractmethod
    async def ensure_compatible(self, embedding_identity: str) -> None:
        """Raise ReindexRequiredError unless the index exists with this identity and dimension."""

    @abstractmethod
    async def search(self, vector: Sequence[float], text: str, limit: int) -> SearchCandidates:
        """Up to `limit` chunks closest to `vector` and up to `limit` whose words match `text`."""

    @abstractmethod
    async def ping(self) -> None:
        """Run a trivial query, so it raises if the database is unreachable."""


class LanguageModel(ABC):
    """Implemented by OpenAICompatibleLLM; FakeLanguageModel in tests."""

    @abstractmethod
    async def complete(
        self, system_prompt: str, user_prompt: str, history: Sequence[ChatMessage] = ()
    ) -> str:
        """The model's answer to the user prompt, given the earlier turns in `history`."""
