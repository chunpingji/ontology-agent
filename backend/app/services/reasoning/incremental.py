"""增量重算编排（能力三, R6, FR-017/027, SC-005）。

事实变更后，仅对受影响子图（设备/产品/区域）重算既有结论（**禁止全量**, VR-8）：
重跑 `run_assessment`（引擎可用时）或保留既有结论结果，生成刷新后的新结论并将旧
结论标记 `superseded_by`。失效/取代链可溯源（data-model §1.2）。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.reasoning import ReasoningExecution
from app.services import audit
from app.services.ontology_engine import OntologyEngine
from app.services.reasoning import engine as reasoning_engine

logger = logging.getLogger(__name__)


def _ids_of(execution: ReasoningExecution, key: str) -> set[str]:
    ids: set[str] = set()
    sub = execution.affected_subgraph or {}
    ids.update(str(x) for x in sub.get(key, []))
    params = execution.input_params or {}
    if key == "equipment":
        ids.update(str(x) for x in params.get("equipment_iris", []))
    if key == "product" and params.get("drug_iri"):
        ids.add(str(params["drug_iri"]))
    return ids


def _affected(execution: ReasoningExecution, subgraph: dict) -> bool:
    for key in ("equipment", "product", "area"):
        requested = {str(x) for x in subgraph.get(key, [])}
        if requested and (_ids_of(execution, key) & requested):
            return True
    return False


def recompute_subgraph(
    db: Session,
    subgraph: dict,
    engine: OntologyEngine | None = None,
) -> list[ReasoningExecution]:
    """仅重算与 `subgraph` 相交的、当前生效的结论。返回刷新后的新结论。"""
    candidates = (
        db.query(ReasoningExecution)
        .filter(ReasoningExecution.effective.is_(True))
        .filter(ReasoningExecution.superseded_by.is_(None))
        .all()
    )
    affected = [e for e in candidates if _affected(e, subgraph)]

    refreshed: list[ReasoningExecution] = []
    for old in affected:
        results = old.results
        risk = old.risk_level
        rules = old.rules_fired
        params = old.input_params or {}
        if engine is not None:
            try:  # 引擎可用→真实重算；不可用（如 fake）→保留既有结论结果。
                res = reasoning_engine.run_assessment(
                    engine, params.get("drug_iri"), params.get("equipment_iris", []),
                )
                results = {"risk_level": res.risk_level,
                           "requires_dedication": res.requires_dedication}
                risk = res.risk_level
                rules = res.rules_fired
            except Exception as exc:  # noqa: BLE001
                logger.info("recompute fell back to prior result: %s", exc)

        new = ReasoningExecution(
            execution_type=old.execution_type,
            input_params=params,
            rules_fired=rules,
            results=results,
            risk_level=risk,
            affected_subgraph={k: list(v) for k, v in subgraph.items() if v},
            requires_signature=old.requires_signature,
            effective=not old.requires_signature,  # 需签名者待签后方生效（FR-030）
        )
        db.add(new)
        db.flush()  # 取得 new.id
        old.superseded_by = new.id
        old.effective = False
        refreshed.append(new)

    db.commit()
    for r in refreshed:
        db.refresh(r)

    # 推理步骤留痕：每条刷新结论写入哈希链，记录取代链与生效态（FR-028）。
    for old, new in zip(affected, refreshed):
        audit.append(
            db, "reasoning.recompute", actor="system",
            entity_iri=str(new.id),
            details={"superseded": str(old.id), "risk_level": new.risk_level,
                     "effective": new.effective},
            commit=False,
        )
    if refreshed:
        db.commit()

    # 结论生效后自动编排动作（FR-020）；未生效（待签）者由引擎置 suppressed。
    from app.services.reasoning.action_engine import ActionEngine

    action_engine = ActionEngine(db)
    for r in refreshed:
        action_engine.orchestrate(r)

    logger.info("incremental recompute: %d affected, %d refreshed", len(affected), len(refreshed))
    return refreshed
