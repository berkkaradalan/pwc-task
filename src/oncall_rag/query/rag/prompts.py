"""Prompt text for the language model and the fixed answer when nothing relevant is found."""

from collections.abc import Sequence

from oncall_rag.shared.models import ChatMessage, Passage

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

CONDENSE_SYSTEM_PROMPT = """\
Rewrite the latest question from an on-call engineer as a standalone question that can be \
understood without the conversation. Keep every service, command and error it refers to. \
If it is already standalone, return it unchanged. Output only the question."""

NO_CONTEXT_ANSWER = "No relevant documentation was found for this question."

PASSAGE_SEPARATOR = "\n\n"
TURN_SEPARATOR = "\n"
# The local model's context (OLLAMA_CONTEXT_LENGTH, 4096 tokens) must also hold the system
# prompt, the passages and the answer. Ollama silently drops the start of a longer prompt,
# which is where the system prompt is.
HISTORY_MAX_TOTAL_CHARS = 4000


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


def build_condense_prompt(history: Sequence[ChatMessage], question: str) -> str:
    turns = [_format_turn(message) for message in history]
    conversation = TURN_SEPARATOR.join(turns)
    return f"Conversation:\n{conversation}\n\nLatest question: {question}"


def _format_turn(message: ChatMessage) -> str:
    return f"{message.role}: {message.content}"


def recent_history(history: Sequence[ChatMessage]) -> list[ChatMessage]:
    """The most recent messages whose combined length is at most HISTORY_MAX_TOTAL_CHARS."""
    kept: list[ChatMessage] = []
    total_chars = 0
    for message in reversed(history):
        total_chars += len(message.content)
        if total_chars > HISTORY_MAX_TOTAL_CHARS:
            break
        kept.append(message)
    kept.reverse()
    return kept
