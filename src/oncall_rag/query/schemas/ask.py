import textwrap

from pydantic import BaseModel, ConfigDict, Field

from oncall_rag.query.rag.state import AnswerStatus
from oncall_rag.shared.models import Passage

QUESTION_MAX_CHARS = 1000
EXCERPT_MAX_CHARS = 200
EXCERPT_PLACEHOLDER = " ..."
SCORE_DECIMALS = 3


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)


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
    sources: list[Source]
