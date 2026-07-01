"""generated_reports 表（010-risk-report-generation）

存储生成的风险评估报告元数据，链接到 extraction_jobs，支持后续检索。

Revision ID: 0007_add_generated_reports
Revises: 0006_system_config_and_doc_path
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_add_generated_reports"
down_revision = "0006_system_config_and_doc_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("report_type", sa.String(50), nullable=False, server_default="risk_assessment"),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("rules_fired_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rules_summary", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("generated_reports")
