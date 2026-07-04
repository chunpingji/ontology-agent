"""AST 模板基本信息：责任人 / 默认源文件 / 训练数据（015）

为 ast_templates 增加 owner（责任人）、default_source_path/filename（默认源文件），
并新增 ast_template_training_pairs 表（源文档→评估报告 成对训练样例）。

Revision ID: 0017_ast_basic_info
Revises: 0016_report_narratives
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_ast_basic_info"  # ≤32 chars
down_revision = "0016_report_narratives"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ast_templates", sa.Column("owner", sa.String(100)))
    op.add_column("ast_templates", sa.Column("default_source_path", sa.String(500)))
    op.add_column("ast_templates", sa.Column("default_source_filename", sa.String(500)))

    op.create_table(
        "ast_template_training_pairs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "template_id",
            sa.UUID(),
            sa.ForeignKey("ast_templates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("source_path", sa.String(500), nullable=False),
        sa.Column("report_filename", sa.String(500)),
        sa.Column("report_path", sa.String(500)),
        sa.Column("created_by", sa.String(100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("ast_template_training_pairs")
    op.drop_column("ast_templates", "default_source_filename")
    op.drop_column("ast_templates", "default_source_path")
    op.drop_column("ast_templates", "owner")
