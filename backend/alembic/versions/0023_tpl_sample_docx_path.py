"""Add sample_docx_path to ast_templates for DOCX template-based output.

Persists the uploaded sample .docx file path so the report renderer can open
it as the base Document, preserving styles, headers, footers, and page layout.

Revision ID: 0023_sample_docx
Revises: 0022_user_pw
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_sample_docx"
down_revision = "0022_user_pw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ast_templates",
        sa.Column("sample_docx_path", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ast_templates", "sample_docx_path")
