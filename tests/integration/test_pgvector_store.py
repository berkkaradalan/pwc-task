import math
from collections.abc import Sequence

import psycopg
import pytest

from oncall_rag.query.rag.fusion import reciprocal_rank_fusion
from oncall_rag.shared.adapters.vectorstore.pgvector import PgVectorStore, connect_pgvector_store
from oncall_rag.shared.errors import ReindexRequiredError
from oncall_rag.shared.models import Chunk, Document, Passage, SearchCandidates
from tests.integration.conftest import TEST_DIMENSION, TEST_POOL_SIZE

pytestmark = pytest.mark.integration

IDENTITY = "test-embedding@test-provider"
# More than any test indexes, so a test sees every candidate unless it sets its own limit.
ALL_CANDIDATES = 10
QUERY_VECTOR = [1.0, 0.0, 0.0]

TextAndVector = tuple[str, list[float]]


async def index_document(
    store: PgVectorStore,
    source: str,
    chunks: Sequence[TextAndVector],
    *,
    content_hash: str = "hash-1",
    embedding_identity: str = IDENTITY,
) -> None:
    document = Document(
        source=source,
        title=f"Title of {source}",
        content="",
        content_hash=content_hash,
        metadata={"owner": "payments"},
    )
    stored_chunks = [
        Chunk(document_source=source, chunk_index=index, heading_path="Mitigation", text=text)
        for index, (text, _) in enumerate(chunks)
    ]
    embeddings = [vector for _, vector in chunks]
    await store.replace_document(document, stored_chunks, embeddings, embedding_identity)


def texts(passages: Sequence[Passage]) -> list[str]:
    return [passage.text for passage in passages]


def cosine_similarity(first: Sequence[float], second: Sequence[float]) -> float:
    dot_product = sum(a * b for a, b in zip(first, second, strict=True))
    return dot_product / (math.hypot(*first) * math.hypot(*second))


async def test_initialize_twice_keeps_the_schema_and_the_data(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("Restart the worker.", QUERY_VECTOR)])

    await store.initialize()

    assert await store.list_sources() == {"a.md"}
    assert await store.get_content_hash("a.md") == "hash-1"


async def test_reset_empties_the_index(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("Restart the worker.", QUERY_VECTOR)])

    await store.reset()

    assert await store.list_sources() == set()
    assert await store.search(QUERY_VECTOR, "worker", ALL_CANDIDATES) == SearchCandidates([], [])


async def test_search_on_an_empty_index_finds_nothing(store: PgVectorStore) -> None:
    candidates = await store.search(QUERY_VECTOR, "restart the worker", ALL_CANDIDATES)

    assert candidates == SearchCandidates(by_vector=[], by_full_text=[])


async def test_a_close_chunk_is_found_by_vector_without_sharing_a_word(
    store: PgVectorStore,
) -> None:
    await index_document(store, "a.md", [("Scale out the consumers.", [0.9, 0.1, 0.0])])

    candidates = await store.search(QUERY_VECTOR, "zebra", ALL_CANDIDATES)

    assert texts(candidates.by_vector) == ["Scale out the consumers."]
    assert candidates.by_full_text == []


async def test_a_distant_chunk_is_found_by_its_words(store: PgVectorStore) -> None:
    near = [
        ("Alpha.", [1.0, 0.0, 0.0]),
        ("Beta.", [0.9, 0.1, 0.0]),
        ("Gamma.", [0.8, 0.2, 0.0]),
    ]
    await index_document(store, "near.md", near)
    await index_document(store, "far.md", [("Drain the rabbitmq queue.", [0.0, 0.0, 1.0])])

    candidates = await store.search(QUERY_VECTOR, "rabbitmq", limit=3)

    assert texts(candidates.by_vector) == ["Alpha.", "Beta.", "Gamma."]
    assert texts(candidates.by_full_text) == ["Drain the rabbitmq queue."]


