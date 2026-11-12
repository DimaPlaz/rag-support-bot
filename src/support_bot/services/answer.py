import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from anthropic.types import MessageParam
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from support_bot.config import Settings, get_settings
from support_bot.db import repositories
from support_bot.db.models import (
    Channel,
    Conversation,
    HandoffReason,
    Message,
    MessageStatus,
    SourceType,
)
from support_bot.db.session import session_factory
from support_bot.llm.client import Completion, LLMClient, LLMError, get_llm_client
from support_bot.llm.json_stream import FieldDelta, FieldValue, JsonObjectStream, JsonStreamError
from support_bot.llm.pricing import TokenUsage, calculate_cost
from support_bot.llm.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT, ModelAnswer, build_user_message
from support_bot.rag.embeddings import Embedder, get_embedder
from support_bot.rag.search import FoundChunk, search_chunks
from support_bot.services.handoff import HandoffNotifier, LogNotifier, handoff_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    title: str
    url: str | None = None


@dataclass(frozen=True)
class AnswerDelta:
    text: str


@dataclass(frozen=True)
class AnswerDone:
    answered: bool
    answer: str
    sources: list[Source]
    message_id: int


AnswerEvent = AnswerDelta | AnswerDone


@dataclass
class _Outcome:
    answer: str
    handoff_reason: HandoffReason | None = None
    sources: list[Source] = field(default_factory=list)
    model: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


