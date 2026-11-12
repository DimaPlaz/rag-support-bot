import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from anthropic.types import MessageParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from support_bot.config import Settings
from support_bot.db.models import (
    Channel,
    Conversation,
    HandoffReason,
    Message,
    MessageStatus,
    SourceType,
)
from support_bot.llm.client import (
    AnthropicLLMClient,
    Completion,
    LLMError,
    LLMEvent,
    TextDelta,
)
from support_bot.llm.pricing import TokenUsage
from support_bot.rag.loaders import LoadedDocument
from support_bot.rag.search import search_chunks
from support_bot.services.answer import AnswerDelta, AnswerDone, AnswerService, Source
from support_bot.services.ingest import ingest_document
from tests.conftest import VocabEmbedder

VOCABULARY = ["pricing", "cost", "unit", "month", "gasoline", "prohibited", "northgate"]
USAGE = TokenUsage(input_tokens=1000, output_tokens=200)


def _answer_json(found: bool, answer: str, ids: list[int], *, reverse: bool = False) -> str:
    data: dict[str, Any] = {"answer_found": found, "answer": answer, "source_ids": ids}
    if reverse:
        data = dict(reversed(data.items()))
    return json.dumps(data, ensure_ascii=False)


class FakeLLM:
    def __init__(
        self,
        output: str = "",
        *,
        stop_reason: str = "end_turn",
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.stop_reason = stop_reason
        self.error = error
        self.calls: list[list[MessageParam]] = []

    async def stream(
        self, *, system: str, messages: list[MessageParam], schema: dict[str, Any]
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append(messages)
        if self.error:
            raise self.error
        for start in range(0, len(self.output), 7):
            yield TextDelta(self.output[start : start + 7])
        yield Completion(self.stop_reason, self.output, "claude-opus-5-5", USAGE)


class RecordingNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[Message] = []
        self.fail = fail

    async def notify(self, conversation: Conversation, message: Message) -> None:
        if self.fail:
            raise RuntimeError("telegram is down")
        self.messages.append(message)


@pytest.fixture
def embedder() -> VocabEmbedder:
    return VocabEmbedder(VOCABULARY)


@pytest.fixture
async def knowledge_base(db_session: AsyncSession, embedder: VocabEmbedder) -> None:
    for source, title, text in [
        ("pricing.md", "Pricing", "# Pricing\n\nAn M unit costs $99 per month."),
        ("rules.md", "Storage rules", "# Storage rules\n\nGasoline is prohibited."),
    ]:
        document = LoadedDocument(source, SourceType.MARKDOWN, title, text)
        await ingest_document(db_session, embedder, document, chunk_size=500, chunk_overlap=0)


def _service(
    sessions: async_sessionmaker[AsyncSession],
    embedder: VocabEmbedder,
    llm: FakeLLM,
    notifier: RecordingNotifier | None = None,
) -> AnswerService:
    settings = Settings(rag_top_k=3, rag_min_similarity=0.35, history_messages=6)
    return AnswerService(sessions, embedder, llm, notifier or RecordingNotifier(), settings)


async def _ask(
    service: AnswerService, question: str, session_id: str = "session-1"
) -> tuple[str, AnswerDone]:
    streamed = ""
    done: AnswerDone | None = None
    async for event in service.answer(
        session_id=session_id, channel=Channel.WEB, question=question
    ):
        if isinstance(event, AnswerDelta):
            streamed += event.text
        else:
            done = event
    assert done is not None
    return streamed, done


async def _saved(session: AsyncSession, message_id: int) -> Message:
    message = await session.get(Message, message_id)
    assert message is not None
    return message


pytestmark = pytest.mark.usefixtures("knowledge_base")


async def test_search_filters_by_threshold(
    db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    found = await search_chunks(db_session, embedder, "unit cost?", limit=5, min_similarity=0.35)

    assert [chunk.document_title for chunk in found] == ["Pricing"]
    assert found[0].similarity > 0.35


async def test_no_context_hands_off_without_llm(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    llm = FakeLLM()
    notifier = RecordingNotifier()

    streamed, done = await _ask(
        _service(db_sessions, embedder, llm, notifier), "Do you sell pizza?"
    )

    assert llm.calls == []
    assert streamed == ""
    assert not done.answered
    assert "operator" in done.answer
    message = await _saved(db_session, done.message_id)
    assert message.status is MessageStatus.UNANSWERED
    assert message.handoff_reason is HandoffReason.NO_CONTEXT
    assert message.cost_usd is None
    assert [m.id for m in notifier.messages] == [done.message_id]


async def test_answer_is_streamed_with_sources_and_cost(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(True, "An M unit costs $99 per month.", [1]))

    streamed, done = await _ask(_service(db_sessions, embedder, llm), "How much does a unit cost?")

    assert streamed == "An M unit costs $99 per month."
    assert done.answered
    assert done.answer == streamed
    assert done.sources == [Source("Pricing")]
    message = await _saved(db_session, done.message_id)
    assert message.status is MessageStatus.ANSWERED
    assert message.sources == [{"title": "Pricing", "url": None}]
    assert (message.input_tokens, message.output_tokens) == (1000, 200)
    assert message.cost_usd == Decimal("0.008000")
    assert message.model == "claude-opus-5-5"


async def test_reversed_field_order_still_streams(
    db_sessions: async_sessionmaker[AsyncSession], embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(True, "It costs $99.", [1], reverse=True))

    streamed, done = await _ask(_service(db_sessions, embedder, llm), "Unit cost?")

    assert streamed == "It costs $99."
    assert done.answered


async def test_model_without_answer_hands_off(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(False, "", []))

    streamed, done = await _ask(_service(db_sessions, embedder, llm), "Сколько стоит unit на год?")

    assert streamed == ""
    assert not done.answered
    assert "оператору" in done.answer
    message = await _saved(db_session, done.message_id)
    assert message.handoff_reason is HandoffReason.NOT_FOUND
    assert message.cost_usd == Decimal("0.008000")


@pytest.mark.parametrize(
    ("stop_reason", "reason"),
    [
        ("refusal", HandoffReason.REFUSAL),
        ("max_tokens", HandoffReason.MAX_TOKENS),
        ("model_context_window_exceeded", HandoffReason.LLM_ERROR),
    ],
)
async def test_bad_stop_reason_hands_off_even_after_partial_stream(
    db_sessions: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    embedder: VocabEmbedder,
    stop_reason: str,
    reason: HandoffReason,
) -> None:
    partial = '{"answer_found": true, "answer": "An M unit co'
    llm = FakeLLM(partial, stop_reason=stop_reason)

    streamed, done = await _ask(_service(db_sessions, embedder, llm), "Unit cost?")

    assert streamed == "An M unit co"
    # клиент заменяет показанный кусок финальным текстом из done
    assert not done.answered
    assert "operator" in done.answer
    message = await _saved(db_session, done.message_id)
    assert message.handoff_reason is reason
    assert message.output_tokens == 200


async def test_llm_error_hands_off(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(error=LLMError("RateLimitError"))

    _, done = await _ask(_service(db_sessions, embedder, llm), "Unit cost?")

    assert not done.answered
    message = await _saved(db_session, done.message_id)
    assert message.handoff_reason is HandoffReason.LLM_ERROR
    assert message.model is None


async def test_output_not_matching_schema_hands_off(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    llm = FakeLLM('{"answer": "x"}')

    _, done = await _ask(_service(db_sessions, embedder, llm), "Unit cost?")

    message = await _saved(db_session, done.message_id)
    assert message.handoff_reason is HandoffReason.LLM_ERROR


async def test_unknown_source_ids_fall_back_to_top_chunk(
    db_sessions: async_sessionmaker[AsyncSession], embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(True, "It costs $99.", [7, 0]))

    _, done = await _ask(_service(db_sessions, embedder, llm), "Unit cost?")

    assert done.sources == [Source("Pricing")]


async def test_history_is_sent_and_follow_up_finds_context(
    db_sessions: async_sessionmaker[AsyncSession], embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(True, "It costs $99.", [1]))
    service = _service(db_sessions, embedder, llm)
    await _ask(service, "How much does a unit cost?")

    # сам по себе вопрос ничего не находит, но вместе с предыдущим — находит
    _, done = await _ask(service, "And at Northgate?")

    assert done.answered
    messages = llm.calls[-1]
    assert messages[0] == {"role": "user", "content": "How much does a unit cost?"}
    assert messages[1] == {"role": "assistant", "content": "It costs $99."}
    assert "And at Northgate?" in str(messages[2]["content"])


async def test_sessions_do_not_share_history(
    db_sessions: async_sessionmaker[AsyncSession], embedder: VocabEmbedder
) -> None:
    llm = FakeLLM(_answer_json(True, "It costs $99.", [1]))
    service = _service(db_sessions, embedder, llm)
    await _ask(service, "Unit cost?", session_id="session-a")

    await _ask(service, "Unit cost per month?", session_id="session-b")

    assert len(llm.calls[-1]) == 1


async def test_notifier_failure_does_not_break_answer(
    db_sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession, embedder: VocabEmbedder
) -> None:
    service = _service(db_sessions, embedder, FakeLLM(), RecordingNotifier(fail=True))

    _, done = await _ask(service, "Do you sell pizza?")

    assert not done.answered
    assert await db_session.scalar(select(Message.status)) is MessageStatus.UNANSWERED


class _BrokenMessages:
    def stream(self, **kwargs: Any) -> Any:
        raise TypeError("Could not resolve authentication method")


class _BrokenAnthropic:
    messages = _BrokenMessages()


async def test_any_llm_client_failure_becomes_llm_error() -> None:
    client = AnthropicLLMClient(
        _BrokenAnthropic(),  # type: ignore[arg-type]
        model="claude-opus-5-5",
        effort="low",
        max_tokens=100,
    )

    with pytest.raises(LLMError):
        async for _ in client.stream(system="s", messages=[], schema={}):
            pass
