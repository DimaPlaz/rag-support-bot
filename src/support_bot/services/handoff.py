import logging
import re
from typing import Protocol

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
    """Пишет handoff в лог. Отправка оператору в Telegram появится на этапе 5."""

    async def notify(self, conversation: Conversation, message: Message) -> None:
        logger.info(
            "Handoff: session=%s message=%s reason=%s",
            conversation.session_id,
            message.id,
            message.handoff_reason,
        )
