from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    get_ontology_engine,
    require_role,
)
from app.models.reasoning import ReasoningExecution
from app.schemas.integration import (
    ConclusionResponse,
    IncrementalRequest,
    IncrementalResponse,
)
from app.schemas.reasoning import (
    AssessmentRequest,
    AssessmentResponse,
    MACORequest,
    MACOResult,
    PDERequest,
    PDEResponse,
    RuleFired,
    RuleInfo,
    ScenarioResult,
)
from app.services.ontology_engine import OntologyEngine, ontology_engine
from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning import incremental
from app.services.reasoning.calculators import calculate_maco, calculate_pde
from app.services.reasoning.rules import (
    contamination_risk,
    drug_classification,
    equipment_dedication,
    scenario_identification,
)

router = APIRouter()


@router.post("/assess", response_model=AssessmentResponse)
def run_assessment(
    req: AssessmentRequest,
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    try:
        result = reasoning_engine.run_assessment(engine, req.drug_iri, req.equipment_iris)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return AssessmentResponse(
        drug_iri=req.drug_iri,
        equipment_iris=req.equipment_iris,
        risk_level=result.risk_level,
        rules_fired=[RuleFired(**r) for r in result.rules_fired],
        scenarios=[ScenarioResult(**s) for s in result.scenarios],
        requires_dedication=result.requires_dedication,
        maco=MACOResult(
            maco_value=result.maco.value,
            method_used=result.maco.method,
            all_methods=result.maco.all_methods,
        ) if result.maco else None,
        recommendations=result.recommendations,
    )


@router.post("/calculate/pde", response_model=PDEResponse)
def calc_pde(req: PDERequest):
    result = calculate_pde(
        pod=req.pod, bw=req.bw, f1=req.f1, f2=req.f2,
        f3=req.f3, f4=req.f4, f5=req.f5, mf=req.mf,
    )
    return PDEResponse(pde_value=result.value, parameters=result.parameters)


@router.post("/calculate/maco", response_model=MACOResult)
def calc_maco(req: MACORequest):
    try:
        result = calculate_maco(
            pde=req.pde, mbs=req.mbs, tdd_next=req.tdd_next,
            min_therapeutic_dose=req.min_therapeutic_dose,
            ld50=req.ld50, bw=req.bw, route=req.route,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return MACOResult(
        maco_value=result.value, method_used=result.method,
        all_methods=result.all_methods,
    )


_recompute_role = require_role(ROLE_SENIOR_ANALYST)  # 触发增量重算限 senior_analyst（契约 §0）


@router.post("/incremental", response_model=IncrementalResponse)
def recompute_incremental(
    req: IncrementalRequest,
    db: Session = Depends(get_db),
    _: object = Depends(_recompute_role),
):
    """事实变更后仅重算受影响子图的既有结论（禁止全量，FR-017/SC-005）。"""
    engine = ontology_engine if ontology_engine.is_loaded else None
    refreshed = incremental.recompute_subgraph(db, req.affected_subgraph, engine=engine)
    return IncrementalResponse(
        refreshed=[ConclusionResponse.model_validate(r) for r in refreshed]
    )


@router.get("/conclusions/{conclusion_id}", response_model=ConclusionResponse)
def get_conclusion(conclusion_id: UUID, db: Session = Depends(get_db)):
    """查询某结论的生效状态 / 取代链（FR-030 待签结论 effective=false）。"""
    c = db.get(ReasoningExecution, conclusion_id)
    if not c:
        raise HTTPException(404)
    return c


@router.get("/conclusions/{conclusion_id}/trace")
def get_conclusion_trace(conclusion_id: UUID, db: Session = Depends(get_db)):
    """规则链溯源：返回结论触发的规则 ID + 法规依据（FR-027/SC-007）。"""
    c = db.get(ReasoningExecution, conclusion_id)
    if not c:
        raise HTTPException(404)
    return {"rules_fired": c.rules_fired or []}


@router.get("/rules", response_model=list[RuleInfo])
def list_rules():
    rules = []
    groups = [
        ("drug_classification", drug_classification.ALL_RULES),
        ("equipment_dedication", equipment_dedication.ALL_RULES),
        ("contamination_risk", contamination_risk.ALL_RULES),
        ("scenario_identification", scenario_identification.ALL_RULES),
    ]
    for group_name, group_rules in groups:
        for fn in group_rules:
            doc = fn.__doc__ or ""
            rule_id = doc.split(":")[0].strip() if ":" in doc else fn.__name__
            rules.append(RuleInfo(
                rule_id=rule_id,
                group=group_name,
                description=doc.strip(),
            ))
    return rules
