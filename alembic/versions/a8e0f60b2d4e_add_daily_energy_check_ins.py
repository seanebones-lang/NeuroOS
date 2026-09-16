"""Add durable daily energy check-ins.

Revision ID: a8e0f60b2d4e
Revises: f5c8d2e193b1
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8e0f60b2d4e"
down_revision: str | None = "f5c8d2e193b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_energy_check_ins",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column(
            "energy_level",
            postgresql.ENUM("DEEP", "SHALLOW", "RECOVERY", name="energylevel", create_type=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "check_in_date", name="uq_daily_energy_check_ins_user_date"),
    )
    op.create_index("ix_daily_energy_check_ins_user_id", "daily_energy_check_ins", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_daily_energy_check_ins_user_id", table_name="daily_energy_check_ins")
    op.drop_table("daily_energy_check_ins")
