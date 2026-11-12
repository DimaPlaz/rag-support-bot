from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import Chunk, Document, SourceType
from support_bot.rag.embeddings import Embedder


@dataclass(frozen=True)
class FoundChunk:
    chunk_id: int
    content: str
    similarity: float
    document_id: int
    document_title: str
    document_source: str
    source_type: SourceType


async def search_chunks(
    session: AsyncSession, embedder: Embedder, query: str, *, limit: int, min_similarity: float
) -> list[FoundChunk]:
    """Топ-N чанков по косинусной близости, не ниже порога."""
    vector = await embedder.embed_query(query)
    distance = Chunk.embedding.cosine_distance(vector)
    rows = await session.execute(
        select(
            Chunk.id,
            Chunk.content,
            (1 - distance).label("similarity"),
            Document.id,
            Document.title,
            Document.source,
            Document.source_type,
        )
        .join(Document, Document.id == Chunk.document_id)
        .order_by(distance)
        .limit(limit)
    )
    found = [FoundChunk(*row) for row in rows]
    return [chunk for chunk in found if chunk.similarity >= min_similarity]
