"""Chat completions through an OpenAI-compatible API: OpenRouter in the cloud, Ollama locally."""

import asyncio
from collections.abc import Sequence

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from oncall_rag.shared.errors import ProviderResponseError
from oncall_rag.shared.models import ChatMessage, ChatRole
from oncall_rag.shared.ports import LanguageModel

# Low temperature: answers should follow the passages, not vary between calls.
TEMPERATURE = 0.1
MAX_ANSWER_TOKENS = 512
# The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx; each attempt may use
# the full timeout.
MAX_RETRIES = 1


def build_llm_client(base_url: str, api_key: str, timeout_seconds: float) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=base_url, api_key=api_key, timeout=timeout_seconds, max_retries=MAX_RETRIES
    )


class OpenAICompatibleLLM(LanguageModel):
    def __init__(self, client: openai.AsyncOpenAI, *, model: str, max_concurrency: int) -> None:
        self._client = client
        self._model = model
        self._call_slots = asyncio.Semaphore(max_concurrency)

    async def complete(
        self, system_prompt: str, user_prompt: str, history: Sequence[ChatMessage] = ()
    ) -> str:
        """Errors propagate unchanged, with a note naming this call for the caller's log."""
        messages = _chat_messages(system_prompt, user_prompt, history)
        try:
            return await self._request_answer(messages)
        except (openai.OpenAIError, ProviderResponseError) as error:
            error.add_note(f"LLM call to {self._model}")
            raise

    async def _request_answer(self, messages: list[ChatCompletionMessageParam]) -> str:
        async with self._call_slots:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_ANSWER_TOKENS,
            )
        return _answer_text(completion)


def _chat_messages(
    system_prompt: str, user_prompt: str, history: Sequence[ChatMessage]
) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
    for message in history:
        messages.append(_history_message(message))
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _history_message(message: ChatMessage) -> ChatCompletionMessageParam:
    if message.role is ChatRole.USER:
        return {"role": "user", "content": message.content}
    return {"role": "assistant", "content": message.content}


def _answer_text(completion: ChatCompletion) -> str:
    if completion.choices:
        content = completion.choices[0].message.content
        if content and content.strip():
            return content.strip()
    raise ProviderResponseError("The language model returned an empty answer")
