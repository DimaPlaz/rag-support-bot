from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from support_bot.config import get_settings
from support_bot.main import create_app


async def test_demo_page_includes_widget(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<script src="/widget.js" defer></script>' in response.text
    assert "fictional company" in response.text
    assert "$85" in response.text


async def test_widget_script_served(client: AsyncClient) -> None:
    response = await client.get("/widget.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "window.BoxHubChat" in response.text


async def test_static_css_served(client: AsyncClient) -> None:
    response = await client.get("/static/demo.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.fixture
async def cors_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("CORS_ORIGINS", '["https://shop.example"]')
    get_settings.cache_clear()
    app = create_app()
    get_settings.cache_clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }


async def test_cors_allows_configured_origin(cors_client: AsyncClient) -> None:
    response = await cors_client.options("/api/chat", headers=_preflight("https://shop.example"))

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://shop.example"


async def test_cors_rejects_other_origin(cors_client: AsyncClient) -> None:
    response = await cors_client.options("/api/chat", headers=_preflight("https://evil.example"))

    assert "access-control-allow-origin" not in response.headers
