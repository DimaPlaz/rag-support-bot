import json
from typing import Any

import httpx2 as httpx  # SDK openai построен на httpx2
import openai
import pytest

from support_bot.llm.client import (
    Completion,
    LLMError,
    LLMEvent,
    OpenAICompatLLMClient,
    TextDelta,
)
from support_bot.llm.pricing import TokenUsage


def _sse(*chunks: dict[str, object]) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def _chunk(content: str | None, finish_reason: str | None = None) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "anthropic/claude-opus-5-5",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish_reason}],
    }


USAGE_CHUNK = {
    "id": "chatcmpl-1",
    "object": "chat.completion.chunk",
    "created": 0,
    "model": "anthropic/claude-opus-5-5",
    "choices": [],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 40,
        "total_tokens": 1040,
        "prompt_tokens_details": {"cached_tokens": 300, "cache_creation_tokens": 200},
    },
}


def _client(transport: httpx.MockTransport) -> OpenAICompatLLMClient:
    http_client = httpx.AsyncClient(transport=transport)
    return OpenAICompatLLMClient(
        openai.AsyncOpenAI(
            api_key="test", base_url="https://proxy.test/v1", http_client=http_client
        ),
        model="anthropic/claude-opus-5-5",
        effort="low",
        max_tokens=100,
    )


async def _collect(client: OpenAICompatLLMClient) -> list[LLMEvent]:
    return [
        event
        async for event in client.stream(
            system="system prompt",
            messages=[
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ],
            schema={"type": "object"},
        )
    ]


async def test_streams_text_and_maps_completion() -> None:
    requests: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = _sse(_chunk('{"answer'), _chunk('":"hi"}'), _chunk(None, "stop"), USAGE_CHUNK)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handle))
    events = await _collect(client)

    assert events[:2] == [TextDelta('{"answer'), TextDelta('":"hi"}')]
    assert events[-1] == Completion(
        stop_reason="end_turn",
        text='{"answer":"hi"}',
        model="anthropic/claude-opus-5-5",
        # prompt_tokens у прокси включает и кеш
        usage=TokenUsage(
            input_tokens=500, output_tokens=40, cache_read_tokens=300, cache_write_tokens=200
        ),
    )
    sent = requests[0]
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(
    ("finish_reason", "stop_reason"), [("length", "max_tokens"), ("content_filter", "refusal")]
)
async def test_finish_reason_mapping(finish_reason: str, stop_reason: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        body = _sse(_chunk("{", finish_reason))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handle))
    completion = (await _collect(client))[-1]

    assert isinstance(completion, Completion)
    assert completion.stop_reason == stop_reason


async def test_api_error_becomes_llm_error() -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(403, json={"error": "no"})))

    with pytest.raises(LLMError, match="PermissionDeniedError"):
        await _collect(client)
