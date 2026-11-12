from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from support_bot.api.admin import get_http_client
from support_bot.config import get_settings
from support_bot.db.models import (
    Channel,
    Conversation,
    HandoffReason,
    Message,
    MessageStatus,
    SourceType,
)
from support_bot.db.session import get_session
from support_bot.main import create_app
from support_bot.rag.embeddings import get_embedder
from support_bot.rag.loaders import LoadedDocument
from support_bot.services.ingest import ingest_document
from support_bot.services.stats import collect_stats
from tests.conftest import FakeEmbedder

AUTH = ("admin", "s3cret-pass")
ORIGIN = {"Origin": "http://test"}

PAGE_HTML = (
    "<html><head><title>Moving tips</title></head>"
    "<body><main><h1>Tips</h1><p>Label boxes.</p></main></body></html>"
)


@pytest.fixture
def admin_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", AUTH[0])
    monkeypatch.setenv("ADMIN_PASSWORD", AUTH[1])
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    yield
    get_settings.cache_clear()


@pytest.fixture
def admin_app(
    db_sessions: async_sessionmaker[AsyncSession], fake_embedder: FakeEmbedder
) -> FastAPI:
    app = create_app()

    async def session() -> AsyncIterator[AsyncSession]:
        async with db_sessions() as s:
            yield s

    async def http_client() -> AsyncIterator[httpx.AsyncClient]:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, html=PAGE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_http_client] = http_client
    return app


@pytest.fixture
async def admin(admin_app: FastAPI, admin_password: None) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://test", auth=AUTH) as client:
        yield client


@pytest.fixture
async def dialog(db_session: AsyncSession, fake_embedder: FakeEmbedder) -> Conversation:
    await ingest_document(
        db_session,
        fake_embedder,
        LoadedDocument(
            "data/kb/pricing.md", SourceType.MARKDOWN, "Pricing", "# Pricing\n\nM costs $85."
        ),
        chunk_size=500,
        chunk_overlap=0,
    )
    conversation = Conversation(session_id="web-session-1", channel=Channel.WEB)
    telegram = Conversation(session_id="tg-1", channel=Channel.TELEGRAM)
    db_session.add_all([conversation, telegram])
    await db_session.flush()
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                question="How much is M?",
                answer="An M unit costs $85.",
                status=MessageStatus.ANSWERED,
                sources=[{"title": "Pricing", "url": None}],
                model="claude-opus-5-5",
                input_tokens=1000,
                output_tokens=200,
                cost_usd=Decimal("0.008"),
            ),
            Message(
                conversation_id=conversation.id,
                question="<script>alert(1)</script> pizza?",
                answer="Passed to an operator.",
                status=MessageStatus.UNANSWERED,
                handoff_reason=HandoffReason.NO_CONTEXT,
            ),
            Message(
                conversation_id=telegram.id,
                question="Can I store gasoline?",
                answer="Sorry, passed to an operator.",
                status=MessageStatus.UNANSWERED,
                handoff_reason=HandoffReason.NOT_FOUND,
                model="claude-opus-5-5",
                input_tokens=900,
                output_tokens=50,
                cost_usd=Decimal("0.0046"),
            ),
        ]
    )
    await db_session.commit()
    return conversation


async def test_admin_disabled_without_password(
    admin_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # явные настройки без .env: тест не зависит от того, задан ли пароль локально
    settings = get_settings().model_copy(update={"admin_password": None})
    monkeypatch.setattr("support_bot.api.admin.get_settings", lambda: settings)
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="http://test"
    ) as client:
        response = await client.get("/admin", auth=AUTH)

    assert response.status_code == 404


async def test_admin_requires_valid_credentials(admin_app: FastAPI, admin_password: None) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/admin")
        wrong = await client.get("/admin", auth=("admin", "wrong"))

    for response in (anonymous, wrong):
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")


async def test_stats_on_empty_database(db_session: AsyncSession) -> None:
    stats = await collect_stats(db_session)

    assert (stats.total, stats.unanswered, stats.unanswered_percent) == (0, 0, 0.0)
    assert stats.average_cost is None
    assert stats.total_cost == 0


