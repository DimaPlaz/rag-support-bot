from redis.asyncio import Redis

from support_bot.config import get_settings

redis_client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)


def get_redis() -> Redis:
    return redis_client
