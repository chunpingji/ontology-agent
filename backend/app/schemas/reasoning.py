from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AssessmentRequest(BaseModel):
    drug_iri: str
    equipment_iris: list[str]
    assessment_type: str = "full"


class RuleFired(BaseModel):
    rule_id: str
    rule_group: str
    description: str
    inputs: dict[str, Any] = {}
    conclusion: dict[str, Any] = {}


class ScenarioResult(BaseModel):
    scenario_iri: str
    scenario_name: str
    requirements: dict[str, Any] = {}


class AssessmentResponse(BaseModel):
    execution_id: UUID | None = None
    drug_iri: str
    equipment_iris: list[str]
    risk_level: str | None = None
    rules_fired: list[RuleFired] = []
    scenarios: list[ScenarioResult] = []
    requires_dedication: bool = False
    maco: MACOResult | None = None
    recommendations: list[str] = []


class PDERequest(BaseModel):
    pod: float
    bw: float = 50.0
    f1: float = 1.0
    f2: float = 10.0
    f3: float = 1.0
    f4: float = 1.0
    f5: float = 1.0
    mf: float = 1.0


class PDEResponse(BaseModel):
    pde_value: float
    parameters: dict[str, float]


class MACORequest(BaseModel):
    pde: float | None = None
    mbs: float
    tdd_next: float
    min_therapeutic_dose: float | None = None
    ld50: float | None = None
    bw: float = 50.0
    route: str = "oral"


class MACOResult(BaseModel):
    maco_value: float
    method_used: str
    all_methods: dict[str, float] = {}
    unit: str = "mg"


class RuleInfo(BaseModel):
    rule_id: str
    group: str
    description: str
    regulation_ref: str | None = None
