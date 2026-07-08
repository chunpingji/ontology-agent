"""Add soft-delete (deleted_at) to generated_reports.

Reports were previously append-only with no delete path.  The UI exposed a
"delete" action that only cleared the client-side cache (FR-027), causing
deleted items to reappear on page refresh.  This migration adds a nullable
deleted_at timestamp so the backend can persist the deletion.

Revision ID: 0018_report_soft_del
Revises: 0017_ast_basic_info
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_report_soft_del"  # ≤32 chars
down_revision = "0017_ast_basic_info"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_reports",
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("generated_reports", "deleted_at")