async def test_a_chunk_found_by_both_lists_ranks_first_after_fusion(
    store: PgVectorStore,
) -> None:
    await index_document(store, "vector.md", [("Check the dashboards.", [1.0, 0.0, 0.0])])
    await index_document(store, "both.md", [("Restart rabbitmq.", [0.9, 0.1, 0.0])])
    await index_document(store, "text.md", [("The rabbitmq broker.", [0.0, 0.0, 1.0])])

    candidates = await store.search(QUERY_VECTOR, "restart rabbitmq", limit=2)
    fused = reciprocal_rank_fusion([candidates.by_vector, candidates.by_full_text])

    assert texts(candidates.by_vector) == ["Check the dashboards.", "Restart rabbitmq."]
    assert texts(candidates.by_full_text) == ["Restart rabbitmq.", "The rabbitmq broker."]
    assert texts(fused) == ["Restart rabbitmq.", "Check the dashboards.", "The rabbitmq broker."]


async def test_a_chunk_sharing_one_word_of_the_question_matches(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("Restart the payments worker.", [0.0, 1.0, 0.0])])

    candidates = await store.search(QUERY_VECTOR, "restart kafka zookeeper", ALL_CANDIDATES)

    assert texts(candidates.by_full_text) == ["Restart the payments worker."]


@pytest.mark.parametrize(
    "question",
    [pytest.param("the of and", id="stop-words"), pytest.param("?! ... --", id="punctuation")],
)
async def test_a_question_without_searchable_words_falls_back_to_vector_results(
    store: PgVectorStore, question: str
) -> None:
    await index_document(store, "a.md", [("The and of the worker.", [0.9, 0.1, 0.0])])

    candidates = await store.search(QUERY_VECTOR, question, ALL_CANDIDATES)

    assert candidates.by_full_text == []
    assert texts(candidates.by_vector) == ["The and of the worker."]


async def test_a_hyphenated_word_also_matches_its_parts_on_their_own(
    store: PgVectorStore,
) -> None:
    far = [0.0, 0.0, 1.0]
    await index_document(store, "hyphenated.md", [("Restart payments-worker.", far)])
    await index_document(store, "spaced.md", [("Restart the payments worker.", far)])
    await index_document(store, "part.md", [("Payment provider outage.", far)])
    await index_document(store, "unrelated.md", [("Rotate the TLS certificate.", far)])

    candidates = await store.search(QUERY_VECTOR, "payments-worker", ALL_CANDIDATES)

    assert texts(candidates.by_full_text) == [
        "Restart payments-worker.",
        "Restart the payments worker.",
        "Payment provider outage.",
    ]


async def test_each_list_holds_at_most_limit_candidates(store: PgVectorStore) -> None:
    chunks = [(f"Restart rabbitmq node {node}.", [1.0, node / 10, 0.0]) for node in range(5)]
    await index_document(store, "a.md", chunks)

    candidates = await store.search(QUERY_VECTOR, "rabbitmq", limit=2)

    assert texts(candidates.by_vector) == ["Restart rabbitmq node 0.", "Restart rabbitmq node 1."]
    assert len(candidates.by_full_text) == 2


async def test_score_is_the_cosine_similarity_in_both_lists(store: PgVectorStore) -> None:
    query_vector = [1.0, 2.0, 2.0]
    vectors = {"Restart rabbitmq.": [3.0, 4.0, 0.0], "Drain rabbitmq.": [0.0, 0.0, 1.0]}
    await index_document(store, "a.md", list(vectors.items()))

    candidates = await store.search(query_vector, "rabbitmq", ALL_CANDIDATES)

    passages = [*candidates.by_vector, *candidates.by_full_text]
    assert len(passages) == 4
    for passage in passages:
        expected = cosine_similarity(query_vector, vectors[passage.text])
        assert passage.score == pytest.approx(expected, abs=1e-6)


async def test_a_passage_carries_its_document_title_and_heading(store: PgVectorStore) -> None:
    await index_document(store, "runbooks/a.md", [("Restart the worker.", QUERY_VECTOR)])

    [passage] = (await store.search(QUERY_VECTOR, "zebra", ALL_CANDIDATES)).by_vector

    assert passage.source == "runbooks/a.md"
    assert passage.title == "Title of runbooks/a.md"
    assert passage.heading_path == "Mitigation"


async def test_an_index_that_was_never_built_needs_the_ingest_service(database_url: str) -> None:
    async with connect_pgvector_store(
        database_url, dimension=TEST_DIMENSION, pool_max_size=TEST_POOL_SIZE
    ) as unbuilt:
        with pytest.raises(ReindexRequiredError, match="has not been built yet"):
            await unbuilt.ensure_compatible(IDENTITY)


