"""add per-topic arxiv cross-list preference

Revision ID: 20260914_01
Revises: 20260912_02
Create Date: 2026-09-14 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_01"
down_revision: str | Sequence[str] | None = "20260912_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(
            sa.Column(
                "include_cross_list",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_column("include_cross_list")
