"""Add sample_content_json column to ast_templates

013: Stores the sample DOCX parsed into faithful tiptap/ProseMirror JSON
(headings, tables, structure preserved) so the AI slot-suggestion drawer can
render a faithful preview and link suggested slots to it by structural anchor,
rather than round-tripping the document flattened to plain text.

Revision ID: 0012_ast_template_sample_json
Revises: 0011_ast_template_sample_text
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_ast_template_sample_json"  # ≤32 chars: alembic_version.version_num is VARCHAR(32)
down_revision = "0011_ast_template_sample_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ast_templates", sa.Column("sample_content_json", sa.JSON()))


def downgrade() -> None:
    op.drop_column("ast_templates", "sample_content_json")
