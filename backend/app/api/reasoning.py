from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    get_ontology_engine,
    require_role,
)
from app.models.reasoning import ActionExecution, ReasoningExecution
from app.schemas.integration import (
    ActionBrief,
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
from app.services import audit
from app.services.ontology_engine import OntologyEngine, ontology_engine
from app.services.reasoning import engine as reasoning_engine
from app.services.reasoning import incremental, risk
from app.services.reasoning.action_engine import ActionEngine
from app.services.reasoning.calculators import calculate_maco, calculate_pde
from app.services.reasoning.lifecycle import IllegalTransition, LifecycleState, transition
from app.services.reasoning.rules import (
    contamination_risk,
    drug_classification,
    equipment_dedication,
    scenario_identification,
)

router = APIRouter()

_assess_role = require_role(ROLE_SENIOR_ANALYST)  # 评估即落库限 senior_analyst（FR-017）


def _build_canonical_results(result) -> dict:
    """组装 canonical ``results`` dict（R2, data-model §1.1）——键集同时满足
    ``action_engine._plan`` 与 ``risk_report.build_report_json`` 及 ``risk`` 判据，
    作为落库 ``results``、下游动作编排与报告导出的共同数据源。"""
    scenarios = [
        s.get("scenario_name") or s.get("scenario_iri") or ""
        for s in (result.scenarios or [])
    ]

    # 污染评分：contamination_risk 规则的 pathway → risk_level。
    contamination_scores: dict[str, str] = {}
    for r in result.rules_fired or []:
        if r.get("rule_group") == "contamination_risk":
            pathway = (r.get("inputs") or {}).get("pathway")
            level = (r.get("conclusion") or {}).get("risk_level")
            if pathway and level:
                contamination_scores[pathway] = level

    # 高危类别：从场景/药物分类规则命中青霉素/头孢/高致敏（初始集）。
    add_classes = " ".join(
        str((r.get("conclusion") or {}).get("add_class", ""))
        for r in (result.rules_fired or [])
        if r.get("rule_group") == "drug_classification"
    )
    haystack = (" ".join(scenarios) + " " + add_classes).lower()
    hazardous: list[str] = []
    if "penicillin" in haystack:
        hazardous.append("penicillin")
    if "cephalosporin" in haystack:
        hazardous.append("cephalosporin")
    if any(k in haystack for k in ("hormonal", "sensitiz", "highactivity", "highpotency")):
        hazardous.append("highly_sensitizing")

    def _flag(name: str) -> bool:
        return any((r.get("conclusion") or {}).get(name) for r in (result.rules_fired or []))

    return {
        "category": result.risk_level,
        "risk_level": result.risk_level,
        "requires_dedication": bool(result.requires_dedication),
        "requires_inactivation": _flag("requires_inactivation"),
        "requires_recleaning": _flag("requires_recleaning"),
        "schedule_conflict": _flag("schedule_conflict"),
        "contamination_scores": contamination_scores,
        "cfdi_scenarios": scenarios,
        # AssessmentResult 未直接暴露 PDE（仅引擎内局部量）；MACOResult 无 parameters。
        # 报告侧 pde 为可选展示项，缺省置 None 不影响工作流闭合。
        "pde": None,
        "hazardous_categories": hazardous,
    }


@router.post("/assess", response_model=AssessmentResponse, status_code=201)
def run_assessment(
    req: AssessmentRequest,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: object = Depends(_assess_role),
):
    """评估即落库（G1/G2, FR-001~007）：返回结论的同时持久化为带唯一标识与初始
    生命周期状态的工作流对象，自动 arm QA 闸门并编排动作（单事务原子）。"""
    try:
        result = reasoning_engine.run_assessment(engine, req.drug_iri, req.equipment_iris)
    except ValueError as e:
        raise HTTPException(400, str(e))

    results = _build_canonical_results(result)
    requires_sig = risk.requires_qa_signature(results, result.risk_level)
    actor = getattr(identity, "username", "system")

    conc = ReasoningExecution(
        execution_type=req.assessment_type or "full",
        input_params={
            "drug_iri": req.drug_iri,
            "equipment_iris": list(req.equipment_iris),
            "assessment_type": req.assessment_type,
        },
        rules_fired=result.rules_fired,
        results=results,
        risk_level=result.risk_level,
        maco_value=result.maco.value if result.maco else None,
        maco_method=result.maco.method if result.maco else None,
        scenarios_identified=result.scenarios,
        affected_subgraph={
            "equipment": list(req.equipment_iris),
            "product": [req.drug_iri],
        },
        requires_signature=requires_sig,
    )
    db.add(conc)
    db.flush()  # 取得 conc.id
    # flush 会以列 default("effective") 填充 None；此处复位为 None，使 transition 视为
    # 初始无前态（INITIAL）并据判据落初始态（T1/T2）。
    conc.lifecycle_state = None

    target = (
        LifecycleState.PENDING_SIGNATURE if requires_sig else LifecycleState.EFFECTIVE
    )
    try:
        transition(db, conc, target, actor=actor, reason="assess persist")
    except IllegalTransition as exc:
        raise HTTPException(409, str(exc))

    audit.append(
        db, "reasoning.persist", actor=actor, entity_iri=str(conc.id),
        details={"risk_level": result.risk_level, "requires_signature": requires_sig,
                 "lifecycle_state": conc.lifecycle_state},
        commit=False,
    )
    # 据初始态编排动作：effective → pending（可派发）；pending_signature → suppressed
    # （全抑制、零对外派发, FR-003/006）。orchestrate 内部统一提交本事务。
    ActionEngine(db).orchestrate(conc)
    db.refresh(conc)

    return AssessmentResponse(
        execution_id=conc.id,
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
        lifecycle_state=conc.lifecycle_state,
        requires_signature=conc.requires_signature,
        effective=conc.effective,
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
    try:
        refreshed = incremental.recompute_subgraph(db, req.affected_subgraph, engine=engine)
    except IllegalTransition as exc:  # 取代链非法迁移 → 409（多入口一致, FR-016）
        raise HTTPException(409, str(exc))
    return IncrementalResponse(
        refreshed=[ConclusionResponse.model_validate(r) for r in refreshed]
    )


@router.get("/conclusions/{conclusion_id}", response_model=ConclusionResponse)
def get_conclusion(conclusion_id: UUID, db: Session = Depends(get_db)):
    """查询某结论的生命周期状态 / 取代链 + 已编排动作清单（FR-002/FR-030）。"""
    c = db.get(ReasoningExecution, conclusion_id)
    if not c:
        raise HTTPException(404)
    actions = (
        db.query(ActionExecution)
        .filter(ActionExecution.conclusion_id == c.id)
        .order_by(ActionExecution.created_at)
        .all()
    )
    resp = ConclusionResponse.model_validate(c)
    resp.actions = [ActionBrief.model_validate(a) for a in actions]
    return resp


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
