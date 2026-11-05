import hashlib
import logging
from pathlib import Path

import pytest

from oncall_rag.shared.errors import DocsDirectoryNotFoundError
from oncall_rag.ingest.loaders import DocumentRejectedError, load_documents, parse_document


def test_front_matter_is_parsed_into_title_and_metadata(fixture_docs: Path) -> None:
    result = load_documents(fixture_docs)
    document = next(d for d in result.documents if d.source == "payments-outage.md")

    assert document.title == "Payments API outage"
    assert document.metadata == {
        "service": "payments-api",
        "owner_team": "payments-sre",
        "last_reviewed": "2026-09-15",
    }
    assert not document.content.startswith("---")
    assert document.content.startswith("# Payments API outage")
    raw = (fixture_docs / "payments-outage.md").read_bytes()
    assert document.content_hash == hashlib.sha256(raw).hexdigest()


def test_title_falls_back_to_file_name(fixture_docs: Path) -> None:
    result = load_documents(fixture_docs)
    document = next(d for d in result.documents if d.source == "no-front-matter.md")

    assert document.title == "no-front-matter"
    assert document.metadata == {}


def test_sources_are_relative_posix_paths_in_sorted_order(fixture_docs: Path) -> None:
    result = load_documents(fixture_docs)

    assert [d.source for d in result.documents] == [
        "no-front-matter.md",
        "notes/database-failover.txt",
        "payments-outage.md",
    ]


def test_invalid_files_are_skipped_with_a_warning(
    fixture_docs: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        result = load_documents(fixture_docs)

    reasons = {skipped.source: skipped.reason for skipped in result.skipped}
    assert set(reasons) == {"bad-front-matter.md", "diagram.svg", "empty.md", "invalid-utf8.md"}
    assert reasons["bad-front-matter.md"].startswith("malformed front matter")
    assert reasons["diagram.svg"] == "unsupported extension '.svg'"
    assert reasons["empty.md"] == "empty file"
    assert reasons["invalid-utf8.md"].startswith("invalid UTF-8")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 4


def test_hidden_files_are_ignored(fixture_docs: Path) -> None:
    result = load_documents(fixture_docs)

    all_sources = [d.source for d in result.documents] + [s.source for s in result.skipped]
    assert ".hidden.md" not in all_sources


def test_missing_folder_is_a_fatal_error(tmp_path: Path) -> None:
    with pytest.raises(DocsDirectoryNotFoundError):
        load_documents(tmp_path / "does-not-exist")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"---\nlast_reviewed: last week\n---\nBody\n", "'last_reviewed'"),
        (b"---\nservice: [a, b]\n---\nBody\n", "'service' must be text"),
        (b"---\ntitle: Only front matter\n---\n\n", "empty file"),
        (b"   \n\n", "empty file"),
    ],
)
def test_malformed_input_is_rejected(raw: bytes, reason: str) -> None:
    with pytest.raises(DocumentRejectedError, match=reason):
        parse_document("doc.md", raw)


@pytest.mark.parametrize(
    ("raw", "content"),
    [
        (b"---\ntitle: x\nno closing delimiter\n", "---\ntitle: x\nno closing delimiter"),
        (b"---\n- just\n- a list\n---\nBody\n", "Body"),
    ],
)
def test_unusable_front_matter_is_ignored_with_a_warning(
    raw: bytes, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        document = parse_document("doc.md", raw)

    assert document.title == "doc"
    assert document.metadata == {}
    assert document.content == content
    assert "Ignoring front matter in doc.md" in caplog.text


def test_last_reviewed_with_a_time_part_keeps_the_date() -> None:
    raw = b"---\nlast_reviewed: 2026-09-15 10:30:00\n---\nBody\n"

    document = parse_document("doc.md", raw)

    assert document.metadata == {"last_reviewed": "2026-09-15"}


def test_front_matter_is_optional_and_unknown_fields_are_ignored() -> None:
    raw = b"---\nservice: search\nunknown: value\nlast_reviewed: '2026-01-02'\n---\nBody\n"

    document = parse_document("runbooks/search.md", raw)

    assert document.title == "search"
    assert document.metadata == {"service": "search", "last_reviewed": "2026-01-02"}
    assert document.content == "Body"


def test_windows_line_endings_and_bom_are_normalized() -> None:
    document = parse_document("doc.md", "\ufeff---\r\ntitle: T\r\n---\r\nLine one\r\nLine two\r\n".encode())

    assert document.title == "T"
    assert document.content == "Line one\nLine two"
