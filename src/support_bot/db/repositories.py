from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import Channel, Chunk, Conversation, Document, Message


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


async def get_or_create_conversation(
    session: AsyncSession, session_id: str, channel: Channel
) -> Conversation:
    # ON CONFLICT: два параллельных запроса одной сессии не должны падать на уникальности
    await session.execute(
        pg_insert(Conversation)
        .values(session_id=session_id, channel=channel)
        .on_conflict_do_nothing(index_elements=[Conversation.session_id])
    )
    conversation = await session.scalar(
        select(Conversation).where(Conversation.session_id == session_id)
    )
    assert conversation is not None
    return conversation


async def recent_messages(session: AsyncSession, conversation_id: int, limit: int) -> list[Message]:
    """Последние сообщения диалога в хронологическом порядке."""
    if limit <= 0:
        return []
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.all()))
