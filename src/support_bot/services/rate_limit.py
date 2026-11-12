import logging
import time
from functools import lru_cache

from redis.asyncio import Redis
from redis.exceptions import RedisError

from support_bot.cache import get_redis
from support_bot.config import get_settings

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


class RateLimiter:
    """Фиксированное окно в минуту на Redis: INCR счётчика с TTL."""

    def __init__(self, redis: Redis, *, per_ip: int, per_session: int) -> None:
        self._redis = redis
        self._limits = {"ip": per_ip, "session": per_session}

    async def allow(self, *, ip: str, session_id: str) -> bool:
        window = int(time.time()) // _WINDOW_SECONDS
        keys = {
            scope: f"rate:{scope}:{value}:{window}"
            for scope, value in (("ip", ip), ("session", session_id))
        }
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key in keys.values():
                    pipe.incr(key)
                    pipe.expire(key, _WINDOW_SECONDS)
                results = await pipe.execute()
        except RedisError:
            # Недоступный Redis не должен класть чат: пропускаем запрос и пишем в лог.
            logger.exception("Rate limiter unavailable, request allowed")
            return True
        counts = dict(zip(keys, results[::2], strict=True))
        return all(counts[scope] <= limit for scope, limit in self._limits.items())


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        get_redis(),
        per_ip=settings.rate_limit_ip_per_minute,
        per_session=settings.rate_limit_session_per_minute,
    )
