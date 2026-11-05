"""State of one question's run through the graph. Nothing in it outlives the request."""

from enum import StrEnum
from typing import Required, TypedDict

from oncall_rag.shared.models import Passage


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    NO_RELEVANT_CONTEXT = "no_relevant_context"


class RagState(TypedDict, total=False):
    question: Required[str]
    passages: list[Passage]
    answer: str
    status: AnswerStatus
