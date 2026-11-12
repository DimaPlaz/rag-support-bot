import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.cache import get_redis
from support_bot.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter()


async def check_db(session: Annotated[AsyncSession, Depends(get_session)]) -> bool:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        return False
    return True


async def check_redis(redis: Annotated[Redis, Depends(get_redis)]) -> bool:
    try:
        await redis.ping()
    except Exception:
        logger.exception("Redis health check failed")
        return False
    return True


@router.get("/health")
async def health(
    response: Response,
    db_ok: Annotated[bool, Depends(check_db)],
    redis_ok: Annotated[bool, Depends(check_redis)],
) -> dict[str, str]:
    healthy = db_ok and redis_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "error",
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
