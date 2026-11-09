"""In-memory test doubles for the domain ports, the provider APIs and the builders the
composition roots call. No network, no database."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Collection, Coroutine, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import openai
import psycopg

from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import ChatMessage, Chunk, Document, Passage, SearchCandidates
from oncall_rag.shared.ports import DocumentIndex, Embedder, LanguageModel, PassageSearch

FAKE_DIMENSION = 8
FAKE_EMBEDDINGS_API_URL = "http://embeddings.test/v1"
FAKE_PROVIDER_API_URL = "http://provider.test/v1"


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
    """httpx2.MockTransport handler for `/v1/embeddings`; records request payloads.

    Answers every request with `status_code`; only 200 carries embeddings.
    """

    def __init__(self, dimension: int, status_code: int = 200) -> None:
        self.dimension = dimension
        self.status_code = status_code
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if self.status_code != httpx2.codes.OK:
            return httpx2.Response(self.status_code, json={})
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
    """Stores documents in a dict. `search` returns the canned `vector_results` (or those in
    `vector_results_by_text` for that search text) and `full_text_results`."""

    def __init__(self) -> None:
        self.documents: dict[str, StoredDocument] = {}
        self.initialize_calls = 0
        self.reset_calls = 0
        self.vector_results: list[Passage] = []
        self.vector_results_by_text: dict[str, list[Passage]] = {}
        self.full_text_results: list[Passage] = []
        self.search_calls: list[tuple[list[float], str, int]] = []
        self.compatibility_checks: list[str] = []
        self.is_down = False

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def reset(self) -> None:
        self.reset_calls += 1
        self.documents.clear()

    async def ping(self) -> None:
        if self.is_down:
            raise psycopg.OperationalError("connection to server at db.internal failed")

    async def search(self, vector: Sequence[float], text: str, limit: int) -> SearchCandidates:
        self.search_calls.append((list(vector), text, limit))
        vector_results = self.vector_results_by_text.get(text, self.vector_results)
        return SearchCandidates(
            by_vector=vector_results[:limit], by_full_text=self.full_text_results[:limit]
        )

    async def list_sources(self) -> set[str]:
        return set(self.documents)

    async def list_embedding_identities(self) -> set[str]:
        return {stored.embedding_identity for stored in self.documents.values()}

    async def ensure_compatible(self, embedding_identity: str) -> None:
        self.compatibility_checks.append(embedding_identity)
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
class StoreConnection:
    database_url: str
    dimension: int
    pool_max_size: int


class FakeStoreConnector:
    """Stands in for `connect_pgvector_store`: records the arguments and yields `store`."""

    def __init__(self, store: InMemoryVectorStore) -> None:
        self.store = store
        self.connections: list[StoreConnection] = []

    @asynccontextmanager
    async def __call__(
        self, database_url: str, *, dimension: int, pool_max_size: int
    ) -> AsyncIterator[InMemoryVectorStore]:
        self.connections.append(StoreConnection(database_url, dimension, pool_max_size))
        yield self.store


class FakeChatApi:
    """httpx2.MockTransport handler for `/v1/chat/completions`; answers every request with
    `answer` after `delay_seconds` and records the request payloads."""

    def __init__(self, answer: str, delay_seconds: float = 0) -> None:
        self.answer = answer
        self.delay_seconds = delay_seconds
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        await asyncio.sleep(self.delay_seconds)
        message = {"role": "assistant", "content": self.answer}
        choice = {"index": 0, "finish_reason": "stop", "message": message}
        completion = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": payload["model"],
            "choices": [choice],
        }
        return httpx2.Response(200, json=completion)


class FakeClientBuilder:
    """Stands in for `build_embedding_client` or `build_llm_client`: records the arguments and
    returns an SDK client whose requests are answered by `responder`."""

    def __init__(
        self,
        responder: Callable[[httpx2.Request], httpx2.Response]
        | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]],
    ) -> None:
        self.responder = responder
        self.calls: list[tuple[object, ...]] = []
        self.clients: list[openai.AsyncOpenAI] = []

    def __call__(self, *arguments: object) -> openai.AsyncOpenAI:
        self.calls.append(arguments)
        client = openai.AsyncOpenAI(
            base_url=FAKE_PROVIDER_API_URL,
            api_key="test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.responder)),
        )
        self.clients.append(client)
        return client


@dataclass(frozen=True, slots=True)
class FakeProviders:
    """What the query composition root gets instead of the database pool and the SDK clients."""

    store: InMemoryVectorStore
    connector: FakeStoreConnector
    embeddings_api: FakeEmbeddingsApi
    chat_api: FakeChatApi
    embedding_clients: FakeClientBuilder
    llm_clients: FakeClientBuilder


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
