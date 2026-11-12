"""Отдельный запуск Telegram-бота: `python -m support_bot.bot`.

Обычно бот работает внутри процесса API (TG_POLLING_ENABLED=true), чтобы не держать
в памяти вторую копию модели эмбеддингов. Этот вход — для запуска бота отдельно.
"""

import asyncio
import logging

from support_bot.config import get_settings
from support_bot.services.answer import get_answer_service
from support_bot.services.rate_limit import get_rate_limiter
from support_bot.telegram.client import get_bot
from support_bot.telegram.dispatcher import create_dispatcher


async def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    bot = get_bot()
    if bot is None:
        raise SystemExit("TG_BOT_TOKEN is not set")
    dispatcher = create_dispatcher(get_answer_service(), get_rate_limiter())
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
