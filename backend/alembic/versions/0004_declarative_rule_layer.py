"""declarative-rule-layer: E11/E12/E13 三张声明式规则元数据表（能力 006）

新增可编辑 T-Box 元数据表：
- ``ontology_classification_criterion``（E11，分类判据，`logic_role=defined`）
- ``ontology_decision_rule``（E12，产生式规则 R-ED/R-SC/R-CP）
- ``ontology_conflict_policy``（E13，冲突消解策略）

沿用 0001 的 ``Base.metadata.create_all(checkfirst=True)`` 物化风格：模型已在
``app.models`` 注册，故迁移与模型定义逐字一致、幂等可重入；downgrade 仅 drop 本
特性三表。表的内容（判据/规则/策略种子）由发布期 seed 写入，不在结构迁移内。

本迁移仅建元数据表，不写回权威 TTL、不触发 T-Box 物化（宪章 II 不触发）。

Revision ID: 0004_declarative_rule_layer
Revises: 0003_workflow_statemachine
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.models  # noqa: F401  — populate Base.metadata with every table
from app.db import Base

revision = "0004_declarative_rule_layer"
down_revision = "0003_workflow_statemachine"
branch_labels = None
depends_on = None

# Tables introduced by 能力 006 (dropped on downgrade; legacy tables untouched).
_FEATURE_TABLES = (
    "ontology_conflict_policy",
    "ontology_decision_rule",
    "ontology_classification_criterion",
)


def upgrade() -> None:
    bind = op.get_bind()
    # checkfirst keeps it idempotent and leaves pre-existing tables alone;
    # only the three new E11/E12/E13 tables are materialised here.
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for table in _FEATURE_TABLES:
        if table in existing:
            op.drop_table(table)
