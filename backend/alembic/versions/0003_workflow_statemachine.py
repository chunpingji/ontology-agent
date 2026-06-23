"""workflow-statemachine-closure: 结论显式生命周期状态机闭环

单一迁移：为 ``reasoning_executions`` 增 ``lifecycle_state`` 列（显式四态唯一真理来源）
+ 建索引 + 由旧布尔（superseded_by / effective / requires_signature）回填四态
（data-model §4 优先级表）。沿用 0002 的防御式 ``sa.inspect`` 守卫 + ``op.add_column``
风格，幂等可重入。``action_execution.status`` 的 ``voided`` 取值为语义扩展（既有
``String(20)`` 容纳），无结构变更。

本特性不写回权威 TTL、无 T-Box 写入（宪章 II 不触发）。

Revision ID: 0003_workflow_statemachine
Revises: 0002_extraction_realtime
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_workflow_statemachine"
down_revision = "0002_extraction_realtime"
branch_labels = None
depends_on = None

_TABLE = "reasoning_executions"
_COLUMN = "lifecycle_state"
_INDEX = f"ix_{_TABLE}_{_COLUMN}"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return  # 全新库由 create_all 直接建表（已含列），无需补列。

    present = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in present:
        return  # 幂等：已存在则不重复添加（可重入）。

    # 先以可空 + server_default 落列（兼容存量行），随后回填，再建索引。
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.String(30), nullable=True, server_default="effective"),
    )

    # 四态回填（优先级自上而下，命中即停；data-model §4）。
    # 1. superseded_by IS NOT NULL → superseded
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_COLUMN} = 'superseded' "
            f"WHERE superseded_by IS NOT NULL"
        )
    )
    # 2. effective IS TRUE（且非已取代）→ effective
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_COLUMN} = 'effective' "
            f"WHERE superseded_by IS NULL AND effective = {_true_literal(bind)}"
        )
    )
    # 3. requires_signature IS TRUE（且未生效、未取代）→ pending_signature
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_COLUMN} = 'pending_signature' "
            f"WHERE superseded_by IS NULL AND effective = {_false_literal(bind)} "
            f"AND requires_signature = {_true_literal(bind)}"
        )
    )
    # 4. 其余历史无标记 → effective（保守：存量结论视为已生效）。
    op.execute(
        sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = 'effective' WHERE {_COLUMN} IS NULL")
    )

    op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    present = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in present:
        return
    try:
        op.drop_index(_INDEX, table_name=_TABLE)
    except Exception:  # pragma: no cover - index may be absent on legacy
        pass
    op.drop_column(_TABLE, _COLUMN)


def _true_literal(bind) -> str:
    """布尔真值字面量（PostgreSQL 用 TRUE，SQLite 用 1）。"""
    return "1" if bind.dialect.name == "sqlite" else "TRUE"


def _false_literal(bind) -> str:
    return "0" if bind.dialect.name == "sqlite" else "FALSE"
