import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from support_bot.api import chat, health, pages
from support_bot.cache import redis_client
from support_bot.config import get_settings
from support_bot.db.session import engine
from support_bot.rag.embeddings import get_embedder

logger = logging.getLogger(__name__)


async def _warm_up_embedder() -> None:
    try:
        await get_embedder().embed_query("warmup")
    except Exception:
        logger.exception("Embedding model warm-up failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Модель эмбеддингов грузится несколько секунд — прогреваем в фоне, чтобы не ждал первый вопрос.
    warmup = asyncio.create_task(_warm_up_embedder())
    yield
    warmup.cancel()
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
    app.mount("/static", StaticFiles(directory=pages.STATIC_DIR), name="static")
    return app


app = create_app()
