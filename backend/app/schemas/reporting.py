"""能力四 DTO：动作执行 / 风险评估报告（contracts/action-report-api）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conclusion_id: UUID
    action_type: str
    status: str
    payload: dict | None = None
    rule_chain: Any | None = None
    writeback_status: str | None = None
    result: dict | None = None
    created_at: datetime


class ActionListResponse(BaseModel):
    actions: list[ActionResponse]


class ActionPatch(BaseModel):
    status: str  # pending→executed→in_progress→done / failed


class WritebackResultRequest(BaseModel):
    writeback_status: str  # accepted / not_accepted


class SignatureInfo(BaseModel):
    signer: str
    meaning: str
    signed_at: datetime


class RiskReportResponse(BaseModel):
    conclusion_id: UUID
    effective: bool
    classification: dict
    dedication_decision: str
    contamination_scores: dict
    cfdi_scenarios: list[str]
    maco: dict
    rule_chain: list[dict]
    signature: SignatureInfo | None = None
    pdf_url: str
