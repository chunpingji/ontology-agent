"""系统配置表 + extraction_jobs.document_path（UI 改进 Phase 1）

新表 system_configs：key-value JSON 配置（默认抽取目标、文件类型关键词等）。
extraction_jobs 增列 document_path：上传文件持久路径（替代 /tmp 临时文件，支持
标注端点重新读取源文档）。

Revision ID: 0006_system_config_and_doc_path
Revises: 0005_add_ner_columns
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_system_config_and_doc_path"
down_revision = "0005_add_ner_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_configs",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("extraction_jobs")}
    if "document_path" not in existing:
        op.add_column(
            "extraction_jobs",
            sa.Column("document_path", sa.String(500), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("extraction_jobs", "document_path")
    op.drop_table("system_configs")
