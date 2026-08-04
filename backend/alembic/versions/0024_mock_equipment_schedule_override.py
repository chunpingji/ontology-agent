"""Add editable mock equipment schedule overrides.

Revision ID: 0024_mock_schedule_override
Revises: 0023_sample_docx
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0024_mock_schedule_override"
down_revision = "0023_sample_docx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mock_equipment_schedule_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("equipment_id", sa.String(100), nullable=False),
        sa.Column("schedule_date", sa.Date(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "equipment_id",
            "schedule_date",
            name="uq_mock_schedule_equipment_date",
        ),
    )
    op.create_index(
        "ix_mock_equipment_schedule_overrides_equipment_id",
        "mock_equipment_schedule_overrides",
        ["equipment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mock_equipment_schedule_overrides_equipment_id",
        table_name="mock_equipment_schedule_overrides",
    )
    op.drop_table("mock_equipment_schedule_overrides")
