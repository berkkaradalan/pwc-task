from itertools import pairwise

from oncall_rag.ingest.chunker import (
    ChunkingConfig,
    build_embedding_text,
    chunk_document,
    split_text,
)
from oncall_rag.shared.models import Chunk, Document

CONFIG = ChunkingConfig(max_chars=200, overlap_chars=60)


def make_document(content: str, source: str = "runbook.md", title: str = "Runbook") -> Document:
    return Document(
        source=source,
        title=title,
        content=content,
        content_hash="hash",
        metadata={"service": "payments-api"},
    )


def sentence(index: int) -> str:
    return f"Sentence number {index} explains one step of the procedure."


def test_markdown_is_split_on_headings_with_heading_path() -> None:
    content = (
        "Intro before any heading.\n\n"
        "# Payments outage\n\nOverview.\n\n"
        "## Mitigation\n\n"
        "### Rollback\n\nRoll back the release.\n\n"
        "### Scale up\n\nAdd replicas.\n\n"
        "## Verification\n\nCheck the error rate.\n\n"
        "#### Deep detail\n\nH4 stays inside the section."
    )

    chunks = chunk_document(make_document(content), CONFIG)

    assert [(c.heading_path, c.text) for c in chunks] == [
        ("", "Intro before any heading."),
        ("Payments outage", "Overview."),
        ("Payments outage > Mitigation > Rollback", "Roll back the release."),
        ("Payments outage > Mitigation > Scale up", "Add replicas."),
        (
            "Payments outage > Verification",
            "Check the error rate.\n\n#### Deep detail\n\nH4 stays inside the section.",
        ),
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3, 4]
    assert all(c.document_source == "runbook.md" for c in chunks)
    assert all(c.metadata == {"service": "payments-api"} for c in chunks)


def test_hash_lines_inside_code_fences_are_not_headings() -> None:
    content = "## Diagnosis\n\n```bash\n# list pods\nkubectl get pods\n```\n\n## Next\n\nDone."

    chunks = chunk_document(make_document(content), CONFIG)

    assert [c.heading_path for c in chunks] == ["Diagnosis", "Next"]
    assert "# list pods" in chunks[0].text


def test_markdown_without_headings_uses_size_based_chunks() -> None:
    content = "\n\n".join(sentence(i) for i in range(10))

    chunks = chunk_document(make_document(content), CONFIG)

    assert len(chunks) > 1
    assert all(c.heading_path == "" for c in chunks)
    assert all(len(c.text) <= CONFIG.max_chars for c in chunks)


def test_txt_files_are_never_split_on_headings() -> None:
    content = "# Not a heading in a text file\n\nSome text."

    chunks = chunk_document(make_document(content, source="notes.txt"), CONFIG)

    assert len(chunks) == 1
    assert chunks[0].heading_path == ""
    assert chunks[0].text == content


def test_oversized_section_is_split_on_paragraphs_with_overlap() -> None:
    paragraphs = [sentence(i) for i in range(8)]  # ~58 chars each
    content = "## Mitigation\n\n" + "\n\n".join(paragraphs)

    chunks = chunk_document(make_document(content), CONFIG)

    assert len(chunks) > 1
    assert all(c.heading_path == "Mitigation" for c in chunks)
    assert all(len(c.text) <= CONFIG.max_chars for c in chunks)
    # Chunks break on paragraph boundaries: every paragraph is intact in some chunk.
    for paragraph in paragraphs:
        assert any(paragraph in c.text for c in chunks)
    # Consecutive chunks overlap: the next chunk starts with the previous one's tail.
    for previous, current in pairwise(chunks):
        first_paragraph = current.text.split("\n\n")[0]
        assert previous.text.endswith(first_paragraph)


def test_long_paragraph_falls_back_to_sentence_boundaries() -> None:
    paragraph = " ".join(sentence(i) for i in range(10))  # one paragraph, ~590 chars

    pieces = split_text(paragraph, CONFIG)

    assert len(pieces) > 1
    assert all(len(piece) <= CONFIG.max_chars for piece in pieces)
    assert all(piece.endswith(".") for piece in pieces)
    # Overlap uses whole sentences.
    for previous, current in pairwise(pieces):
        assert previous.endswith(current.split(". ")[0] + ".")


def test_long_list_keeps_line_breaks() -> None:
    lines = [f"- step {i}: restart worker pool {i}" for i in range(12)]

    pieces = split_text("\n".join(lines), CONFIG)

    assert len(pieces) > 1
    for piece in pieces:
        assert len(piece) <= CONFIG.max_chars
        assert all(line in lines for line in piece.split("\n"))


def test_single_huge_word_is_hard_split() -> None:
    pieces = split_text("x" * 450, ChunkingConfig(max_chars=200, overlap_chars=0))

    assert [len(piece) for piece in pieces] == [200, 200, 50]


def test_chunking_is_deterministic() -> None:
    content = "## A\n\n" + "\n\n".join(sentence(i) for i in range(20))
    document = make_document(content)

    assert chunk_document(document, CONFIG) == chunk_document(document, CONFIG)


def test_embedding_text_carries_title_and_heading_context() -> None:
    chunk = Chunk("runbook.md", 0, "Mitigation > Rollback", "Roll back the release.")
    no_heading = Chunk("runbook.md", 1, "", "Intro.")

    assert build_embedding_text("Payments outage", chunk) == (
        "Payments outage > Mitigation > Rollback\n\nRoll back the release."
    )
    assert build_embedding_text("Payments outage", no_heading) == "Payments outage\n\nIntro."


def test_embedding_text_does_not_repeat_a_heading_equal_to_the_title() -> None:
    chunk = Chunk("runbook.md", 0, "Payments Outage > Mitigation", "Roll back.")

    assert build_embedding_text("Payments outage", chunk) == (
        "Payments outage > Mitigation\n\nRoll back."
    )
