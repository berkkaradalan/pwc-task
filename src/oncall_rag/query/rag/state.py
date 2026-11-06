"""State of one question's run through the graph. Nothing in it outlives the request."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Required, TypedDict

from oncall_rag.shared.models import ChatMessage, Passage


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    NO_RELEVANT_CONTEXT = "no_relevant_context"


class RagState(TypedDict, total=False):
    question: Required[str]
    history: Required[Sequence[ChatMessage]]
    search_query: Required[str]
    passages: list[Passage]
    answer: str
    status: AnswerStatus


class CondenseUpdate(TypedDict):
    search_query: str


class RetrievalUpdate(TypedDict):
    passages: list[Passage]


class AnswerUpdate(TypedDict):
    answer: str
    status: AnswerStatus


def initial_state(question: str, history: Sequence[ChatMessage]) -> RagState:
    """Search starts from the question; condense_question rewrites it when history is given."""
    return {"question": question, "history": history, "search_query": question}
