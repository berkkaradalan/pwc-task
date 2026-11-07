"""Answering flow: [condense_question ->] retrieve -> generate | no_context -> END."""

import logging
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
)
from oncall_rag.shared.models import SearchCandidates
from oncall_rag.shared.ports import Embedder, LanguageModel, PassageSearch

logger = logging.getLogger(__name__)

RagGraph = CompiledStateGraph[RagState, None, RagState, RagState]

# Candidates per search method before fusion: deep enough that a runbook one method ranks low
# can still be lifted into the top results by the other.
CANDIDATES_PER_METHOD = 20


class Node(StrEnum):
    CONDENSE_QUESTION = "condense_question"
    RETRIEVE = "retrieve"
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
    graph.add_node(Node.GENERATE, nodes.generate)
    graph.add_node(Node.NO_CONTEXT, nodes.no_context)
    graph.add_conditional_edges(START, route_from_start, [Node.CONDENSE_QUESTION, Node.RETRIEVE])
    graph.add_edge(Node.CONDENSE_QUESTION, Node.RETRIEVE)
    graph.add_conditional_edges(
        Node.RETRIEVE, route_after_retrieval, [Node.GENERATE, Node.NO_CONTEXT]
    )
    graph.add_edge(Node.GENERATE, END)
    graph.add_edge(Node.NO_CONTEXT, END)
    return graph.compile()


def route_from_start(state: RagState) -> Node:
    if state["history"]:
        return Node.CONDENSE_QUESTION
    return Node.RETRIEVE


def route_after_retrieval(state: RagState) -> Node:
    if state["passages"]:
        return Node.GENERATE
    return Node.NO_CONTEXT


@dataclass(frozen=True, slots=True)
class _Nodes:
    embedder: Embedder
    store: PassageSearch
    llm: LanguageModel
    retrieval: RetrievalConfig

    async def condense_question(self, state: RagState) -> CondenseUpdate:
        prompt = build_condense_prompt(recent_history(state["history"]), state["question"])
        search_query = await self.llm.complete(CONDENSE_SYSTEM_PROMPT, prompt)
        logger.info("Condensed the follow-up into the search query %r", search_query)
        return {"search_query": search_query}

    async def retrieve(self, state: RagState) -> RetrievalUpdate:
        search_query = state["search_query"]
        vector = await self.embedder.embed_query(search_query)
        candidates = await self.store.search(vector, search_query, CANDIDATES_PER_METHOD)
        if not self._is_relevant(candidates):
            return {"passages": []}
        fused = reciprocal_rank_fusion([candidates.by_vector, candidates.by_full_text])
        return {"passages": fused[: self.retrieval.top_k]}

    def _is_relevant(self, candidates: SearchCandidates) -> bool:
        """Only vector similarity opens the gate: full-text also matches unrelated questions."""
        return any(
            passage.score >= self.retrieval.min_similarity for passage in candidates.by_vector
        )

    async def generate(self, state: RagState) -> AnswerUpdate:
        prompt = build_user_prompt(state["question"], state["passages"])
        history = recent_history(state["history"])
        answer = await self.llm.complete(SYSTEM_PROMPT, prompt, history)
        return {"answer": answer, "status": AnswerStatus.ANSWERED}

    async def no_context(self, state: RagState) -> AnswerUpdate:
        return {"answer": NO_CONTEXT_ANSWER, "status": AnswerStatus.NO_RELEVANT_CONTEXT}
