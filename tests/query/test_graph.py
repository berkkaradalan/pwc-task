import logging

import pytest

from oncall_rag.query.rag.graph import (
    CANDIDATES_PER_METHOD,
    Node,
    RetrievalConfig,
    build_graph,
    route_after_retrieval,
    route_from_start,
)
from oncall_rag.query.rag.prompts import HISTORY_MAX_TOTAL_CHARS, NO_CONTEXT_ANSWER
from oncall_rag.query.rag.state import AnswerStatus, initial_state
from oncall_rag.shared.models import ChatMessage, ChatRole, Passage
from tests.fakes import FakeEmbedder, FakeLanguageModel, InMemoryVectorStore

RETRIEVAL = RetrievalConfig(top_k=3, min_similarity=0.5)
GRAPH_LOGGER = "oncall_rag.query.rag.graph"
HISTORY = [
    ChatMessage(ChatRole.USER, "Should I run FLUSHALL?"),
    ChatMessage(ChatRole.ASSISTANT, "No."),
]


def passage(source: str, score: float) -> Passage:
    return Passage(
        source=source, title=f"Title {source}", heading_path="Mitigation", text="Do X.", score=score
    )


def test_starts_with_retrieval_without_history() -> None:
    assert route_from_start(initial_state("q", [])) == Node.RETRIEVE


def test_starts_with_condensing_when_history_is_given() -> None:
    assert route_from_start(initial_state("Why not?", HISTORY)) == Node.CONDENSE_QUESTION


def test_routes_to_generate_when_passages_were_kept() -> None:
    state = {"question": "q", "passages": [passage("a.md", 0.9)]}
    assert route_after_retrieval(state) == Node.GENERATE


def test_routes_to_no_context_without_passages() -> None:
    assert route_after_retrieval({"question": "q", "passages": []}) == Node.NO_CONTEXT


async def test_relevant_passages_are_answered_by_the_llm() -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.9), passage("b.md", 0.6)]
    llm = FakeLanguageModel("Restart the worker.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke(initial_state("How do I fix the backlog?", []))

    assert state["status"] == AnswerStatus.ANSWERED
    assert state["answer"] == "Restart the worker."
    assert [p.source for p in state["passages"]] == ["a.md", "b.md"]
    [call] = llm.calls
    assert "[1] Title a.md > Mitigation" in call.user_prompt
    assert "[2] Title b.md > Mitigation" in call.user_prompt
    assert "How do I fix the backlog?" in call.user_prompt
    [(_, text, limit)] = store.search_calls
    assert text == "How do I fix the backlog?"
    assert limit == CANDIDATES_PER_METHOD


async def test_both_candidate_lists_are_fused_and_cut_to_top_k() -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.9), passage("b.md", 0.7), passage("c.md", 0.6)]
    store.full_text_results = [passage("c.md", 0.6), passage("d.md", 0.3)]
    graph = build_graph(FakeEmbedder(), store, FakeLanguageModel("Answer."), RETRIEVAL)

    state = await graph.ainvoke(initial_state("How do I fix the backlog?", []))

    # c.md is found by both searches; d.md only by full text, with a low similarity.
    assert [p.source for p in state["passages"]] == ["c.md", "a.md", "b.md"]


async def test_once_the_gate_is_open_passages_below_the_threshold_are_kept() -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.6)]
    store.full_text_results = [passage("b.md", 0.3)]
    graph = build_graph(FakeEmbedder(), store, FakeLanguageModel("Answer."), RETRIEVAL)

    state = await graph.ainvoke(initial_state("Why is the payment still pending?", []))

    assert state["status"] == AnswerStatus.ANSWERED
    assert [(p.source, p.score) for p in state["passages"]] == [("a.md", 0.6), ("b.md", 0.3)]


async def test_full_text_matches_alone_do_not_open_the_gate() -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.4)]
    store.full_text_results = [passage("a.md", 0.4), passage("b.md", 0.45)]
    llm = FakeLanguageModel()
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke(initial_state("How do I configure Kafka consumer groups?", []))

    assert state["status"] == AnswerStatus.NO_RELEVANT_CONTEXT
    assert state["passages"] == []
    assert llm.calls == []


async def test_no_passage_above_the_threshold_skips_the_llm() -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.4)]
    llm = FakeLanguageModel()
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke(initial_state("What is the capital of France?", []))

    assert state["status"] == AnswerStatus.NO_RELEVANT_CONTEXT
    assert state["answer"] == NO_CONTEXT_ANSWER
    assert state["passages"] == []
    assert llm.calls == []


async def test_only_the_most_recent_history_that_fits_reaches_the_llm() -> None:
    half = HISTORY_MAX_TOTAL_CHARS // 2
    oldest = ChatMessage(ChatRole.USER, "o" * half)
    older = ChatMessage(ChatRole.ASSISTANT, "a" * half)
    latest = ChatMessage(ChatRole.USER, "u" * half)
    store = InMemoryVectorStore()
    store.vector_results = [passage("a.md", 0.9)]
    llm = FakeLanguageModel("Standalone question?", "Answer.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    await graph.ainvoke(initial_state("Why not?", [oldest, older, latest]))

    condense_call, generate_call = llm.calls
    assert oldest.content not in condense_call.user_prompt
    assert older.content in condense_call.user_prompt
    assert latest.content in condense_call.user_prompt
    assert generate_call.history == [older, latest]


async def test_the_condensed_search_query_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    store = InMemoryVectorStore()
    store.vector_results = [passage("redis.md", 0.9)]
    llm = FakeLanguageModel("Why not run FLUSHALL?", "Because it deletes every key.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    with caplog.at_level(logging.INFO, logger=GRAPH_LOGGER):
        await graph.ainvoke(initial_state("Why not?", HISTORY))

    [record] = [record for record in caplog.records if record.name == GRAPH_LOGGER]
    assert record.levelno == logging.INFO
    assert (
        record.getMessage()
        == "Condensed the follow-up into the search query 'Why not run FLUSHALL?'"
    )
