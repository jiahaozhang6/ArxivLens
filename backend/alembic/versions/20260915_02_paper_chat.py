"""add persistent paper chat sessions and messages

Revision ID: 20260915_02
Revises: 20260915_01
Create Date: 2026-09-15 17:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260915_02"
down_revision: str | Sequence[str] | None = "20260915_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_chat_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("preferred_llm_profile_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["preferred_llm_profile_id"], ["llm_profiles.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_chat_sessions_paper_updated",
        "paper_chat_sessions",
        ["paper_id", "updated_at"],
    )
    op.create_table(
        "paper_chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("model_routing", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["paper_chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_profile_id"], ["llm_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_chat_messages_session_created",
        "paper_chat_messages",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_paper_chat_messages_status",
        "paper_chat_messages",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_chat_messages_status", table_name="paper_chat_messages")
    op.drop_index("ix_paper_chat_messages_session_created", table_name="paper_chat_messages")
    op.drop_table("paper_chat_messages")
    op.drop_index("ix_paper_chat_sessions_paper_updated", table_name="paper_chat_sessions")
    op.drop_table("paper_chat_sessions")
