import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from support_bot.api import admin, chat, health, pages
from support_bot.cache import redis_client
from support_bot.config import get_settings
from support_bot.db.session import engine
from support_bot.rag.embeddings import get_embedder
from support_bot.services.answer import get_answer_service
from support_bot.services.rate_limit import get_rate_limiter
from support_bot.telegram.client import get_bot
from support_bot.telegram.dispatcher import create_dispatcher
from support_bot.web.templating import STATIC_DIR

logger = logging.getLogger(__name__)


async def _warm_up_embedder() -> None:
    try:
        await get_embedder().embed_query("warmup")
    except Exception:
        logger.exception("Embedding model warm-up failed")


async def _run_telegram_polling() -> None:
    bot = get_bot()
    if bot is None:
        logger.warning("TG_POLLING_ENABLED is set, but TG_BOT_TOKEN is empty")
        return
    dispatcher = create_dispatcher(get_answer_service(), get_rate_limiter())
    try:
        await dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False)
    except Exception:
        logger.exception("Telegram polling stopped")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Модель эмбеддингов грузится несколько секунд — прогреваем в фоне, чтобы не ждал первый вопрос.
    warmup = asyncio.create_task(_warm_up_embedder())
    # Бот живёт в процессе API, чтобы не держать в памяти вторую копию модели (~1.6 ГБ).
    polling = (
        asyncio.create_task(_run_telegram_polling()) if get_settings().tg_polling_enabled else None
    )
    yield
    warmup.cancel()
    if polling is not None:
        polling.cancel()
        with suppress(asyncio.CancelledError):
            await polling
    if (bot := get_bot()) is not None:
        await bot.session.close()
    await redis_client.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(title="BoxHub Support Bot", lifespan=lifespan)
    if settings.cors_origins:
        # Нужен, только если виджет подключают на сайте с другим доменом.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["POST"],
            allow_headers=["Content-Type"],
        )
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(pages.router)
    app.include_router(admin.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
