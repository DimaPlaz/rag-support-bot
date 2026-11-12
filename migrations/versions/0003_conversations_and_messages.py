"""conversations and messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("web", "telegram", name="channel", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("answered", "unanswered", name="messagestatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "handoff_reason",
            sa.Enum(
                "no_context",
                "not_found",
                "refusal",
                "max_tokens",
                "llm_error",
                name="handoffreason",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False)
    op.create_index(op.f("ix_messages_status"), "messages", ["status"], unique=False)
    op.alter_column(
        "documents",
        "source_type",
        existing_type=sa.VARCHAR(length=16),
        type_=sa.Enum(
            "markdown", "text", "pdf", "url", name="sourcetype", native_enum=False, length=32
        ),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "documents",
        "source_type",
        existing_type=sa.Enum(
            "markdown", "text", "pdf", "url", name="sourcetype", native_enum=False, length=32
        ),
        type_=sa.VARCHAR(length=16),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_messages_status"), table_name="messages")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
