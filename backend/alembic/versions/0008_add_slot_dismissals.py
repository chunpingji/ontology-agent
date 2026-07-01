"""slot_dismissals 表（011-ast-extraction-ui）

持久化用户对 AST 槽位的「不适用」标记，按 (job_id, slot_id) 唯一约束。

Revision ID: 0008_add_slot_dismissals
Revises: 0007_add_generated_reports
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_add_slot_dismissals"
down_revision = "0007_add_generated_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slot_dismissals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("slot_id", sa.String(200), nullable=False),
        sa.Column("dismissed_by", sa.String(100), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("job_id", "slot_id", name="uq_slot_dismissal_job_slot"),
    )


def downgrade() -> None:
    op.drop_table("slot_dismissals")
