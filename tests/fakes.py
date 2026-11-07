"""In-memory test doubles for the domain ports and the embeddings API. No network, no database."""

import hashlib
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass

import httpx2
import openai
import psycopg

from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import ChatMessage, Chunk, Document, Passage
from oncall_rag.shared.ports import DocumentIndex, Embedder, LanguageModel, PassageSearch

FAKE_DIMENSION = 8
FAKE_EMBEDDINGS_API_URL = "http://embeddings.test/v1"


class FakeEmbedder(Embedder):
    """Deterministic vectors derived from a hash of the text. Records document and query calls."""

    def __init__(self, embedding_identity: str = "fake-embedding-model") -> None:
        self._embedding_identity = embedding_identity
        self.document_calls: list[list[str]] = []
        self.queries: list[str] = []

    @property
    def embedding_identity(self) -> str:
        return self._embedding_identity

    @property
    def embedded_texts(self) -> list[str]:
        return [text for call in self.document_calls for text in call]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:FAKE_DIMENSION]]


class FakeEmbeddingsApi:
    """httpx2.MockTransport handler for `/v1/embeddings`; records request payloads."""

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.payloads: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        data = [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(len(text))] * self.dimension,
            }
            for index, text in enumerate(payload["input"])
        ]
        usage = {"prompt_tokens": 0, "total_tokens": 0}
        return httpx2.Response(
            200, json={"object": "list", "data": data, "model": payload["model"], "usage": usage}
        )

    def client(self) -> openai.AsyncOpenAI:
        """An SDK client whose requests are answered by this handler."""
        return openai.AsyncOpenAI(
            base_url=FAKE_EMBEDDINGS_API_URL,
            api_key="test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self)),
        )


@dataclass(frozen=True, slots=True)
class StoredDocument:
    document: Document
    chunks: list[Chunk]
    embeddings: list[list[float]]
    embedding_identity: str


class InMemoryVectorStore(DocumentIndex, PassageSearch):
    """Stores documents in a dict. `search` returns the canned `search_results`."""

    def __init__(self) -> None:
        self.documents: dict[str, StoredDocument] = {}
        self.initialize_calls = 0
        self.reset_calls = 0
        self.search_results: list[Passage] = []
        self.search_calls: list[tuple[list[float], int]] = []
        self.is_down = False

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def reset(self) -> None:
        self.reset_calls += 1
        self.documents.clear()

    async def ping(self) -> None:
        if self.is_down:
            raise psycopg.OperationalError("connection to server at db.internal failed")

    async def search(self, vector: Sequence[float], top_k: int) -> list[Passage]:
        self.search_calls.append((list(vector), top_k))
        return self.search_results[:top_k]

    async def list_sources(self) -> set[str]:
        return set(self.documents)

    async def list_embedding_identities(self) -> set[str]:
        return {stored.embedding_identity for stored in self.documents.values()}

    async def ensure_compatible(self, embedding_identity: str) -> None:
        foreign = await self.list_embedding_identities() - {embedding_identity}
        if foreign:
            stored = ", ".join(sorted(foreign))
            raise ReindexRequiredError(
                f"Index identities [{stored}], configured {embedding_identity}"
            )

    async def get_content_hash(self, source: str) -> str | None:
        stored = self.documents.get(source)
        return stored.document.content_hash if stored else None

    async def replace_document(
        self,
        document: Document,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        embedding_identity: str,
    ) -> None:
        self.documents[document.source] = StoredDocument(
            document=document,
            chunks=list(chunks),
            embeddings=[list(vector) for vector in embeddings],
            embedding_identity=embedding_identity,
        )

    async def delete_documents(self, sources: Collection[str]) -> None:
        for source in sources:
            del self.documents[source]


@dataclass(frozen=True, slots=True)
class CompletionCall:
    system_prompt: str
    user_prompt: str
    history: list[ChatMessage]


class FakeLanguageModel(LanguageModel):
    """Returns `answers` in order, one per call, or raises `error` if set. Records every call."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.error: Exception | None = None
        self.calls: list[CompletionCall] = []

    async def complete(
        self, system_prompt: str, user_prompt: str, history: Sequence[ChatMessage] = ()
    ) -> str:
        self.calls.append(CompletionCall(system_prompt, user_prompt, list(history)))
        if self.error is not None:
            raise self.error
        return self.answers.pop(0)
