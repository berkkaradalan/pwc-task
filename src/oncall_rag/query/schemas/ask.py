import textwrap

from pydantic import BaseModel, ConfigDict, Field

from oncall_rag.query.rag.state import AnswerStatus
from oncall_rag.shared.models import ChatRole, Passage

QUESTION_MAX_CHARS = 1000
HISTORY_MESSAGE_MAX_CHARS = 4000
MAX_HISTORY_MESSAGES = 10
EXCERPT_MAX_CHARS = 200
EXCERPT_PLACEHOLDER = " ..."
SCORE_DECIMALS = 3


class HistoryMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: ChatRole
    content: str = Field(min_length=1, max_length=HISTORY_MESSAGE_MAX_CHARS)


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    history: list[HistoryMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
        description="Earlier turns of this conversation, oldest first. The server stores none.",
    )


class Source(BaseModel):
    source: str
    title: str
    heading_path: str
    score: float
    excerpt: str

    @classmethod
    def from_passage(cls, passage: Passage) -> "Source":
        excerpt = textwrap.shorten(
            passage.text, width=EXCERPT_MAX_CHARS, placeholder=EXCERPT_PLACEHOLDER
        )
        return cls(
            source=passage.source,
            title=passage.title,
            heading_path=passage.heading_path,
            score=round(passage.score, SCORE_DECIMALS),
            excerpt=excerpt,
        )


class AskResponse(BaseModel):
    answer: str
    status: AnswerStatus
    search_query: str = Field(
        description=(
            "The question used for retrieval: the question itself without history, otherwise "
            "the language model's standalone rewrite of it."
        )
    )
    sources: list[Source]
