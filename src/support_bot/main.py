import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from support_bot.api import health
from support_bot.cache import redis_client
from support_bot.config import get_settings
from support_bot.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await redis_client.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    logging.basicConfig(level=get_settings().log_level)
    app = FastAPI(title="BoxHub Support Bot", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
