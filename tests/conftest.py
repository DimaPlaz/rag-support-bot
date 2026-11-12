from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from support_bot.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def override(app: FastAPI) -> Callable[[Callable[..., object], object], None]:
    def _override(dependency: Callable[..., object], value: object) -> None:
        app.dependency_overrides[dependency] = lambda: value

    return _override
