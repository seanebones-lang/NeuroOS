"""Add protocol-run idempotency.

Revision ID: e4b7c1d982a0
Revises: d1f6a2c8349b
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c1d982a0"
down_revision: str | None = "d1f6a2c8349b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("protocol_runs") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint(
            "uq_protocol_runs_user_protocol_idempotency",
            ["user_id", "protocol_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("protocol_runs") as batch_op:
        batch_op.drop_constraint(
            "uq_protocol_runs_user_protocol_idempotency",
            type_="unique",
        )
        batch_op.drop_column("idempotency_key")
