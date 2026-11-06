"""Core domain objects. Pure data, no I/O."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class Document:
    """A loaded source file.

    `source` is the path relative to DOCS_DIR (POSIX separators). `content` is the body
    without front matter. `content_hash` is the SHA-256 of the raw file bytes.
    """

    source: str
    title: str
    content: str
    content_hash: str
    metadata: Mapping[str, str]

    @property
    def is_markdown(self) -> bool:
        return PurePosixPath(self.source).suffix.lower() == ".md"


@dataclass(frozen=True, slots=True)
class Chunk:
    """A piece of a document. `text` is the raw chunk text used for citations."""

    document_source: str
    chunk_index: int
    heading_path: str
    text: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Passage:
    """A retrieved chunk. `score` is its cosine similarity to the question (1 = same direction)."""

    source: str
    title: str
    heading_path: str
    text: str
    score: float


class ChatRole(StrEnum):
    """Roles a client may send. There is no system role, so clients cannot inject instructions."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """An earlier turn of the conversation."""

    role: ChatRole
    content: str
