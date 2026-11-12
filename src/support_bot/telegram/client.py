from functools import lru_cache

from aiogram import Bot

from support_bot.config import get_settings


@lru_cache
def get_bot() -> Bot | None:
    """Клиент Bot API; None, если токен не задан. Нужен и боту, и уведомлениям оператору."""
    token = get_settings().tg_bot_token
    return Bot(token.get_secret_value()) if token else None
