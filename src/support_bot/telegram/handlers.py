import logging
import secrets
import time
from typing import Any, Protocol

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from support_bot.db.models import Channel
from support_bot.services.answer import AnswerDelta, AnswerDone, AnswerService
from support_bot.services.rate_limit import RateLimiter
from support_bot.telegram import texts

logger = logging.getLogger(__name__)

MAX_QUESTION = 1000
MAX_MESSAGE = 4096
DRAFT_INTERVAL_SECONDS = 0.5
EXAMPLE_PREFIX = "example:"


class TelegramSender(Protocol):
    async def send_chat_action(self, chat_id: int, action: str) -> Any: ...

    async def send_message_draft(
        self, chat_id: int, draft_id: int, *, text: str | None = None
    ) -> Any: ...

    async def send_message(self, chat_id: int, text: str) -> Any: ...


def create_router() -> Router:
    router = Router(name="support")
    # В группах бот молчит: поддержка — только в личных сообщениях.
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_start, CommandStart())
    router.message.register(on_chat_id, Command("id"))
    router.callback_query.register(on_example, F.data.startswith(EXAMPLE_PREFIX))
    router.message.register(on_question, F.text)
    router.message.register(on_other)
    return router


def examples_keyboard(language_code: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=question, callback_data=f"{EXAMPLE_PREFIX}{index}")]
            for index, question in enumerate(texts.examples(language_code))
        ]
    )


async def on_start(message: Message) -> None:
    language_code = message.from_user.language_code if message.from_user else None
    await message.answer(
        texts.text("greeting", language_code), reply_markup=examples_keyboard(language_code)
    )


async def on_chat_id(message: Message) -> None:
    language_code = message.from_user.language_code if message.from_user else None
    await message.answer(texts.text("chat_id", language_code).format(chat_id=message.chat.id))


async def on_example(
    callback: CallbackQuery, bot: Bot, answers: AnswerService, limiter: RateLimiter
) -> None:
    await callback.answer()
    language_code = callback.from_user.language_code
    questions = texts.examples(language_code)
    index = (callback.data or "").removeprefix(EXAMPLE_PREFIX)
    if callback.message is None or not index.isdigit() or int(index) >= len(questions):
        return
    question = questions[int(index)]
    chat_id = callback.message.chat.id
    # Показываем, какой вопрос задан кнопкой: иначе в чате будет ответ без вопроса.
    await bot.send_message(chat_id, f"› {question}")
    await reply_to_question(
        bot,
        chat_id=chat_id,
        user_id=callback.from_user.id,
        question=question,
        language_code=language_code,
        answers=answers,
        limiter=limiter,
    )


async def on_question(
    message: Message, bot: Bot, answers: AnswerService, limiter: RateLimiter
) -> None:
    if message.from_user is None or message.text is None:
        return
    await reply_to_question(
        bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        question=message.text,
        language_code=message.from_user.language_code,
        answers=answers,
        limiter=limiter,
    )


async def on_other(message: Message) -> None:
    language_code = message.from_user.language_code if message.from_user else None
    await message.answer(texts.text("text_only", language_code))


async def reply_to_question(
    sender: TelegramSender,
    *,
    chat_id: int,
    user_id: int,
    question: str,
    language_code: str | None,
    answers: AnswerService,
    limiter: RateLimiter,
    draft_interval: float = DRAFT_INTERVAL_SECONDS,
) -> None:
    """Ответ в Telegram: текст стримится черновиком, затем уходит финальным сообщением."""
    question = question.strip()
    if not question:
        return
    if len(question) > MAX_QUESTION:
        await sender.send_message(chat_id, texts.text("too_long", language_code))
        return
    session_id = f"tg-{chat_id}"
    if not await limiter.allow(ip=f"tg:{user_id}", session_id=session_id):
        await sender.send_message(chat_id, texts.text("rate_limited", language_code))
        return

    await sender.send_chat_action(chat_id, ChatAction.TYPING)
    draft_id = secrets.randbelow(2**31 - 1) + 1
    drafts_enabled = True
    last_draft = 0.0
    streamed = ""
    done: AnswerDone | None = None
    try:
        async for event in answers.answer(
            session_id=session_id, channel=Channel.TELEGRAM, question=question
        ):
            if not isinstance(event, AnswerDelta):
                done = event
                continue
            streamed += event.text
            now = time.monotonic()
            # Черновик обновляем не чаще раза в draft_interval, чтобы не упереться в лимиты API.
            if drafts_enabled and now - last_draft >= draft_interval:
                last_draft = now
                drafts_enabled = await _send_draft(sender, chat_id, draft_id, streamed)
    except Exception:
        logger.exception("Telegram answer failed for chat %s", chat_id)
        done = None

    if done is None:
        await sender.send_message(chat_id, texts.text("error", language_code))
        return
    await sender.send_message(chat_id, format_answer(done, language_code))


async def _send_draft(sender: TelegramSender, chat_id: int, draft_id: int, text: str) -> bool:
    try:
        await sender.send_message_draft(chat_id, draft_id, text=text[:MAX_MESSAGE])
    except TelegramAPIError:
        logger.warning("Message drafts are unavailable, sending only the final answer")
        return False
    return True


def format_answer(done: AnswerDone, language_code: str | None) -> str:
    text = done.answer
    if done.answered and done.sources:
        titles = [f"{s.title} ({s.url})" if s.url else s.title for s in done.sources]
        text += f"\n\n📄 {texts.text('sources', language_code)}: {', '.join(titles)}"
    if len(text) > MAX_MESSAGE:
        text = text[: MAX_MESSAGE - 1] + "…"
    return text
