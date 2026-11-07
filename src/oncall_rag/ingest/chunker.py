"""Splitting documents into chunks. Pure, deterministic functions without I/O."""

from dataclasses import dataclass
from itertools import groupby

from langchain_text_splitters import (
    ExperimentalMarkdownSyntaxTextSplitter,
    RecursiveCharacterTextSplitter,
)

from oncall_rag.shared.models import Chunk, Document

HEADING_SEPARATOR = " > "
HEADING_LEVELS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
# Paragraphs, then lines, then sentences; "" is the last-resort hard split.
TEXT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
BLOCK_SEPARATOR = "\n\n"
# Lists other files only: nothing to answer from, and it pollutes retrieval for short queries.
NAVIGATION_HEADING = "Related runbooks"


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int
    overlap_chars: int


@dataclass(frozen=True, slots=True)
class _Section:
    heading_path: str
    text: str


def chunk_document(document: Document, config: ChunkingConfig) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in _sections(document):
        for text in split_text(section.text, config):
            chunk = Chunk(
                document_source=document.source,
                chunk_index=len(chunks),
                heading_path=section.heading_path,
                text=text,
                metadata=dict(document.metadata),
            )
            chunks.append(chunk)
    return chunks


def build_embedding_text(title: str, chunk: Chunk) -> str:
    """Text sent to the embedder: '<title> > <heading_path>' context, then the raw text.

    A leading heading that repeats the title (a common H1) is not repeated in the context.
    """
    headings = chunk.heading_path.split(HEADING_SEPARATOR) if chunk.heading_path else []
    if headings and headings[0].casefold() == title.casefold():
        headings = headings[1:]
    context = HEADING_SEPARATOR.join([title, *headings])
    return f"{context}\n\n{chunk.text}"


def split_text(text: str, config: ChunkingConfig) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.max_chars,
        chunk_overlap=config.overlap_chars,
        separators=TEXT_SEPARATORS,
        keep_separator="end",
    )
    return splitter.split_text(text)


def _sections(document: Document) -> list[_Section]:
    if not document.is_markdown:
        return [_Section(heading_path="", text=document.content)]
    return _markdown_sections(document.content)


def _markdown_sections(text: str) -> list[_Section]:
    """Split on H1-H3 headings, keeping whitespace and code fences intact.

    The splitter emits code fences as separate blocks; blocks under the same heading are
    joined again so a code sample stays with the text that explains it.
    """
    splitter = ExperimentalMarkdownSyntaxTextSplitter(HEADING_LEVELS, strip_headers=True)
    blocks = splitter.split_text(text)
    sections: list[_Section] = []
    grouped = groupby(blocks, key=lambda block: _heading_path(block.metadata))
    for heading_path, same_heading in grouped:
        if _is_navigation_only(heading_path):
            continue
        body = BLOCK_SEPARATOR.join(block.page_content.strip() for block in same_heading)
        sections.append(_Section(heading_path=heading_path, text=body))
    return sections


def _heading_path(metadata: dict[str, str]) -> str:
    headings = [metadata[key] for _, key in HEADING_LEVELS if key in metadata]
    return HEADING_SEPARATOR.join(headings)


def _is_navigation_only(heading_path: str) -> bool:
    own_heading = heading_path.rsplit(HEADING_SEPARATOR, maxsplit=1)[-1]
    return own_heading.casefold() == NAVIGATION_HEADING.casefold()
