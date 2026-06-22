"""数据库源只读反射读取器（能力二, FR-012, R2/R7）。

通过 SQLAlchemy `inspect()` 对只读源库做结构反射：
- 每张表 → 一个 `class` 候选（列清单作为数据属性建议）
- 每个外键 → 一个 `link` 候选（from 表 → to 表）

凭据安全（R7）：源库 DSN **不入库**，仅以环境变量名 `dsn_ref` 引用；运行时经
`os.environ[dsn_ref]` 注入。反射为只读，不写源库；这些候选只进入审核队列，
绝不自动发布为权威 T-Box（宪法原则 II）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from sqlalchemy import create_engine, inspect

logger = logging.getLogger(__name__)


@dataclass
class DBStructureCandidate:
    candidate_kind: str  # "class" | "link"
    name: str
    properties: dict = field(default_factory=dict)


class DBSourceError(RuntimeError):
    """源库不可达 / DSN 引用缺失。"""


def _resolve_dsn(dsn_ref: str) -> str:
    dsn = os.environ.get(dsn_ref)
    if not dsn:
        raise DBSourceError(f"未找到环境变量 {dsn_ref}（凭据须经 env 注入，不入库, R7）")
    return dsn


def reflect_database(
    dsn_ref: str,
    schema: str | None = None,
    include_tables: list[str] | None = None,
) -> list[DBStructureCandidate]:
    """反射源库结构，返回 class/link 结构候选。"""
    dsn = _resolve_dsn(dsn_ref)
    try:
        engine = create_engine(dsn)
        inspector = inspect(engine)
        table_names = inspector.get_table_names(schema=schema)
    except Exception as exc:  # noqa: BLE001 — 源库不可达统一上抛
        raise DBSourceError(f"源库反射失败：{type(exc).__name__}: {exc}") from exc

    if include_tables:
        wanted = set(include_tables)
        table_names = [t for t in table_names if t in wanted]

    candidates: list[DBStructureCandidate] = []
    try:
        for table in table_names:
            columns = inspector.get_columns(table, schema=schema)
            pks = inspector.get_pk_constraint(table, schema=schema).get(
                "constrained_columns", []
            )
            candidates.append(DBStructureCandidate(
                candidate_kind="class",
                name=table,
                properties={
                    "table": table,
                    "schema": schema,
                    "columns": [
                        {"name": c["name"], "type": str(c.get("type")),
                         "nullable": bool(c.get("nullable", True))}
                        for c in columns
                    ],
                    "primary_key": pks,
                },
            ))

            for fk in inspector.get_foreign_keys(table, schema=schema):
                referred = fk.get("referred_table")
                if not referred:
                    continue
                candidates.append(DBStructureCandidate(
                    candidate_kind="link",
                    name=f"{table}_{referred}",
                    properties={
                        "from_table": table,
                        "to_table": referred,
                        "constrained_columns": fk.get("constrained_columns", []),
                        "referred_columns": fk.get("referred_columns", []),
                    },
                ))
    finally:
        engine.dispose()

    logger.info("DB 源反射完成：%d 张表 → %d 个结构候选", len(table_names), len(candidates))
    return candidates
