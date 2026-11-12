import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from httpx import AsyncClient

from support_bot.db.models import Channel
from support_bot.services.answer import (
    AnswerDelta,
    AnswerDone,
    AnswerEvent,
    Source,
    get_answer_service,
)
from support_bot.services.rate_limit import get_rate_limiter

Override = Callable[[Callable[..., object], object], None]


class FakeService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def answer(
        self, *, session_id: str, channel: Channel, question: str
    ) -> AsyncIterator[AnswerEvent]:
        self.calls.append({"session_id": session_id, "channel": channel, "question": question})
        yield AnswerDelta("Hello")
        if self.fail:
            raise RuntimeError("db is down")
        yield AnswerDelta(" there")
        yield AnswerDone(True, "Hello there", [Source("Pricing")], 7)


class FakeLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def allow(self, *, ip: str, session_id: str) -> bool:
        return self.allowed


def _events(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


PAYLOAD = {"question": "  How much?  ", "session_id": "abc12345"}


async def test_chat_streams_sse_events(client: AsyncClient, override: Override) -> None:
    service = FakeService()
    override(get_answer_service, service)
    override(get_rate_limiter, FakeLimiter())

    response = await client.post("/api/chat", json=PAYLOAD)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _events(response.text) == [
        ("delta", {"text": "Hello"}),
        ("delta", {"text": " there"}),
        (
            "done",
            {
                "answered": True,
                "answer": "Hello there",
                "sources": [{"title": "Pricing", "url": None}],
                "message_id": 7,
            },
        ),
    ]
    assert service.calls == [
        {"session_id": "abc12345", "channel": Channel.WEB, "question": "How much?"}
    ]


async def test_chat_error_event_on_failure(client: AsyncClient, override: Override) -> None:
    override(get_answer_service, FakeService(fail=True))
    override(get_rate_limiter, FakeLimiter())

    response = await client.post("/api/chat", json=PAYLOAD)

    events = _events(response.text)
    assert events[0] == ("delta", {"text": "Hello"})
    assert events[-1][0] == "error"
    assert "operator" in events[-1][1]["message"]


async def test_chat_rate_limited(client: AsyncClient, override: Override) -> None:
    service = FakeService()
    override(get_answer_service, service)
    override(get_rate_limiter, FakeLimiter(allowed=False))

    response = await client.post("/api/chat", json=PAYLOAD)

    assert response.status_code == 429
    assert service.calls == []


async def test_chat_validates_input(client: AsyncClient, override: Override) -> None:
    override(get_answer_service, FakeService())
    override(get_rate_limiter, FakeLimiter())

    for payload in [
        {"question": "   ", "session_id": "abc12345"},
        {"question": "x" * 1001, "session_id": "abc12345"},
        {"question": "Hi", "session_id": "bad id!"},
        {"question": "Hi", "session_id": "short"},
    ]:
        response = await client.post("/api/chat", json=payload)
        assert response.status_code == 422, payload
