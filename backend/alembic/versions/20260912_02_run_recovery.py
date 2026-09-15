"""link analyses to daily runs for crash recovery

Revision ID: 20260912_02
Revises: 20260912_01
Create Date: 2026-09-12 20:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_02"
down_revision: str | Sequence[str] | None = "20260912_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.add_column(sa.Column("run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_analyses_run_id_run_logs",
            "run_logs",
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_analyses_run_id", ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.drop_index("ix_analyses_run_id")
        batch_op.drop_constraint("fk_analyses_run_id_run_logs", type_="foreignkey")
        batch_op.drop_column("run_id")
