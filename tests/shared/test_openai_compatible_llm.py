import asyncio
import json

import httpx2
import openai
import pytest

from oncall_rag.shared.adapters.llm.openai_compatible import OpenAICompatibleLLM
from oncall_rag.shared.errors import LLMError, LLMTimeoutError, LLMUnavailableError

MODEL = "test-model"


def completion_body(content: str | None) -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def make_llm(transport: httpx2.MockTransport, max_concurrency: int = 4) -> OpenAICompatibleLLM:
    client = openai.AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=transport),
    )
    return OpenAICompatibleLLM(client, model=MODEL, max_concurrency=max_concurrency)


async def test_returns_the_answer_and_sends_both_messages() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=completion_body("  Restart the worker.  "))

    llm = make_llm(httpx2.MockTransport(handler))

    answer = await llm.complete("system rules", "the question")

    assert answer == "Restart the worker."
    [payload] = requests
    assert payload["model"] == MODEL
    assert payload["messages"] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "the question"},
    ]


async def test_timeout_raises_llm_timeout_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    llm = make_llm(httpx2.MockTransport(handler))

    with pytest.raises(LLMTimeoutError):
        await llm.complete("system", "user")


async def test_connection_error_raises_llm_unavailable_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    llm = make_llm(httpx2.MockTransport(handler))

    with pytest.raises(LLMUnavailableError):
        await llm.complete("system", "user")


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_overload_and_server_errors_raise_llm_unavailable_error(status_code: int) -> None:
    llm = make_llm(httpx2.MockTransport(lambda _: httpx2.Response(status_code, json={})))

    with pytest.raises(LLMUnavailableError, match=f"HTTP {status_code}"):
        await llm.complete("system", "user")


@pytest.mark.parametrize("status_code", [401, 404])
async def test_rejected_request_raises_llm_error(status_code: int) -> None:
    llm = make_llm(httpx2.MockTransport(lambda _: httpx2.Response(status_code, json={})))

    with pytest.raises(LLMError, match=f"HTTP {status_code}"):
        await llm.complete("system", "user")


async def test_empty_answer_raises_llm_error() -> None:
    llm = make_llm(httpx2.MockTransport(lambda _: httpx2.Response(200, json=completion_body(""))))

    with pytest.raises(LLMError, match="empty answer"):
        await llm.complete("system", "user")


async def test_concurrent_calls_are_limited() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx2.Response(200, json=completion_body("ok"))

    llm = make_llm(httpx2.MockTransport(handler), max_concurrency=2)

    await asyncio.gather(*(llm.complete("system", "user") for _ in range(6)))

    assert peak == 2
