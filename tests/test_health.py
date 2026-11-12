from collections.abc import Callable

import pytest
from httpx import AsyncClient

from support_bot.api.health import check_db, check_redis

Override = Callable[[Callable[..., object], object], None]


async def test_health_ok(client: AsyncClient, override: Override) -> None:
    override(check_db, True)
    override(check_redis, True)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


@pytest.mark.parametrize(
    ("db_ok", "redis_ok", "failed"),
    [(False, True, "db"), (True, False, "redis")],
)
async def test_health_reports_failed_dependency(
    client: AsyncClient, override: Override, db_ok: bool, redis_ok: bool, failed: str
) -> None:
    override(check_db, db_ok)
    override(check_redis, redis_ok)

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body[failed] == "error"
