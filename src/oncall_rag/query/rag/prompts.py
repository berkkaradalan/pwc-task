"""Prompt text for the answering model and the fixed answer when nothing relevant is found."""

from collections.abc import Sequence

from oncall_rag.shared.models import Passage

SYSTEM_PROMPT = """\
You answer questions from on-call engineers using only the numbered documentation \
passages in the user message.
- Write plain prose. Use a numbered list only for a step-by-step procedure.
- Be concise, but include every fact from the passages that answers the question, \
such as times, thresholds and follow-up escalations.
- Commands in the passages are examples for the service they name. Unless the question \
is about that service, write them with a placeholder such as `<service>`.
- Never mention the passages, their numbers or "the context". Answer directly.
- If the passages do not contain the answer, say that the documentation does not cover it. \
Do not guess or add steps that are not in the passages."""

NO_CONTEXT_ANSWER = "No relevant documentation was found for this question."

PASSAGE_SEPARATOR = "\n\n"


def build_user_prompt(question: str, passages: Sequence[Passage]) -> str:
    numbered = [_format_passage(number, passage) for number, passage in enumerate(passages, 1)]
    passages_text = PASSAGE_SEPARATOR.join(numbered)
    return f"Passages:\n\n{passages_text}\n\nQuestion: {question}"


def _format_passage(number: int, passage: Passage) -> str:
    location = _location(passage)
    return f"[{number}] {location}\n{passage.text}"


def _location(passage: Passage) -> str:
    if not passage.heading_path:
        return passage.title
    return f"{passage.title} > {passage.heading_path}"
