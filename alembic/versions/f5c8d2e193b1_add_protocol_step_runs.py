"""Add protocol step execution records.

Revision ID: f5c8d2e193b1
Revises: e4b7c1d982a0
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5c8d2e193b1"
down_revision: str | None = "e4b7c1d982a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protocol_step_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("protocol_run_id", sa.UUID(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tool_activity", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["protocol_run_id"], ["protocol_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "protocol_run_id",
            "step_index",
            name="uq_protocol_step_runs_run_index",
        ),
    )
    op.create_index(
        "ix_protocol_step_runs_protocol_run_id",
        "protocol_step_runs",
        ["protocol_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_protocol_step_runs_protocol_run_id", table_name="protocol_step_runs")
    op.drop_table("protocol_step_runs")
