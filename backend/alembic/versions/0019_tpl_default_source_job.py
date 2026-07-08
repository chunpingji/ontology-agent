"""Link AST template default source file to an ExtractionJob.

Previously the default source was stored as a raw file with no annotation
pipeline entry.  Adding a job_id lets the source tab preview content,
show the relation graph, and re-run recognition — same as regular docs.

Revision ID: 0019_tpl_src_job
Revises: 0018_report_soft_del
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_tpl_src_job"
down_revision = "0018_report_soft_del"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ast_templates",
        sa.Column(
            "default_source_job_id",
            sa.UUID(),
            sa.ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ast_templates", "default_source_job_id")
