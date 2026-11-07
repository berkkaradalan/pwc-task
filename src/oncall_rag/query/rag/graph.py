"""Answering flow: [condense_question ->] retrieve | retrieve_follow_up
-> generate | no_context -> END."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from oncall_rag.query.rag.fusion import reciprocal_rank_fusion
from oncall_rag.query.rag.prompts import (
    CONDENSE_SYSTEM_PROMPT,
    NO_CONTEXT_ANSWER,
    SYSTEM_PROMPT,
    build_condense_prompt,
    build_user_prompt,
    recent_history,
)
from oncall_rag.query.rag.state import (
    AnswerStatus,
    AnswerUpdate,
    CondenseUpdate,
    RagState,
    RetrievalUpdate,
    SearchQueryRetrievalUpdate,
)
from oncall_rag.shared.models import ChatMessage, ChatRole, Passage, SearchCandidates
from oncall_rag.shared.ports import Embedder, LanguageModel, PassageSearch

logger = logging.getLogger(__name__)

RagGraph = CompiledStateGraph[RagState, None, RagState, RagState]

# Candidates per search method before fusion: deep enough that a runbook one method ranks low
# can still be lifted into the top results by the other.
CANDIDATES_PER_METHOD = 20


class Node(StrEnum):
    CONDENSE_QUESTION = "condense_question"
    RETRIEVE = "retrieve"
    RETRIEVE_FOLLOW_UP = "retrieve_follow_up"
    GENERATE = "generate"
    NO_CONTEXT = "no_context"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    top_k: int
    min_similarity: float


def build_graph(
    embedder: Embedder, store: PassageSearch, llm: LanguageModel, retrieval: RetrievalConfig
) -> RagGraph:
    nodes = _Nodes(embedder=embedder, store=store, llm=llm, retrieval=retrieval)
    graph = StateGraph(RagState)
    graph.add_node(Node.CONDENSE_QUESTION, nodes.condense_question)
    graph.add_node(Node.RETRIEVE, nodes.retrieve)
    graph.add_node(Node.RETRIEVE_FOLLOW_UP, nodes.retrieve_follow_up)
    graph.add_node(Node.GENERATE, nodes.generate)
    graph.add_node(Node.NO_CONTEXT, nodes.no_context)
    graph.add_conditional_edges(START, route_from_start, [Node.CONDENSE_QUESTION, Node.RETRIEVE])
    graph.add_conditional_edges(
        Node.CONDENSE_QUESTION, route_after_condensing, [Node.RETRIEVE, Node.RETRIEVE_FOLLOW_UP]
    )
    for retrieval_node in (Node.RETRIEVE, Node.RETRIEVE_FOLLOW_UP):
        graph.add_conditional_edges(
            retrieval_node, route_after_retrieval, [Node.GENERATE, Node.NO_CONTEXT]
        )
    graph.add_edge(Node.GENERATE, END)
    graph.add_edge(Node.NO_CONTEXT, END)
    return graph.compile()


def route_from_start(state: RagState) -> Node:
    if state["history"]:
        return Node.CONDENSE_QUESTION
    return Node.RETRIEVE


def route_after_condensing(state: RagState) -> Node:
    """Small models do not always rewrite a follow-up such as "Why not?", whatever the prompt
    says, so an unrewritten one is also tried after the previous question."""
    is_unchanged = state["search_query"] == state["question"]
    if is_unchanged and _previous_user_question(state["history"]) is not None:
        return Node.RETRIEVE_FOLLOW_UP
    return Node.RETRIEVE


def route_after_retrieval(state: RagState) -> Node:
    if state["passages"]:
        return Node.GENERATE
    return Node.NO_CONTEXT


def _previous_user_question(history: Sequence[ChatMessage]) -> str | None:
    for message in reversed(history):
        if message.role is ChatRole.USER:
            return message.content
    return None


def _is_same_question(rewrite: str, question: str) -> bool:
    return rewrite.casefold().split() == question.casefold().split()


def _top_similarity(candidates: SearchCandidates) -> float:
    return max((passage.score for passage in candidates.by_vector), default=float("-inf"))


@dataclass(frozen=True, slots=True)
class _Nodes:
    embedder: Embedder
    store: PassageSearch
    llm: LanguageModel
    retrieval: RetrievalConfig

    async def condense_question(self, state: RagState) -> CondenseUpdate:
        question = state["question"]
        prompt = build_condense_prompt(recent_history(state["history"]), question)
        rewrite = await self.llm.complete(CONDENSE_SYSTEM_PROMPT, prompt)
        if _is_same_question(rewrite, question):
            logger.info("The follow-up was not rewritten")
            return {"search_query": question}
        logger.info("Condensed the follow-up into the search query %r", rewrite)
        return {"search_query": rewrite}

    async def retrieve(self, state: RagState) -> RetrievalUpdate:
        candidates = await self._search(state["search_query"])
        return {"passages": self._relevant_passages(candidates)}

    async def retrieve_follow_up(self, state: RagState) -> SearchQueryRetrievalUpdate:
        """Keeps whichever matches the documents more closely: the follow-up alone or after the
        previous question. Alone, a short one like "Why not?" can pass the gate on noise."""
        question = state["question"]
        previous_question = _previous_user_question(state["history"])
        assert previous_question is not None  # narrows the type: the route checked it
        with_previous = f"{previous_question} {question}"
        alone_candidates = await self._search(question)
        with_previous_candidates = await self._search(with_previous)
        if _top_similarity(with_previous_candidates) > _top_similarity(alone_candidates):
            logger.info("Searching with the previous question as %r", with_previous)
            return {
                "search_query": with_previous,
                "passages": self._relevant_passages(with_previous_candidates),
            }
        logger.info(
            "Searching with the follow-up alone: it matches better than with the previous question"
        )
        return {"search_query": question, "passages": self._relevant_passages(alone_candidates)}

    async def _search(self, search_query: str) -> SearchCandidates:
        vector = await self.embedder.embed_query(search_query)
        return await self.store.search(vector, search_query, CANDIDATES_PER_METHOD)

    def _relevant_passages(self, candidates: SearchCandidates) -> list[Passage]:
        """Only vector similarity opens the gate: full-text also matches unrelated questions."""
        if _top_similarity(candidates) < self.retrieval.min_similarity:
            return []
        fused = reciprocal_rank_fusion([candidates.by_vector, candidates.by_full_text])
        return fused[: self.retrieval.top_k]

    async def generate(self, state: RagState) -> AnswerUpdate:
        prompt = build_user_prompt(state["question"], state["passages"])
        history = recent_history(state["history"])
        answer = await self.llm.complete(SYSTEM_PROMPT, prompt, history)
        return {"answer": answer, "status": AnswerStatus.ANSWERED}

    async def no_context(self, state: RagState) -> AnswerUpdate:
        return {"answer": NO_CONTEXT_ANSWER, "status": AnswerStatus.NO_RELEVANT_CONTEXT}
