"""Add report_status + report_error to generated_reports (013-llm-template-report-enhance)

Async report generation tracks status lifecycle (pending → running → completed | failed).
Existing rows have NULL status, treated as 'completed' by application logic.

Revision ID: 0010_report_async_status
Revises: 0009_add_ast_templates
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_report_async_status"
down_revision = "0009_add_ast_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_reports", sa.Column("report_status", sa.String(20)))
    op.add_column("generated_reports", sa.Column("report_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("generated_reports", "report_error")
    op.drop_column("generated_reports", "report_status")
