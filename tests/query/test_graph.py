from oncall_rag.query.rag.graph import (
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
    store.search_results = [passage("a.md", 0.9), passage("b.md", 0.6), passage("c.md", 0.2)]
    llm = FakeLanguageModel("Restart the worker.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke(initial_state("How do I fix the backlog?", []))

    assert state["status"] == AnswerStatus.ANSWERED
    assert state["answer"] == "Restart the worker."
    assert [p.source for p in state["passages"]] == ["a.md", "b.md"]
    [call] = llm.calls
    assert "[1] Title a.md > Mitigation" in call.user_prompt
    assert "[2] Title b.md > Mitigation" in call.user_prompt
    assert "c.md" not in call.user_prompt
    assert "How do I fix the backlog?" in call.user_prompt
    [(_, top_k)] = store.search_calls
    assert top_k == RETRIEVAL.top_k


async def test_no_passage_above_the_threshold_skips_the_llm() -> None:
    store = InMemoryVectorStore()
    store.search_results = [passage("a.md", 0.4)]
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
    store.search_results = [passage("a.md", 0.9)]
    llm = FakeLanguageModel("Standalone question?", "Answer.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    await graph.ainvoke(initial_state("Why not?", [oldest, older, latest]))

    condense_call, generate_call = llm.calls
    assert oldest.content not in condense_call.user_prompt
    assert older.content in condense_call.user_prompt
    assert latest.content in condense_call.user_prompt
    assert generate_call.history == [older, latest]
