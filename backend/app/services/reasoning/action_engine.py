"""Action 编排引擎（能力四, R11, FR-020–023）。

结论生效后按结论内容编排动作（专用化工单/告警、灭活·再清洁任务、排期阻断 +
建议性回写、报告生成），写 `ActionExecution` 留痕并入审计哈希链。结论未签名
（`effective=False`）时动作置 `suppressed`，**不触发对外动作**（FR-030/VR-6）。
对外仅建议性回写，绝不直接改写外部权威数据（FR-022/VR-7/原则 II）。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.reasoning import ActionExecution, ReasoningExecution
from app.services import audit

logger = logging.getLogger(__name__)


def _plan(results: dict) -> list[str]:
    """从结论结果推导应编排的动作类型（FR-020–022）。"""
    actions: list[str] = []
    if results.get("requires_dedication"):
        actions += ["dedication_work_order", "alert"]
    if results.get("requires_inactivation"):
        actions.append("inactivation_task")
    if results.get("requires_recleaning"):
        actions.append("recleaning_task")
    if results.get("schedule_conflict"):
        actions += ["schedule_block", "advisory_writeback"]
    return actions


class ActionEngine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def orchestrate(self, conclusion: ReasoningExecution) -> list[ActionExecution]:
        """为一个结论编排并落库动作执行记录，返回创建的动作列表。"""
        results = conclusion.results or {}
        params = conclusion.input_params or {}
        equipment = (params.get("equipment_iris") or [None])[0]
        rule_chain = conclusion.rules_fired or []
        # 未生效（待 QA 签名）→ 抑制对外动作（FR-030/VR-6）。
        suppressed = not conclusion.effective
        status = "suppressed" if suppressed else "pending"

        created: list[ActionExecution] = []
        for action_type in _plan(results):
            payload = {"equipment": equipment, "reason": results.get("category") or action_type}
            writeback = "pending" if action_type == "advisory_writeback" else None
            act = ActionExecution(
                conclusion_id=conclusion.id,
                action_type=action_type,
                status=status,
                payload=payload,
                rule_chain=rule_chain,
                writeback_status=writeback,
            )
            self.db.add(act)
            created.append(act)

        if created:
            self.db.flush()
            audit.append(
                self.db, "action.orchestrate", actor="system",
                entity_iri=str(conclusion.id),
                details={
                    "conclusion_id": str(conclusion.id),
                    "actions": [a.action_type for a in created],
                    "suppressed": suppressed,
                },
                commit=False,
            )
            self.db.commit()
            for a in created:
                self.db.refresh(a)
        logger.info("orchestrated %d actions for conclusion %s (suppressed=%s)",
                    len(created), conclusion.id, suppressed)
        return created
