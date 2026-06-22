"""ontology_meta: E1–E10 表 + audit_log 增列 actor/release_id（能力一）

首个迁移：无既有迁移链，故从 ``Base.metadata`` 物化全量 schema（幂等），
其中 ``audit_log`` 已含 ``actor``/``release_id`` 两列（模型已定义，R6/FR-032）。
对遗留库（audit_log 早于本特性存在且缺列）提供防御式补列。

Revision ID: 0001_ontology_meta
Revises:
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.models  # noqa: F401  — populate Base.metadata with every table
from app.db import Base
from app.models.types import GUID

# revision identifiers, used by Alembic.
revision = "0001_ontology_meta"
down_revision = None
branch_labels = None
depends_on = None

# Tables introduced by 能力一 (dropped on downgrade; legacy tables untouched).
_FEATURE_TABLES = (
    "ontology_change_log",
    "ontology_release",
    "ontology_class_mapping",
    "ontology_restriction",
    "ontology_action",
    "ontology_data_property",
    "ontology_link_type",
    "ontology_class",
    "app_user",
    "app_role",
)


def upgrade() -> None:
    bind = op.get_bind()
    # Create every table defined on the metadata; checkfirst keeps it idempotent
    # and leaves any pre-existing legacy tables alone.
    Base.metadata.create_all(bind=bind, checkfirst=True)

    # Defensive: a legacy audit_log created before this feature may lack the new
    # columns (create_all skips existing tables, so it would not add them).
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("audit_log")}
    if "actor" not in cols:
        op.add_column("audit_log", sa.Column("actor", sa.String(length=100), nullable=True))
        op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    if "release_id" not in cols:
        op.add_column("audit_log", sa.Column("release_id", GUID(), nullable=True))
        op.create_index("ix_audit_log_release_id", "audit_log", ["release_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for table in _FEATURE_TABLES:
        if table in existing:
            op.drop_table(table)

    cols = {c["name"] for c in sa.inspect(bind).get_columns("audit_log")}
    if "release_id" in cols:
        op.drop_index("ix_audit_log_release_id", table_name="audit_log")
        op.drop_column("audit_log", "release_id")
    if "actor" in cols:
        op.drop_index("ix_audit_log_actor", table_name="audit_log")
        op.drop_column("audit_log", "actor")
