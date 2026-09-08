"""Decision storage for the versioned calculation pipeline."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.evidence import EvidenceJobState
from app.models.reporting import CalculationDecision
from app.services import audit
from app.services.extraction.candidate_store import CandidateStore
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reasoning.pde_calculation import apply_decisions
from app.services.reasoning.rule_service import evaluate as evaluate_candidates
from app.services.reporting.template_v2 import ReportingError


def decision_history(db, job_id):
    rows = db.scalars(
        select(CalculationDecision)
        .where(CalculationDecision.job_id == job_id)
        .order_by(CalculationDecision.revision_no)
    ).all()
    for row in rows:
        if evidence_hash(row.payload) != row.content_hash:
            raise ReportingError("CALCULATION_DECISION_HASH_MISMATCH")
    return [row.payload for row in rows]


def job_calculations(db, job_id, *, candidates=None):
    return apply_decisions(
        evaluate_candidates(CandidateStore(db).list(job_id) if candidates is None else candidates),
        decision_history(db, job_id),
    )


def decide(db, job_id, body, actor):
    # Serializes decisions against reviews/publication, which update this same head.
    db.scalar(select(EvidenceJobState).where(EvidenceJobState.job_id == job_id).with_for_update())
    result = next(
        (
            r
            for r in job_calculations(db, job_id)
            if r["subject_candidate_id"] == body.subject_candidate_id
        ),
        None,
    )
    if result is None or result["calculation_id"] != body.calculation_id:
        raise ReportingError(
            "CALCULATION_INPUTS_CHANGED", "计算输入已变化，请刷新后重新处理", status=409
        )
    if result["decision_revision"] != body.expected_revision:
        raise ReportingError("CALCULATION_DECISION_CONFLICT", "取值决定已更新，请刷新", status=409)
    if body.choice in {"derived", "asserted"} and result["status"] == "incomplete":
        raise ReportingError("CALCULATION_INCOMPLETE", "请先补齐有效参数，完成校验后再选择取值")
    if body.choice in {"derived", "asserted"} and not result["linked_to_source"]:
        raise ReportingError("CALCULATION_SUBJECT_UNBOUND", "请先确认该实体与源文档的关系")
    payload = {
        "job_id": str(job_id),
        "subject_candidate_id": body.subject_candidate_id,
        "calculation_id": body.calculation_id,
        "revision": body.expected_revision + 1,
        "choice": body.choice,
        "reason": body.reason.strip(),
        "actor": actor,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "calculation": {
            k: v for k, v in result.items() if k not in {"decision", "decision_revision"}
        },
    }
    payload["decision_id"] = evidence_hash(payload)
    db.add(
        CalculationDecision(
            id=payload["decision_id"],
            payload=payload,
            content_hash=evidence_hash(payload),
            actor=actor,
            job_id=job_id,
            subject_candidate_id=body.subject_candidate_id,
            calculation_id=body.calculation_id,
            revision_no=payload["revision"],
        )
    )
    audit.append(
        db,
        "calculation.decision",
        actor=actor,
        entity_iri=result["subject_iri"],
        details={k: v for k, v in payload.items() if k != "calculation"},
        commit=False,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ReportingError("CALCULATION_DECISION_CONFLICT", status=409) from exc
    return payload
