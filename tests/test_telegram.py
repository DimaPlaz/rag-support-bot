from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessageDraft

from support_bot.config import Settings
from support_bot.db.models import Channel, Conversation, HandoffReason, Message, MessageStatus
from support_bot.services import answer as answer_module
from support_bot.services.answer import AnswerDelta, AnswerDone, AnswerEvent, Source
from support_bot.services.handoff import LogNotifier, TelegramNotifier, operator_message
from support_bot.telegram.handlers import (
    EXAMPLE_PREFIX,
    MAX_MESSAGE,
    examples_keyboard,
    format_answer,
    reply_to_question,
)


class FakeSender:
    def __init__(self, *, drafts_fail: bool = False) -> None:
        self.drafts_fail = drafts_fail
        self.actions: list[tuple[int, str]] = []
        self.drafts: list[tuple[int, int, str | None]] = []
        self.messages: list[tuple[int, str]] = []

    async def send_chat_action(self, chat_id: int, action: str) -> Any:
        self.actions.append((chat_id, action))

    async def send_message_draft(
        self, chat_id: int, draft_id: int, *, text: str | None = None
    ) -> Any:
        if self.drafts_fail:
            raise TelegramBadRequest(SendMessageDraft(chat_id=chat_id, draft_id=draft_id), "nope")
        self.drafts.append((chat_id, draft_id, text))

    async def send_message(self, chat_id: int, text: str) -> Any:
        self.messages.append((chat_id, text))


class FakeAnswers:
    def __init__(self, events: list[AnswerEvent], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def answer(
        self, *, session_id: str, channel: Channel, question: str
    ) -> AsyncIterator[AnswerEvent]:
        self.calls.append({"session_id": session_id, "channel": channel, "question": question})
        for event in self.events:
            yield event
        if self.fail:
            raise RuntimeError("db is down")


class FakeLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    async def allow(self, *, ip: str, session_id: str) -> bool:
        self.calls.append((ip, session_id))
        return self.allowed


ANSWERED = [
    AnswerDelta("An M unit "),
    AnswerDelta("costs $85."),
    AnswerDone(True, "An M unit costs $85.", [Source("Pricing")], 1),
]


async def _reply(
    sender: FakeSender,
    answers: FakeAnswers,
    limiter: FakeLimiter | None = None,
    question: str = "How much is M?",
    language_code: str | None = "en",
) -> None:
    await reply_to_question(
        sender,
        chat_id=42,
        user_id=7,
        question=question,
        language_code=language_code,
        answers=answers,  # type: ignore[arg-type]
        limiter=limiter or FakeLimiter(),  # type: ignore[arg-type]
        draft_interval=0,
    )


async def test_answer_streams_drafts_then_final_message() -> None:
    sender = FakeSender()
    answers = FakeAnswers(ANSWERED)

    await _reply(sender, answers)

    assert answers.calls == [
        {"session_id": "tg-42", "channel": Channel.TELEGRAM, "question": "How much is M?"}
    ]
    assert sender.actions == [(42, "typing")]
    assert [text for _, _, text in sender.drafts] == ["An M unit ", "An M unit costs $85."]
    assert len({draft_id for _, draft_id, _ in sender.drafts}) == 1
    assert sender.messages == [(42, "An M unit costs $85.\n\n📄 Sources: Pricing")]


async def test_drafts_unavailable_falls_back_to_final_message() -> None:
    sender = FakeSender(drafts_fail=True)

    await _reply(sender, FakeAnswers(ANSWERED))

    assert sender.drafts == []
    assert sender.messages == [(42, "An M unit costs $85.\n\n📄 Sources: Pricing")]


async def test_handoff_answer_has_no_sources_line() -> None:
    sender = FakeSender()
    done = AnswerDone(False, "Passed to an operator.", [], 2)

    await _reply(sender, FakeAnswers([done]))

    assert sender.messages == [(42, "Passed to an operator.")]


async def test_rate_limited_user_gets_message_without_answer() -> None:
    sender = FakeSender()
    answers = FakeAnswers(ANSWERED)
    limiter = FakeLimiter(allowed=False)

    await _reply(sender, answers, limiter)

    assert answers.calls == []
    assert limiter.calls == [("tg:7", "tg-42")]
    assert "too quickly" in sender.messages[0][1]


async def test_too_long_and_empty_questions() -> None:
    sender = FakeSender()
    answers = FakeAnswers(ANSWERED)

    await _reply(sender, answers, question="x" * 1001, language_code="ru")
    await _reply(sender, answers, question="   ")

    assert answers.calls == []
    assert sender.messages == [(42, "Пожалуйста, уложите вопрос в 1000 символов.")]


async def test_service_failure_sends_polite_error() -> None:
    sender = FakeSender()

    await _reply(sender, FakeAnswers([AnswerDelta("Partial")], fail=True), language_code="ru")

    assert sender.messages == [
        (42, "Извините, что-то пошло не так. Попробуйте ещё раз чуть позже.")
    ]


def test_format_answer_with_url_and_limit() -> None:
    done = AnswerDone(True, "y" * 5000, [Source("Tips", "https://boxhub.example/tips")], 3)

    text = format_answer(done, "en")

    assert len(text) == MAX_MESSAGE
    assert text.endswith("…")
    short = format_answer(AnswerDone(True, "Ok", done.sources, 3), "ru")
    assert short == "Ok\n\n📄 Источники: Tips (https://boxhub.example/tips)"


def test_examples_keyboard_follows_language() -> None:
    keyboard = examples_keyboard("ru-RU")

    buttons = [row[0] for row in keyboard.inline_keyboard]
    assert buttons[0].text == "Сколько стоит бокс среднего размера?"
    assert [b.callback_data for b in buttons] == [f"{EXAMPLE_PREFIX}{i}" for i in range(3)]
    assert examples_keyboard(None).inline_keyboard[0][0].text == "How much is a medium unit?"


def _handoff_message() -> tuple[Conversation, Message]:
    conversation = Conversation(id=5, session_id="abc12345", channel=Channel.WEB)
    message = Message(
        id=9,
        conversation_id=5,
        question="Do you deliver pizza?",
        answer="Passed to an operator.",
        status=MessageStatus.UNANSWERED,
        handoff_reason=HandoffReason.NOT_FOUND,
    )
    return conversation, message


def test_operator_message_contents() -> None:
    conversation, message = _handoff_message()

    text = operator_message(conversation, message, "https://bot.example/")

    assert text == (
        "❓ Unanswered question · web\n"
        "Reason: the answer is not in the knowledge base\n\n"
        "Do you deliver pizza?\n\n"
        "Open conversation: https://bot.example/admin/conversations/5"
    )


async def test_telegram_notifier_sends_to_operator_chat() -> None:
    sender = FakeSender()
    conversation, message = _handoff_message()

    await TelegramNotifier(sender, 1001, "http://localhost:8000").notify(conversation, message)

    assert len(sender.messages) == 1
    chat_id, text = sender.messages[0]
    assert chat_id == 1001
    assert "Do you deliver pizza?" in text


@pytest.mark.parametrize(
    ("has_bot", "chat_id", "expected"),
    [(True, 1001, TelegramNotifier), (True, None, LogNotifier), (False, 1001, LogNotifier)],
)
def test_notifier_selection(
    monkeypatch: pytest.MonkeyPatch, has_bot: bool, chat_id: int | None, expected: type
) -> None:
    monkeypatch.setattr(answer_module, "get_bot", lambda: FakeSender() if has_bot else None)

    notifier = answer_module._notifier(Settings(tg_operator_chat_id=chat_id))

    assert isinstance(notifier, expected)
