from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import Chunk, Document, SourceType
from support_bot.rag.loaders import LoadedDocument
from support_bot.services.ingest import (
    EmptyDocumentError,
    IngestResult,
    IngestStatus,
    collect_targets,
    ingest_document,
)
from tests.conftest import FakeEmbedder

TEXT = "# Pricing\n\n## Sizes\n\nXS costs $29.\n\n## Discounts\n\nPrepay 6 months: 10% off."


def _doc(text: str = TEXT, source: str = "data/kb/pricing.md") -> LoadedDocument:
    return LoadedDocument(
        source=source, source_type=SourceType.MARKDOWN, title="Pricing", text=text
    )


async def _ingest(
    session: AsyncSession, embedder: FakeEmbedder, loaded: LoadedDocument
) -> IngestResult:
    return await ingest_document(session, embedder, loaded, chunk_size=500, chunk_overlap=50)


async def _counts(session: AsyncSession) -> tuple[int, int]:
    documents = await session.scalar(select(func.count()).select_from(Document))
    chunks = await session.scalar(select(func.count()).select_from(Chunk))
    return documents or 0, chunks or 0


async def test_first_ingest_creates_document_and_chunks(
    db_session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    result = await _ingest(db_session, fake_embedder, _doc())

    assert result.status is IngestStatus.CREATED
    assert result.chunk_count == 2
    assert await _counts(db_session) == (1, 2)
    contents = await db_session.scalars(select(Chunk.content).order_by(Chunk.position))
    assert list(contents) == [
        "Pricing > Sizes\n\nXS costs $29.",
        "Pricing > Discounts\n\nPrepay 6 months: 10% off.",
    ]


async def test_repeat_ingest_is_skipped_without_embedding(
    db_session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _ingest(db_session, fake_embedder, _doc())

    result = await _ingest(db_session, fake_embedder, _doc())

    assert result.status is IngestStatus.SKIPPED
    assert fake_embedder.calls == 1
    assert await _counts(db_session) == (1, 2)


async def test_same_content_under_other_source_is_skipped(
    db_session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _ingest(db_session, fake_embedder, _doc())

    result = await _ingest(db_session, fake_embedder, _doc(source="uploads/pricing-copy.md"))

    assert result.status is IngestStatus.SKIPPED
    assert result.source == "data/kb/pricing.md"
    assert await _counts(db_session) == (1, 2)


async def test_changed_content_replaces_chunks(
    db_session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    await _ingest(db_session, fake_embedder, _doc())

    result = await _ingest(db_session, fake_embedder, _doc(text="# Pricing\n\nAll units $10."))

    assert result.status is IngestStatus.UPDATED
    assert await _counts(db_session) == (1, 1)
    assert await db_session.scalar(select(Chunk.content)) == "Pricing\n\nAll units $10."


async def test_empty_document_rejected(
    db_session: AsyncSession, fake_embedder: FakeEmbedder
) -> None:
    with pytest.raises(EmptyDocumentError):
        await _ingest(db_session, fake_embedder, _doc(text="# Only heading"))
    assert await _counts(db_session) == (0, 0)


def test_collect_targets(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.pdf").write_bytes(b"")

    assert collect_targets("https://boxhub.example") == ["https://boxhub.example"]
    assert collect_targets(str(tmp_path)) == [str(tmp_path / "a.md"), str(tmp_path / "b.pdf")]
    assert collect_targets(str(tmp_path / "a.md")) == [str(tmp_path / "a.md")]
    with pytest.raises(FileNotFoundError):
        collect_targets(str(tmp_path / "missing"))