class AnswerService:
    """Ответ на вопрос по базе знаний — общий для сайта и Telegram."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        llm: LLMClient,
        notifier: HandoffNotifier,
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._embedder = embedder
        self._llm = llm
        self._notifier = notifier
        self._settings = settings

    async def answer(
        self, *, session_id: str, channel: Channel, question: str
    ) -> AsyncIterator[AnswerEvent]:
        async with self._sessions() as session:
            conversation = await repositories.get_or_create_conversation(
                session, session_id, channel
            )
            history = await repositories.recent_messages(
                session, conversation.id, self._settings.history_messages // 2
            )
            chunks = await self._search(session, question, history)
            # Отпускаем соединение с БД на время генерации ответа.
            await session.commit()

            outcome: _Outcome | None = None
            if chunks:
                async for event in self._generate(question, history, chunks):
                    if isinstance(event, AnswerDelta):
                        yield event
                    else:
                        outcome = event
            else:
                outcome = _handoff(question, HandoffReason.NO_CONTEXT)
            assert outcome is not None

            message = await self._save(session, conversation, question, outcome)

        if message.status is MessageStatus.UNANSWERED:
            await self._notify(conversation, message)
        yield AnswerDone(
            answered=message.status is MessageStatus.ANSWERED,
            answer=message.answer,
            sources=outcome.sources,
            message_id=message.id,
        )

    async def _search(
        self, session: AsyncSession, question: str, history: Sequence[Message]
    ) -> list[FoundChunk]:
        # Уточняющий вопрос («а в Northgate?») без предыдущего почти ничего не находит,
        # поэтому ищем и по нему одному, и вместе с прошлым вопросом.
        queries = [question]
        if history:
            queries.append(f"{history[-1].question}\n{question}")
        best: dict[int, FoundChunk] = {}
        for query in queries:
            for chunk in await search_chunks(
                session,
                self._embedder,
                query,
                limit=self._settings.rag_top_k,
                min_similarity=self._settings.rag_min_similarity,
            ):
                current = best.get(chunk.chunk_id)
                if current is None or chunk.similarity > current.similarity:
                    best[chunk.chunk_id] = chunk
        ranked = sorted(best.values(), key=lambda chunk: chunk.similarity, reverse=True)
        return ranked[: self._settings.rag_top_k]

    async def _generate(
        self, question: str, history: Sequence[Message], chunks: Sequence[FoundChunk]
    ) -> AsyncIterator[AnswerDelta | _Outcome]:
        documents = [
            (number, chunk.document_title, chunk.content)
            for number, chunk in enumerate(chunks, start=1)
        ]
        messages: list[MessageParam] = []
        for previous in history:
            messages.append({"role": "user", "content": previous.question})
            messages.append({"role": "assistant", "content": previous.answer})
        messages.append({"role": "user", "content": build_user_message(question, documents)})

        parser: JsonObjectStream | None = JsonObjectStream()
        answer_found: bool | None = None
        buffered: list[str] = []
        completion: Completion | None = None
        try:
            async for event in self._llm.stream(
                system=SYSTEM_PROMPT, messages=messages, schema=ANSWER_SCHEMA
            ):
                if isinstance(event, Completion):
                    completion = event
                    continue
                if parser is None:
                    continue
                try:
                    fields = parser.feed(event.text)
                except JsonStreamError:
                    # Живой показ ломается, но итог всё равно проверим по полному JSON.
                    logger.exception("Cannot parse streamed answer")
                    parser = None
                    continue
                for item in fields:
                    if isinstance(item, FieldValue) and item.name == "answer_found":
                        answer_found = item.value is True
                        if answer_found and buffered:
                            yield AnswerDelta("".join(buffered))
                        buffered.clear()
                    elif isinstance(item, FieldDelta) and item.name == "answer":
                        if answer_found:
                            yield AnswerDelta(item.text)
                        elif answer_found is None:
                            buffered.append(item.text)
        except LLMError:
            logger.exception("LLM call failed")
            yield _handoff(question, HandoffReason.LLM_ERROR)
            return

        if completion is None:
            yield _handoff(question, HandoffReason.LLM_ERROR)
            return
        yield _finish(question, chunks, completion)

    async def _save(
        self,
        session: AsyncSession,
        conversation: Conversation,
        question: str,
        outcome: _Outcome,
    ) -> Message:
        cost = calculate_cost(outcome.model, outcome.usage) if outcome.model else None
        if outcome.model and cost is None:
            logger.warning("No price for model %s, cost is not recorded", outcome.model)
        message = Message(
            conversation_id=conversation.id,
            question=question,
            answer=outcome.answer,
            status=MessageStatus.UNANSWERED if outcome.handoff_reason else MessageStatus.ANSWERED,
            handoff_reason=outcome.handoff_reason,
            sources=[{"title": s.title, "url": s.url} for s in outcome.sources],
            model=outcome.model,
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            cache_read_tokens=outcome.usage.cache_read_tokens,
            cache_write_tokens=outcome.usage.cache_write_tokens,
            cost_usd=cost,
        )
        session.add(message)
        await session.commit()
        return message

    async def _notify(self, conversation: Conversation, message: Message) -> None:
        try:
            await self._notifier.notify(conversation, message)
        except Exception:
            # Сбой уведомления не должен ломать ответ пользователю: вопрос уже в БД как unanswered.
            logger.exception("Handoff notification failed for message %s", message.id)


def _finish(question: str, chunks: Sequence[FoundChunk], completion: Completion) -> _Outcome:
    """Разбирает итог вызова LLM. stop_reason проверяется до чтения ответа."""
    reason: HandoffReason | None = None
    parsed: ModelAnswer | None = None
    if completion.stop_reason == "refusal":
        reason = HandoffReason.REFUSAL
    elif completion.stop_reason == "max_tokens":
        reason = HandoffReason.MAX_TOKENS
    elif completion.stop_reason != "end_turn":
        reason = HandoffReason.LLM_ERROR
    else:
        try:
            parsed = ModelAnswer.model_validate_json(completion.text)
        except ValidationError:
            logger.exception("Model answer does not match the schema")
            reason = HandoffReason.LLM_ERROR
        else:
            if not parsed.answer_found or not parsed.answer.strip():
                reason = HandoffReason.NOT_FOUND

    if reason is not None:
        if reason is not HandoffReason.NOT_FOUND:
            logger.warning("LLM answer rejected: stop_reason=%s", completion.stop_reason)
        outcome = _handoff(question, reason)
    else:
        assert parsed is not None
        outcome = _Outcome(
            answer=parsed.answer.strip(), sources=_sources(chunks, parsed.source_ids)
        )
    outcome.model = completion.model
    outcome.usage = completion.usage
    return outcome


def _handoff(question: str, reason: HandoffReason) -> _Outcome:
    return _Outcome(answer=handoff_text(question, reason), handoff_reason=reason)


def _sources(chunks: Sequence[FoundChunk], source_ids: Sequence[int]) -> list[Source]:
    """Источники из метаданных чанков по номерам, которые назвала модель."""
    picked = [chunks[i - 1] for i in source_ids if 1 <= i <= len(chunks)]
    if not picked:
        picked = [chunks[0]]
    sources: dict[int, Source] = {}
    for chunk in picked:
        url = chunk.document_source if chunk.source_type is SourceType.URL else None
        sources.setdefault(chunk.document_id, Source(chunk.document_title, url))
    return list(sources.values())


@lru_cache
def get_answer_service() -> AnswerService:
    return AnswerService(
        session_factory, get_embedder(), get_llm_client(), LogNotifier(), get_settings()
    )
