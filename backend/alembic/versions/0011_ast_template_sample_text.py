"""Add sample_text column to ast_templates

Stores the extracted text from the sample DOCX used to create the template,
so the AI slot suggestion drawer can display it when editing.

Revision ID: 0011_ast_template_sample_text
Revises: 0010_report_async_status
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_ast_template_sample_text"
down_revision = "0010_report_async_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ast_templates", sa.Column("sample_text", sa.Text()))


def downgrade() -> None:
    op.drop_column("ast_templates", "sample_text")
