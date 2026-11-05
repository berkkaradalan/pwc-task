"""Chat completions through an OpenAI-compatible API: OpenRouter in the cloud, Ollama locally."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from oncall_rag.shared.errors import LLMError, LLMTimeoutError, LLMUnavailableError

# Low temperature: answers should follow the passages, not vary between calls.
TEMPERATURE = 0.1
MAX_ANSWER_TOKENS = 512
# The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx; each attempt may use
# the full timeout.
MAX_RETRIES = 1
MAX_ERROR_DETAIL_CHARS = 300


def build_openai_client(base_url: str, api_key: str, timeout_seconds: float) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=base_url, api_key=api_key, timeout=timeout_seconds, max_retries=MAX_RETRIES
    )


class OpenAICompatibleLLM:
    def __init__(self, client: openai.AsyncOpenAI, *, model: str, max_concurrency: int) -> None:
        self._client = client
        self._model = model
        self._call_slots = asyncio.Semaphore(max_concurrency)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        async with self._call_slots, _translated_errors(self._model):
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_ANSWER_TOKENS,
            )
        return _answer_text(completion)


@asynccontextmanager
async def _translated_errors(model: str) -> AsyncGenerator[None]:
    try:
        yield
    except openai.APITimeoutError as error:
        raise LLMTimeoutError(f"The language model '{model}' did not answer in time") from error
    except openai.APIConnectionError as error:
        raise LLMUnavailableError(f"Cannot reach the language model API: {error}") from error
    except (openai.RateLimitError, openai.InternalServerError) as error:
        detail = error.message[:MAX_ERROR_DETAIL_CHARS]
        raise LLMUnavailableError(
            f"The language model API is unavailable (HTTP {error.status_code}): {detail}"
        ) from error
    except openai.APIStatusError as error:
        detail = error.message[:MAX_ERROR_DETAIL_CHARS]
        raise LLMError(
            f"The language model API rejected the request for '{model}' "
            f"(HTTP {error.status_code}): {detail}"
        ) from error


def _answer_text(completion: ChatCompletion) -> str:
    if completion.choices:
        content = completion.choices[0].message.content
        if content and content.strip():
            return content.strip()
    raise LLMError("The language model returned an empty answer")
