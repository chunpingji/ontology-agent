from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_ontology_engine
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
from app.services.ontology_engine import OntologyEngine
from app.services.reasoning import engine as reasoning_engine
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
