import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from support_bot.api import chat, health
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
    logging.basicConfig(level=get_settings().log_level)
    app = FastAPI(title="BoxHub Support Bot", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(chat.router)
    return app


app = create_app()
