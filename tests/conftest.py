import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from support_bot.config import get_settings
from support_bot.db.models import EMBEDDING_DIM
from support_bot.main import create_app

TEST_DATABASE = "support_bot_test"


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


class FakeEmbedder:
    """Детерминированные векторы из хеша текста: тестам не нужна модель на 1 ГБ."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[i % len(digest)] / 255 + 0.01 for i in range(EMBEDDING_DIM)]


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


class VocabEmbedder(FakeEmbedder):
    """Вектор — наличие слов из словаря: близость текстов предсказуема в тестах поиска."""

    def __init__(self, vocabulary: list[str]) -> None:
        super().__init__()
        self._vocabulary = vocabulary

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [1.0 if word in lowered else 0.0 for word in self._vocabulary]
        # маленькая общая компонента, чтобы вектор не был нулевым
        vector.append(0.01)
        return vector + [0.0] * (EMBEDDING_DIM - len(vector))


async def _prepare_database(url: str) -> None:
    admin_engine = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin_engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    url = make_url(get_settings().database_url).set(database=TEST_DATABASE)
    url_string = url.render_as_string(hide_password=False)
    try:
        asyncio.run(asyncio.wait_for(_prepare_database(url_string), timeout=5))
    except (OSError, TimeoutError) as error:
        pytest.skip(f"PostgreSQL is not available ({error}); run `docker compose up -d db`")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url_string.replace("%", "%%"))
    command.upgrade(config, "head")
    yield url_string


@pytest.fixture
async def db_sessions(test_database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE documents, chunks, conversations, messages RESTART IDENTITY CASCADE")
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db_session(
    db_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_sessions() as session:
        yield session
