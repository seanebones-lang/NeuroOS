"""Add task protocol-run provenance.

Revision ID: d1f6a2c8349b
Revises: bc565ca98d06
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f6a2c8349b"
down_revision: str | None = "bc565ca98d06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("protocol_run_id", sa.UUID(), nullable=True))
        batch_op.create_foreign_key(
            "fk_tasks_protocol_run_id_protocol_runs",
            "protocol_runs",
            ["protocol_run_id"],
            ["id"],
        )
        batch_op.create_index("ix_tasks_protocol_run_id", ["protocol_run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_protocol_run_id")
        batch_op.drop_constraint("fk_tasks_protocol_run_id_protocol_runs", type_="foreignkey")
        batch_op.drop_column("protocol_run_id")
