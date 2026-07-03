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
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select

from app.services.extraction.transforms import apply_transform

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


# =========================================================================== #
# Declaration-driven row reading (014 US1, R4/R5/R12, FR-007/010/019/020/023a)
# =========================================================================== #


@dataclass
class RowCandidate:
    """One declaration-driven candidate produced from a source row.

    ``instance`` — a bound entity row (data + resolved object properties);
    ``link`` — an *unresolved* object reference queued for reviewer resolution
    (FR-023a). ``source_ref`` is the resolvable provenance triple
    ``{system, entity, record}`` (SC-003); ``notes`` collects per-value
    transform issues (non-fatal, spec Edge Cases).
    """

    candidate_kind: str
    extracted_properties: dict
    source_ref: dict
    identifier: str | None = None
    target_class_iri: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RowReadResult:
    """Outcome of reading a bound table: produced candidates plus two health
    signals — ``drifted_paths`` (declared columns absent from the table → E6
    ``health="drift"``) and ``degraded_reason`` (DSN unset/unreachable →
    zero candidates, job degrades, never crashes; R12/FR-019)."""

    candidates: list[RowCandidate] = field(default_factory=list)
    drifted_paths: list[str] = field(default_factory=list)
    degraded_reason: str | None = None


def _individual_id_value(ind, prop_key: str) -> Any:
    """Read ``prop_key`` off an individual's properties (exact, else substring).

    Mirrors the aligner's tolerant key match so a ``target_id_path`` declared as
    a bare column name or a full property IRI both resolve.
    """
    props = getattr(ind, "properties", None) or {}
    if prop_key in props:
        return props[prop_key]
    for key, val in props.items():
        if prop_key and prop_key in key:
            return val
    return None


def _resolve_id_reference(engine, target_class_iri, target_id_path, raw) -> str | None:
    """Resolve an object ``id_reference`` → the IRI of the existing individual of
    ``target_class_iri`` whose ``target_id_path`` equals ``raw`` (R4/FR-023a).

    Returns ``None`` when no such individual exists (caller emits a ``link``
    review candidate). Read-only over the engine (Principle II)."""
    if not (target_class_iri and target_id_path):
        return None
    for ind in engine.get_individuals(target_class_iri) or []:
        val = _individual_id_value(ind, target_id_path)
        if val is not None and str(val) == str(raw):
            return ind.iri
    return None


def read_source_rows(binding, property_bindings, engine) -> RowReadResult:
    """Read a ``db_table``-bound source table into declaration-driven candidates.

    Resolves the DSN via ``os.environ[binding.source_system]`` (credentials via
    env, never stored — FR-006/R7), reflects the bound table, and for each row
    applies every property binding's transform (R6) to build one ``instance``
    candidate. Object ``id_reference`` bindings resolve to an existing
    individual's IRI, else spawn a ``link`` review candidate (R4/FR-023a).

    Drift (R5/FR-020): a declared ``source_path`` absent from the reflected table
    is skipped and reported in ``drifted_paths`` (caller marks E6 ``health``).
    Degradation (R12/FR-019): an unset or unreachable DSN returns zero candidates
    with a ``degraded_reason`` — the job completes, never crashes.
    """
    dsn_ref = (binding.source_system or "").strip()
    table = (binding.target or "").strip()

    # T019 — DSN unset → degrade (never crash).
    try:
        dsn = _resolve_dsn(dsn_ref)
    except DBSourceError as exc:
        logger.warning("DB 源降级（DSN 未注入）：%s", exc)
        return RowReadResult(degraded_reason=str(exc))

    sa_engine = create_engine(dsn)
    try:
        # T019 — unreachable source / missing table → degrade (never crash).
        try:
            md = MetaData()
            tbl = Table(table, md, autoload_with=sa_engine)
        except Exception as exc:  # noqa: BLE001 — 源不可达/表缺失统一降级
            logger.warning("DB 源降级（表不可达）：%s", exc)
            return RowReadResult(
                degraded_reason=f"源表 {table} 不可达：{type(exc).__name__}: {exc}"
            )

        actual_cols = set(tbl.columns.keys())

        # T014 — drift: declared columns absent from the reflected table.
        drifted: list[str] = []
        active: list = []
        for pb in property_bindings:
            if pb.source_path and pb.source_path not in actual_cols:
                drifted.append(pb.source_path)
                continue
            active.append(pb)
        if drifted:
            logger.warning("E6 漂移：表 %s 缺列 %s（跳过对应绑定, R5）", table, drifted)

        data_bindings = [pb for pb in active if (pb.property_kind or "data") == "data"]
        object_bindings = [pb for pb in active if pb.property_kind == "object"]
        id_binding = next((pb for pb in active if pb.is_identifier), None)

        with sa_engine.connect() as conn:
            raw_rows = [dict(r._mapping) for r in conn.execute(select(tbl))]
    finally:
        sa_engine.dispose()

    candidates: list[RowCandidate] = []
    for idx, row in enumerate(raw_rows):
        # Resolvable provenance (SC-003): identifier value → primary key → ordinal.
        record = None
        if id_binding is not None:
            record = row.get(id_binding.source_path)
        if record is None:
            record = row.get("id")
        if record is None:
            record = idx
        source_ref = {"system": dsn_ref, "entity": table, "record": str(record)}

        props: dict[str, Any] = {}
        notes: list[str] = []
        for pb in data_bindings:
            outcome = apply_transform(
                pb.transform_type, pb.transform_config, row.get(pb.source_path)
            )
            props[pb.property_iri] = outcome.value
            if outcome.note:
                notes.append(f"{pb.property_iri}: {outcome.note}")

        links: list[RowCandidate] = []
        for pb in object_bindings:
            raw = row.get(pb.source_path)
            if raw is None:
                continue
            if pb.object_resolution == "id_reference":
                match_iri = _resolve_id_reference(
                    engine, pb.target_class_iri, pb.target_id_path, raw
                )
                if match_iri is not None:
                    props[pb.property_iri] = match_iri       # materialize the link
                else:                                        # FR-023a — link candidate
                    links.append(RowCandidate(
                        candidate_kind="link",
                        extracted_properties={pb.property_iri: raw},
                        source_ref=source_ref,
                        target_class_iri=pb.target_class_iri,
                        notes=[f"{pb.property_iri}: 未找到匹配 "
                               f"{pb.target_class_iri}[{pb.target_id_path}={raw}]"],
                    ))
            # nested_object resolution is added with US2 (T027).

        id_val = props.get(id_binding.property_iri) if id_binding else None
        candidates.append(RowCandidate(
            candidate_kind="instance",
            extracted_properties=props,
            source_ref=source_ref,
            identifier=str(id_val) if id_val not in (None, "") else None,
            notes=notes,
        ))
        candidates.extend(links)

    logger.info("DB 声明式读取：表 %s → %d 行 → %d 候选（漂移列 %d）",
                table, len(raw_rows), len(candidates), len(drifted))
    return RowReadResult(candidates=candidates, drifted_paths=drifted)
