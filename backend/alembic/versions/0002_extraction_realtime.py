"""extraction-realtime: 能力二/三 GAP — 候选归组、实时物化、动作、Part 11 签名、审计哈希链

单一迁移覆盖 data-model.md 全部新表与列扩展（T004–T006）。沿用 0001 的
``Base.metadata.create_all(checkfirst=True)`` + 防御式 ``add_column`` 模式，
对既有表（extraction_candidates / reasoning_executions / audit_log /
integration_connectors）幂等补列，对遗留行用 server_default 回填。

Revision ID: 0002_extraction_realtime
Revises: 0001_ontology_meta
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.models  # noqa: F401  — populate Base.metadata with every table
from app.db import Base
from app.models.types import GUID

revision = "0002_extraction_realtime"
down_revision = "0001_ontology_meta"
branch_labels = None
depends_on = None

# 🆕 本特性新增表（downgrade 时删除）。
_FEATURE_TABLES = (
    "fact_materialization_run",
    "action_execution",
    "electronic_signatures",
)

# 既有表的列扩展：{table: [(col_name, Column, index?)]}。
_COLUMN_EXT: dict[str, list[tuple]] = {
    "extraction_candidates": [
        ("candidate_kind", sa.Column("candidate_kind", sa.String(20), nullable=False,
                                     server_default="instance"), False),
        ("group_key", sa.Column("group_key", sa.String(500), nullable=True), True),
        ("is_canonical", sa.Column("is_canonical", sa.Boolean(), nullable=True,
                                    server_default=sa.false()), False),
        ("source_ref", sa.Column("source_ref", sa.String(200), nullable=True), False),
        ("degraded_reason", sa.Column("degraded_reason", sa.String(200), nullable=True), False),
        ("merged_into_id", sa.Column("merged_into_id", GUID(), nullable=True), False),
        ("action_conditions", sa.Column("action_conditions", sa.JSON(), nullable=True), False),
    ],
    "reasoning_executions": [
        ("requires_signature", sa.Column("requires_signature", sa.Boolean(), nullable=True,
                                         server_default=sa.false()), False),
        ("effective", sa.Column("effective", sa.Boolean(), nullable=True,
                                 server_default=sa.false()), False),
        ("signature_id", sa.Column("signature_id", GUID(), nullable=True), False),
        ("affected_subgraph", sa.Column("affected_subgraph", sa.JSON(), nullable=True), False),
        ("superseded_by", sa.Column("superseded_by", GUID(), nullable=True), False),
    ],
    "audit_log": [
        ("prev_hash", sa.Column("prev_hash", sa.String(64), nullable=True), False),
        ("entry_hash", sa.Column("entry_hash", sa.String(64), nullable=True), True),
        ("seq", sa.Column("seq", sa.Integer(), nullable=True), True),
    ],
    "integration_connectors": [
        ("ingest_mode", sa.Column("ingest_mode", sa.String(20), nullable=True,
                                  server_default="poll"), False),
        ("poll_interval_seconds", sa.Column("poll_interval_seconds", sa.Integer(), nullable=True,
                                            server_default="2"), False),
        ("sync_cursor", sa.Column("sync_cursor", sa.JSON(), nullable=True), False),
        ("last_status", sa.Column("last_status", sa.String(20), nullable=True), False),
        ("last_error", sa.Column("last_error", sa.Text(), nullable=True), False),
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    # 新表（含 0001 未建表）幂等物化；既有表被 checkfirst 跳过 → 下面手工补列。
    Base.metadata.create_all(bind=bind, checkfirst=True)

    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())
    for table, columns in _COLUMN_EXT.items():
        if table not in existing_tables:
            continue
        present = {c["name"] for c in insp.get_columns(table)}
        for name, column, indexed in columns:
            if name in present:
                continue
            op.add_column(table, column)
            if indexed:
                unique = name == "seq"
                op.create_index(f"ix_{table}_{name}", table, [name], unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, columns in _COLUMN_EXT.items():
        if table not in set(insp.get_table_names()):
            continue
        present = {c["name"] for c in insp.get_columns(table)}
        for name, _column, indexed in columns:
            if name not in present:
                continue
            if indexed:
                try:
                    op.drop_index(f"ix_{table}_{name}", table_name=table)
                except Exception:  # pragma: no cover - index may not exist on legacy
                    pass
            op.drop_column(table, name)

    existing = set(sa.inspect(bind).get_table_names())
    for table in _FEATURE_TABLES:
        if table in existing:
            op.drop_table(table)
