"""ast_templates + document_type_mappings 表（012-ast-template-llm-pipeline）

多模板管理层：存储报告模板定义（版本化）与文档类型→模板映射规则。
种子数据：将默认 qs_a_020f05.json 模板写入 DB 并创建 CMCReport 映射。

Revision ID: 0009_add_ast_templates
Revises: 0008_add_slot_dismissals
Create Date: 2026-07-01
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0009_add_ast_templates"
down_revision = "0008_add_slot_dismissals"
branch_labels = None
depends_on = None

_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "app"
    / "services"
    / "reporting"
    / "templates"
    / "qs_a_020f05.json"
)


def upgrade() -> None:
    op.create_table(
        "ast_templates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("doc_no", sa.String(50)),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("name", "version", name="uq_template_name_version"),
    )

    op.create_table(
        "document_type_mappings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("doc_class_iri_pattern", sa.String(500), nullable=False),
        sa.Column(
            "template_id",
            sa.UUID(),
            sa.ForeignKey("ast_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Seed default template from JSON file
    template_id = uuid.uuid4()
    schema_json = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))

    ast_templates = sa.table(
        "ast_templates",
        sa.column("id", sa.UUID()),
        sa.column("name", sa.String),
        sa.column("version", sa.String),
        sa.column("doc_no", sa.String),
        sa.column("schema_json", sa.JSON),
        sa.column("is_default", sa.Boolean),
        sa.column("created_by", sa.String),
    )
    op.bulk_insert(ast_templates, [{
        "id": template_id,
        "name": "QS-A-020F05 风险评估",
        "version": "v1",
        "doc_no": "QS-A-020F05",
        "schema_json": schema_json,
        "is_default": True,
        "created_by": "system",
    }])

    doc_type_mappings = sa.table(
        "document_type_mappings",
        sa.column("id", sa.UUID()),
        sa.column("doc_class_iri_pattern", sa.String),
        sa.column("template_id", sa.UUID()),
        sa.column("priority", sa.Integer),
    )
    op.bulk_insert(doc_type_mappings, [{
        "id": uuid.uuid4(),
        "doc_class_iri_pattern": "CMCReport",
        "template_id": template_id,
        "priority": 0,
    }])


def downgrade() -> None:
    op.drop_table("document_type_mappings")
    op.drop_table("ast_templates")
