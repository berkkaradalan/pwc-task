from oncall_rag.query.rag.graph import Node, RetrievalConfig, build_graph, route_after_retrieval
from oncall_rag.query.rag.prompts import NO_CONTEXT_ANSWER
from oncall_rag.query.rag.state import AnswerStatus
from oncall_rag.shared.models import Passage
from tests.fakes import FakeEmbedder, FakeLanguageModel, InMemoryVectorStore

RETRIEVAL = RetrievalConfig(top_k=3, min_similarity=0.5)


def passage(source: str, score: float) -> Passage:
    return Passage(
        source=source, title=f"Title {source}", heading_path="Mitigation", text="Do X.", score=score
    )


def test_routes_to_generate_when_passages_were_kept() -> None:
    state = {"question": "q", "passages": [passage("a.md", 0.9)]}
    assert route_after_retrieval(state) == Node.GENERATE


def test_routes_to_no_context_without_passages() -> None:
    assert route_after_retrieval({"question": "q", "passages": []}) == Node.NO_CONTEXT


async def test_relevant_passages_are_answered_by_the_llm() -> None:
    store = InMemoryVectorStore()
    store.search_results = [passage("a.md", 0.9), passage("b.md", 0.6), passage("c.md", 0.2)]
    llm = FakeLanguageModel(answer="Restart the worker.")
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke({"question": "How do I fix the backlog?"})

    assert state["status"] == AnswerStatus.ANSWERED
    assert state["answer"] == "Restart the worker."
    assert [p.source for p in state["passages"]] == ["a.md", "b.md"]
    [(_, user_prompt)] = llm.calls
    assert "[1] Title a.md > Mitigation" in user_prompt
    assert "[2] Title b.md > Mitigation" in user_prompt
    assert "c.md" not in user_prompt
    assert "How do I fix the backlog?" in user_prompt
    [(_, top_k)] = store.search_calls
    assert top_k == RETRIEVAL.top_k


async def test_no_passage_above_the_threshold_skips_the_llm() -> None:
    store = InMemoryVectorStore()
    store.search_results = [passage("a.md", 0.4)]
    llm = FakeLanguageModel()
    graph = build_graph(FakeEmbedder(), store, llm, RETRIEVAL)

    state = await graph.ainvoke({"question": "What is the capital of France?"})

    assert state["status"] == AnswerStatus.NO_RELEVANT_CONTEXT
    assert state["answer"] == NO_CONTEXT_ANSWER
    assert state["passages"] == []
    assert llm.calls == []
