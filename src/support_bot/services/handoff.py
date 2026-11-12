import logging
import re
from typing import Any, Protocol

from support_bot.db.models import Conversation, HandoffReason, Message

logger = logging.getLogger(__name__)

_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)

_NOT_FOUND = {
    "ru": "Не нашёл ответа в базе знаний — передал ваш вопрос оператору, он скоро свяжется.",
    "en": "I couldn't find the answer in our knowledge base, so I've passed your question "
    "to an operator. They will get back to you soon.",
}
_FAILURE = {
    "ru": "Извините, сейчас не получается ответить. Передал ваш вопрос оператору.",
    "en": "Sorry, I can't answer right now. I've passed your question to an operator.",
}


def handoff_text(question: str, reason: HandoffReason) -> str:
    """Текст для пользователя. Язык определяем по вопросу: LLM здесь могла не вызываться."""
    language = "ru" if _CYRILLIC.search(question) else "en"
    texts = (
        _NOT_FOUND if reason in (HandoffReason.NO_CONTEXT, HandoffReason.NOT_FOUND) else _FAILURE
    )
    return texts[language]


class HandoffNotifier(Protocol):
    async def notify(self, conversation: Conversation, message: Message) -> None: ...


class LogNotifier:
    """Пишет handoff в лог — когда Telegram для оператора не настроен."""

    async def notify(self, conversation: Conversation, message: Message) -> None:
        logger.info(
            "Handoff: session=%s message=%s reason=%s",
            conversation.session_id,
            message.id,
            message.handoff_reason,
        )


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> Any: ...


REASON_LABELS = {
    HandoffReason.NO_CONTEXT: "no relevant documents found",
    HandoffReason.NOT_FOUND: "the answer is not in the knowledge base",
    HandoffReason.REFUSAL: "the model declined to answer",
    HandoffReason.MAX_TOKENS: "the answer was cut off",
    HandoffReason.LLM_ERROR: "the AI service failed",
}


def operator_message(conversation: Conversation, message: Message, base_url: str) -> str:
    reason = (
        REASON_LABELS.get(message.handoff_reason, "unknown")
        if message.handoff_reason
        else "unknown"
    )
    url = f"{base_url.rstrip('/')}/admin/conversations/{conversation.id}"
    question = message.question if len(message.question) <= 1500 else message.question[:1500] + "…"
    return "\n".join(
        [
            f"❓ Unanswered question · {conversation.channel}",
            f"Reason: {reason}",
            "",
            question,
            "",
            f"Open conversation: {url}",
        ]
    )


class TelegramNotifier:
    """Отправляет оператору в Telegram вопрос, на который бот не ответил."""

    def __init__(self, sender: MessageSender, chat_id: int, base_url: str) -> None:
        self._sender = sender
        self._chat_id = chat_id
        self._base_url = base_url

    async def notify(self, conversation: Conversation, message: Message) -> None:
        await self._sender.send_message(
            self._chat_id, operator_message(conversation, message, self._base_url)
        )
