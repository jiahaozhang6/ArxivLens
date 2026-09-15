"""add incremental daily run progress

Revision ID: 20260915_01
Revises: 20260914_01
Create Date: 2026-09-15 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260915_01"
down_revision: str | Sequence[str] | None = "20260914_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run_logs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "progress_stage",
                sa.String(length=30),
                nullable=False,
                server_default="starting",
            )
        )
        batch_op.add_column(
            sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("run_logs") as batch_op:
        batch_op.drop_column("progress_percent")
        batch_op.drop_column("progress_total")
        batch_op.drop_column("progress_current")
        batch_op.drop_column("progress_stage")
