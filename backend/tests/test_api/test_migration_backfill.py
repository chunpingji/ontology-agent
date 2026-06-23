"""Polish T028：0003 迁移由旧布尔回填四态正确性 + 可重入（data-model §4）。

构造**缺 `lifecycle_state` 列**的 `reasoning_executions` 表（模拟 002 存量库），经
alembic Operations 上下文驱动真实 `upgrade()`，断言四态优先级回填正确；二次 upgrade
验证幂等可重入。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

_MIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0003_workflow_statemachine.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0003", _MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DDL = text(
    "CREATE TABLE reasoning_executions ("
    " id TEXT PRIMARY KEY,"
    " superseded_by TEXT,"
    " effective INTEGER,"
    " requires_signature INTEGER)"
)

_ROWS = [
    # (id, superseded_by, effective, requires_signature, expected_state)
    ("a", "x", 0, 0, "superseded"),          # 1. superseded_by NOT NULL
    ("b", None, 1, 0, "effective"),           # 2. effective TRUE
    ("c", None, 0, 1, "pending_signature"),   # 3. requires_signature TRUE
    ("d", None, 0, 0, "effective"),           # 4. fallback → effective
]


def _run_upgrade(conn, mod):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()


def test_backfill_maps_legacy_booleans_to_four_states():
    mod = _load_migration()
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(_DDL)
        for rid, sup, eff, sig, _ in _ROWS:
            conn.execute(
                text("INSERT INTO reasoning_executions"
                     " (id, superseded_by, effective, requires_signature)"
                     " VALUES (:id, :sup, :eff, :sig)"),
                {"id": rid, "sup": sup, "eff": eff, "sig": sig},
            )
        _run_upgrade(conn, mod)

        for rid, _, _, _, expected in _ROWS:
            got = conn.execute(
                text("SELECT lifecycle_state FROM reasoning_executions WHERE id = :id"),
                {"id": rid},
            ).scalar_one()
            assert got == expected, f"row {rid}: expected {expected}, got {got}"


def test_migration_is_reentrant():
    """二次 upgrade 不重复加列/不报错（幂等守卫, data-model §4）。"""
    mod = _load_migration()
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(_DDL)
        conn.execute(text("INSERT INTO reasoning_executions"
                          " (id, effective, requires_signature) VALUES ('z', 1, 0)"))
        _run_upgrade(conn, mod)
        _run_upgrade(conn, mod)  # 再次执行：列已存在 → 早返回，不抛错
        got = conn.execute(
            text("SELECT lifecycle_state FROM reasoning_executions WHERE id = 'z'")
        ).scalar_one()
        assert got == "effective"
