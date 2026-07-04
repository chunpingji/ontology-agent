"""Persist per-section narratives on generated_reports (015).

Feature 015 surfaces per-section 行文 prose in the web report reading pane (not
only the transient DOCX). Stores the LLM-fused narrative alongside the report:
  {subject_description?, conclusion?, sections: [{section_id, title, text}]}

Revision ID: 0016_report_narratives
Revises: 0015_drop_doc_type_mappings
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_report_narratives"  # ≤32 chars
down_revision = "0015_drop_doc_type_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_reports", sa.Column("narratives", sa.JSON()))


def downgrade() -> None:
    op.drop_column("generated_reports", "narratives")
