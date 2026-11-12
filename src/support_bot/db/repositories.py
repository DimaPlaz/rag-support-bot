from sqlalchemy import delete, func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from support_bot.db.models import (
    Channel,
    Chunk,
    Conversation,
    Document,
    Message,
    MessageStatus,
)


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


async def list_documents(session: AsyncSession) -> list[tuple[Document, int]]:
    """Документы с числом чанков, свежие первыми."""
    chunk_count = func.count(Chunk.id)
    rows = await session.execute(
        select(Document, chunk_count)
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.updated_at.desc(), Document.id.desc())
    )
    return [(document, count) for document, count in rows]


async def list_messages(
    session: AsyncSession, *, unanswered_only: bool, limit: int, offset: int
) -> tuple[list[tuple[Message, Conversation]], int]:
    """Сообщения с диалогами, новые первыми, и общее число для пагинации."""
    condition = Message.status == MessageStatus.UNANSWERED if unanswered_only else true()
    total = await session.scalar(select(func.count(Message.id)).where(condition)) or 0
    rows = await session.execute(
        select(Message, Conversation)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(condition)
        .order_by(Message.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(message, conversation) for message, conversation in rows], total


async def get_conversation(
    session: AsyncSession, conversation_id: int
) -> tuple[Conversation, list[Message]] | None:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    messages = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return conversation, list(messages)
