from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import Chunk, Document


async def get_document_by_hash(session: AsyncSession, content_hash: str) -> Document | None:
    return await session.scalar(select(Document).where(Document.content_hash == content_hash))


async def get_document_by_source(session: AsyncSession, source: str) -> Document | None:
    return await session.scalar(select(Document).where(Document.source == source))


async def replace_chunks(
    session: AsyncSession, document: Document, chunks: list[str], embeddings: list[list[float]]
) -> None:
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    session.add_all(
        Chunk(document_id=document.id, position=position, content=content, embedding=embedding)
        for position, (content, embedding) in enumerate(zip(chunks, embeddings, strict=True))
    )
