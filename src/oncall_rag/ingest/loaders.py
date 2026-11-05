"""Reading and validating source documents (.md and .txt) from a folder."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import frontmatter
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from oncall_rag.shared.errors import DocsDirectoryNotFoundError
from oncall_rag.shared.models import Document

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".md", ".txt"})
NO_EXTENSION_LABEL = "(none)"


@dataclass(frozen=True, slots=True)
class SkippedFile:
    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class LoadResult:
    documents: list[Document]
    skipped: list[SkippedFile]


class DocumentRejectedError(ValueError):
    """A file cannot be used as a document. The message is the skip reason."""


class _FrontMatter(BaseModel):
    """The front matter fields we use; other fields are ignored."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, coerce_numbers_to_str=True)

    title: str | None = None
    service: str | None = None
    owner_team: str | None = None
    last_reviewed: date | None = None

    @field_validator("title", "service", "owner_team")
    @classmethod
    def _blank_means_missing(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value

    @field_validator("last_reviewed", mode="before")
    @classmethod
    def _date_part_only(cls, value: object) -> object:
        # YAML turns "2026-09-15 10:30:00" into a datetime; only the day matters here.
        if isinstance(value, datetime):
            return value.date()
        return value


_EXPECTED_FIELD_TYPES = {
    "title": "text",
    "service": "text",
    "owner_team": "text",
    "last_reviewed": "a YYYY-MM-DD date",
}


def load_documents(docs_dir: Path) -> LoadResult:
    """Load every supported file under `docs_dir`, recursively and sorted by source.

    Hidden files and folders are ignored. Invalid files are skipped with a WARNING
    instead of failing the run.
    """
    if not docs_dir.is_dir():
        raise DocsDirectoryNotFoundError(f"Documents folder not found: {docs_dir}")

    documents: list[Document] = []
    skipped: list[SkippedFile] = []
    for path in _visible_files(docs_dir):
        source = path.relative_to(docs_dir).as_posix()
        try:
            documents.append(_load_file(path, source))
        except DocumentRejectedError as error:
            logger.warning("Skipping %s: %s", source, error)
            skipped.append(SkippedFile(source=source, reason=str(error)))
    return LoadResult(documents=documents, skipped=skipped)


def parse_document(source: str, raw: bytes) -> Document:
    """Build a Document from raw file bytes. Raises DocumentRejectedError if unusable."""
    post = _read_post(source, raw)
    body = post.content.strip()
    if not body:
        raise DocumentRejectedError("empty file")

    fields = _validate_front_matter(post.metadata)
    title = fields.title if fields.title is not None else PurePosixPath(source).stem
    metadata = fields.model_dump(mode="json", exclude={"title"}, exclude_none=True)
    return Document(
        source=source,
        title=title,
        content=body,
        content_hash=hashlib.sha256(raw).hexdigest(),
        metadata=metadata,
    )


def _visible_files(docs_dir: Path) -> list[Path]:
    files = [path for path in docs_dir.rglob("*") if path.is_file()]
    visible = [path for path in files if not _is_hidden(path, docs_dir)]
    return sorted(visible, key=lambda path: path.relative_to(docs_dir).as_posix())


def _is_hidden(path: Path, docs_dir: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(docs_dir).parts)


def _load_file(path: Path, source: str) -> Document:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        extension = path.suffix if path.suffix else NO_EXTENSION_LABEL
        raise DocumentRejectedError(f"unsupported extension '{extension}'")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DocumentRejectedError(f"unreadable file ({error.strerror})") from error
    return parse_document(source, raw)


def _read_post(source: str, raw: bytes) -> frontmatter.Post:
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        post = frontmatter.loads(text)
    except UnicodeDecodeError as error:
        raise DocumentRejectedError(f"invalid UTF-8 (byte offset {error.start})") from error
    except yaml.YAMLError as error:
        problem = _yaml_problem(error)
        raise DocumentRejectedError(f"malformed front matter ({problem})") from error

    # python-frontmatter ignores an unclosed block or a non-mapping silently; say so.
    if frontmatter.checks(text) and not post.metadata:
        logger.warning("Ignoring front matter in %s: no YAML mapping between '---' lines", source)
    return post


def _validate_front_matter(metadata: dict[str, object]) -> _FrontMatter:
    try:
        return _FrontMatter.model_validate(metadata)
    except ValidationError as error:
        field = str(error.errors()[0]["loc"][0])
        expected = _EXPECTED_FIELD_TYPES[field]
        raise DocumentRejectedError(f"malformed front matter ('{field}' must be {expected})") from error


def _yaml_problem(error: yaml.YAMLError) -> str:
    if isinstance(error, yaml.MarkedYAMLError) and error.problem:
        return error.problem
    return "invalid YAML"
