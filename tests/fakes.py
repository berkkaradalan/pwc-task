"""In-memory test doubles for the domain ports. No network, no database."""

import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from oncall_rag.shared.models import Chunk, Document

FAKE_DIMENSION = 8


class FakeEmbedder:
    """Deterministic vectors derived from a hash of the text. Records document calls."""

    def __init__(self, model_name: str = "fake-embedding-model") -> None:
        self._model_name = model_name
        self.document_calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def embedded_texts(self) -> list[str]:
        return [text for call in self.document_calls for text in call]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:FAKE_DIMENSION]]


@dataclass(frozen=True, slots=True)
class StoredDocument:
    document: Document
    chunks: list[Chunk]
    embeddings: list[list[float]]
    embedding_model: str


class InMemoryVectorStore:
    def __init__(self) -> None:
        self.documents: dict[str, StoredDocument] = {}
        self.initialize_calls = 0
        self.reset_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def reset(self) -> None:
        self.reset_calls += 1
        self.documents.clear()

    async def list_sources(self) -> set[str]:
        return set(self.documents)

    async def list_embedding_models(self) -> set[str]:
        return {stored.embedding_model for stored in self.documents.values()}

    async def get_content_hash(self, source: str) -> str | None:
        stored = self.documents.get(source)
        return stored.document.content_hash if stored else None

    async def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None:
        self.documents[document.source] = StoredDocument(
            document=document,
            chunks=list(chunks),
            embeddings=[list(vector) for vector in embeddings],
            embedding_model=embedding_model,
        )

    async def delete_documents(self, sources: Collection[str]) -> None:
        for source in sources:
            del self.documents[source]