async def test_an_index_with_another_dimension_needs_a_reindex(
    store: PgVectorStore, database_url: str
) -> None:
    other_dimension = TEST_DIMENSION + 1

    async with connect_pgvector_store(
        database_url, dimension=other_dimension, pool_max_size=TEST_POOL_SIZE
    ) as reconfigured:
        with pytest.raises(
            ReindexRequiredError, match="stores 3-dimensional vectors, but EMBEDDING_DIM is 4"
        ):
            await reconfigured.ensure_compatible(IDENTITY)


async def test_an_index_with_another_embedding_identity_needs_a_reindex(
    store: PgVectorStore,
) -> None:
    await index_document(
        store, "a.md", [("Restart.", QUERY_VECTOR)], embedding_identity="model@nebius"
    )

    with pytest.raises(ReindexRequiredError, match=r"\[model@nebius\].*'model@deepinfra'"):
        await store.ensure_compatible("model@deepinfra")


async def test_an_index_with_the_configured_identity_is_compatible(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("Restart.", QUERY_VECTOR)])

    await store.ensure_compatible(IDENTITY)


async def test_a_built_empty_index_is_compatible(store: PgVectorStore) -> None:
    await store.ensure_compatible(IDENTITY)


async def test_a_failed_replace_keeps_the_previous_chunks_and_hash(store: PgVectorStore) -> None:
    old = [("Old first.", [1.0, 0.0, 0.0]), ("Old second.", [0.0, 1.0, 0.0])]
    await index_document(store, "a.md", old, content_hash="old")
    wrong_length_vector = [1.0, 0.0, 0.0, 0.0]
    new = [("New first.", [1.0, 0.0, 0.0]), ("New second.", wrong_length_vector)]

    with pytest.raises(psycopg.errors.DataException):
        await index_document(store, "a.md", new, content_hash="new")

    assert await store.get_content_hash("a.md") == "old"
    candidates = await store.search(QUERY_VECTOR, "zebra", ALL_CANDIDATES)
    assert sorted(texts(candidates.by_vector)) == ["Old first.", "Old second."]


async def test_replace_swaps_the_chunks_hash_and_identity(store: PgVectorStore) -> None:
    old = [("Old first.", [1.0, 0.0, 0.0]), ("Old second.", [0.0, 1.0, 0.0])]
    await index_document(store, "a.md", old, content_hash="old", embedding_identity="old-model")

    new = [("New only.", [1.0, 0.0, 0.0])]
    await index_document(store, "a.md", new, content_hash="new", embedding_identity="new-model")

    candidates = await store.search(QUERY_VECTOR, "zebra", ALL_CANDIDATES)
    assert texts(candidates.by_vector) == ["New only."]
    assert await store.get_content_hash("a.md") == "new"
    assert await store.list_embedding_identities() == {"new-model"}


async def test_sources_hashes_and_identities_are_listed(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("A.", QUERY_VECTOR)], content_hash="ha")
    await index_document(
        store, "b.md", [("B.", QUERY_VECTOR)], content_hash="hb", embedding_identity="other"
    )

    assert await store.list_sources() == {"a.md", "b.md"}
    assert await store.list_embedding_identities() == {IDENTITY, "other"}
    assert await store.get_content_hash("b.md") == "hb"
    assert await store.get_content_hash("missing.md") is None


async def test_deleting_a_document_removes_its_chunks_from_search(store: PgVectorStore) -> None:
    await index_document(store, "a.md", [("Restart rabbitmq.", QUERY_VECTOR)])
    await index_document(store, "b.md", [("Drain rabbitmq.", QUERY_VECTOR)])

    await store.delete_documents(["a.md"])
    await store.delete_documents([])

    assert await store.list_sources() == {"b.md"}
    candidates = await store.search(QUERY_VECTOR, "rabbitmq", ALL_CANDIDATES)
    assert texts(candidates.by_vector) == ["Drain rabbitmq."]
    assert texts(candidates.by_full_text) == ["Drain rabbitmq."]


async def test_ping_succeeds_on_a_reachable_database(store: PgVectorStore) -> None:
    await store.ping()
