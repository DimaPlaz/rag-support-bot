import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from support_bot.config import get_settings
from support_bot.services.rate_limit import RateLimiter


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except (RedisError, OSError) as error:
        await client.aclose()
        pytest.skip(f"Redis is not available ({error}); run `docker compose up -d redis`")
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # фиксируем окно, чтобы тест не попал на границу минуты
    monkeypatch.setattr("support_bot.services.rate_limit.time.time", lambda: 1_800_000_000.0)


async def test_session_limit(redis: Redis) -> None:
    limiter = RateLimiter(redis, per_ip=100, per_session=2)
    session = uuid.uuid4().hex

    results = [await limiter.allow(ip=uuid.uuid4().hex, session_id=session) for _ in range(3)]

    assert results == [True, True, False]


async def test_ip_limit_across_sessions(redis: Redis) -> None:
    limiter = RateLimiter(redis, per_ip=2, per_session=100)
    ip = uuid.uuid4().hex

    results = [await limiter.allow(ip=ip, session_id=uuid.uuid4().hex) for _ in range(3)]

    assert results == [True, True, False]


async def test_redis_failure_allows_request() -> None:
    broken = Redis(host="localhost", port=1, socket_connect_timeout=0.2)
    limiter = RateLimiter(broken, per_ip=1, per_session=1)

    assert await limiter.allow(ip="1.2.3.4", session_id="s") is True
    await broken.aclose()
