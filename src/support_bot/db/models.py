from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from support_bot.db.base import Base

# Размерность зашита в схему БД: смена модели эмбеддингов требует новой миграции.
EMBEDDING_DIM = 768


def _str_enum(enum: type[StrEnum]) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class SourceType(StrEnum):
    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    URL = "url"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(2048), unique=True)
    source_type: Mapped[SourceType] = mapped_column(_str_enum(SourceType))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "position"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class Channel(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"


class MessageStatus(StrEnum):
    ANSWERED = "answered"
    UNANSWERED = "unanswered"


class HandoffReason(StrEnum):
    NO_CONTEXT = "no_context"  # поиск не нашёл достаточно похожих чанков
    NOT_FOUND = "not_found"  # модель сообщила, что ответа в контексте нет
    REFUSAL = "refusal"
    MAX_TOKENS = "max_tokens"
    LLM_ERROR = "llm_error"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True)
    channel: Mapped[Channel] = mapped_column(_str_enum(Channel))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    """Один обмен: вопрос пользователя и ответ бота."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(_str_enum(MessageStatus), index=True)
    handoff_reason: Mapped[HandoffReason | None] = mapped_column(_str_enum(HandoffReason))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