@pytest.mark.usefixtures("dialog")
async def test_stats_with_data(db_session: AsyncSession) -> None:
    stats = await collect_stats(db_session)

    assert (stats.total, stats.answered, stats.unanswered) == (3, 1, 2)
    assert stats.unanswered_percent == 66.7
    assert stats.by_channel == {Channel.WEB: 2, Channel.TELEGRAM: 1}
    assert stats.total_cost == Decimal("0.0126")
    # средняя — только по ответам с вызовом LLM
    assert stats.average_cost == Decimal("0.006300")
    assert (stats.input_tokens, stats.output_tokens) == (1900, 250)
    assert (stats.documents, stats.chunks) == (1, 1)


@pytest.mark.usefixtures("dialog")
async def test_overview_page(admin: AsyncClient) -> None:
    response = await admin.get("/admin")

    assert response.status_code == 200
    assert "66.7%" in response.text
    assert "$0.0126" in response.text
    assert "Website 2 · Telegram 1" in response.text
    assert "Can I store gasoline?" in response.text


@pytest.mark.usefixtures("dialog")
async def test_unanswered_page_lists_only_unanswered(admin: AsyncClient) -> None:
    response = await admin.get("/admin/unanswered")

    assert response.status_code == 200
    assert "Can I store gasoline?" in response.text
    assert "the answer is not in the knowledge base" in response.text
    assert "How much is M?" not in response.text
    # вопрос пользователя — данные, а не разметка
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


@pytest.mark.usefixtures("dialog")
async def test_conversation_log_and_pagination(admin: AsyncClient) -> None:
    response = await admin.get("/admin/conversations")

    assert response.status_code == 200
    assert "How much is M?" in response.text
    assert "$0.0080" in response.text
    assert "3 total." in response.text
    assert (await admin.get("/admin/conversations?page=0")).status_code == 422


async def test_conversation_page(admin: AsyncClient, dialog: Conversation) -> None:
    response = await admin.get(f"/admin/conversations/{dialog.id}")

    assert response.status_code == 200
    assert "web-session-1" in response.text
    assert "How much is M?" in response.text
    assert "Can I store gasoline?" not in response.text
    assert (await admin.get("/admin/conversations/999999")).status_code == 404


@pytest.mark.usefixtures("dialog")
async def test_documents_page(admin: AsyncClient) -> None:
    response = await admin.get("/admin/documents")

    assert response.status_code == 200
    assert "Pricing" in response.text
    assert "data/kb/pricing.md" in response.text


async def _upload(admin: AsyncClient, **kwargs: object) -> str:
    response = await admin.post("/admin/documents", headers=ORIGIN, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 303
    page = await admin.get(response.headers["location"])
    return page.text


async def test_upload_markdown_then_duplicate(admin: AsyncClient) -> None:
    files = {"file": ("faq.md", b"# Moving FAQ\n\nWe lend trolleys for free.", "text/markdown")}

    first = await _upload(admin, files=files)
    second = await _upload(admin, files=files)

    assert "Added “Moving FAQ” (1 chunks)." in first
    assert "upload/faq.md" in first
    assert "“Moving FAQ” is already in the knowledge base." in second


async def test_upload_url(admin: AsyncClient) -> None:
    page = await _upload(admin, data={"url": "https://boxhub.example/tips"})

    assert "Added “Moving tips”" in page
    assert "https://boxhub.example/tips" in page


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        (
            {"files": {"file": ("photo.png", b"\x89PNG", "image/png")}},
            "Unsupported file type: .png",
        ),
        ({"data": {"url": "ftp://boxhub.example"}}, "URL must start with http"),
        ({"data": {"url": ""}}, "Choose a file or enter a page URL."),
        ({"files": {"file": ("empty.md", b"# Title only", "text/markdown")}}, "No text found"),
    ],
)
async def test_upload_errors(admin: AsyncClient, kwargs: dict[str, object], error: str) -> None:
    page = await _upload(admin, **kwargs)

    assert 'class="notice error"' in page
    assert error in page


async def test_upload_rejects_cross_site_requests(admin: AsyncClient) -> None:
    data = {"url": "https://boxhub.example/tips"}

    missing = await admin.post("/admin/documents", data=data)
    foreign = await admin.post(
        "/admin/documents", data=data, headers={"Origin": "https://evil.example"}
    )

    assert missing.status_code == 403
    assert foreign.status_code == 403
