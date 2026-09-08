"""Evidence workflow API. Review is never a side effect of extraction or commit."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    Identity,
    get_current_user,
    get_ontology_engine,
    require_role,
)
from app.models.evidence import (
    DocumentAnalysisRecord,
    EvidenceAssertion,
    EvidenceCandidateRecord,
    EvidenceCommit,
    EvidenceJobState,
)
from app.models.extraction import ExtractionJob
from app.schemas.evidence import Candidate, CandidateRef, EvidenceModel
from app.services.extraction.candidate_store import CandidateConflict, CandidateStore
from app.services.extraction.candidate_validation import validate_entered_candidate
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.external_records import configured_external_records
from app.services.extraction.extraction_tasks import SEMANTIC_VERSION, semantic_schema_from_engine
from app.services.extraction.performance import profiled
from app.services.fact_commit import FactCommitService
from app.services.ontology_instance_writer import EvidenceInstanceWriter
from app.services.reporting.template_v2 import ReportingError

router = APIRouter(dependencies=[Depends(get_current_user)])
_analyst = require_role(ROLE_SENIOR_ANALYST)


class CandidateCreate(EvidenceModel):
    request_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    candidate: dict[str, Any]


class CalculationReview(EvidenceModel):
    subject_candidate_id: str = Field(min_length=1)
    calculation_id: str = Field(min_length=64, max_length=64)
    expected_revision: int = Field(ge=0)
    choice: Literal["derived", "asserted", "rejected", "pending"]
    reason: str = Field(min_length=1, max_length=4000)


@router.post("/jobs/{job_id}/calculations/decision")
def review_calculation(job_id: UUID, body: CalculationReview, db: Session = Depends(get_db),
                       identity: Identity = Depends(_analyst)):
    from app.services.reasoning.calculation_review import decide

    _job(db, job_id)
    if not body.reason.strip():
        raise HTTPException(422, "请填写处理理由")
    with _errors():
        return decide(db, job_id, body, identity.username)


class ExtractOptions(EvidenceModel):
    retry_failed: bool = False
    reason: str = Field(default="", max_length=4000)
    pause_after: int | None = Field(default=None, ge=1, le=32)


class CandidateReview(EvidenceModel):
    expected_revision: int = Field(ge=1)
    expected_review_status: Literal["pending", "confirmed", "rejected"] | None = None
    decision: Literal["confirmed", "rejected"]
    reason: str = Field(min_length=1, max_length=4000)
    edited_payload: dict[str, Any] | None = None


class CandidateResolve(EvidenceModel):
    expected_revision: int = Field(ge=1)
    target: CandidateRef
    reason: str = Field(min_length=1, max_length=4000)


class CommitItem(EvidenceModel):
    candidate_id: str = Field(min_length=1)
    revision: int = Field(ge=1)


class CommitRequest(EvidenceModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    items: list[CommitItem] = Field(min_length=1, max_length=1000)


class CoverageAction(EvidenceModel):
    manifest_id: str = Field(min_length=1)
    template_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=4000)
    coverage_task_ids: list[str] = Field(default_factory=list, max_length=1000)


@contextmanager
def _errors():
    try:
        yield
    except CandidateConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReportingError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _job(db, job_id):
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "extraction job not found")
    return job


def _analysis(db, job_id):
    state = db.get(EvidenceJobState, job_id, populate_existing=True)
    row = db.get(DocumentAnalysisRecord, state.analysis_id) if state and state.analysis_id else None
    return DocumentIR.model_validate(row.payload) if row else None


def _validator(db, job_id, engine, actor, reason):
    from app.services.ontology_model_context import capture_schema

    schema = semantic_schema_from_engine(engine)
    capture_schema(db, schema)
    ir = _analysis(db, job_id)

    def validate(candidate):
        candidates = {c.candidate_id: c for c in CandidateStore(db).list(job_id)}
        return validate_entered_candidate(
            candidate,
            candidates,
            schema,
            actor=actor,
            reason=reason,
            records=configured_external_records(),
            ir=ir,
        )

    return validate


def _candidate_json(candidate, labels=None):
    return {
        **candidate.model_dump(mode="json"),
        "positive_eligible": candidate.positive_eligible,
        "class_label": (labels or {}).get(candidate.class_iri),
        "predicate_label": (labels or {}).get(candidate.predicate_iri),
    }


def _commit_json(commit):
    return {
        "commit_id": commit.id,
        "job_id": str(commit.job_id),
        "status": commit.status,
        "snapshot_id": commit.snapshot_id,
        "attempts": commit.attempts,
        "error": commit.error,
        "content_hash": commit.content_hash,
        "items": commit.manifest["requested_items"],
    }


def _service(db, engine):
    return FactCommitService(db, EvidenceInstanceWriter(engine, settings.evidence_world_dir))


def _graph_schema(job, candidates, schema):
    """Expose the source's ontology shape independently of recognized assertions."""
    document_classes = {
        candidate.class_iri
        for candidate in candidates
        if candidate.kind == "entity" and candidate.identity.get("document_root")
    } - {None}
    document_class = (job.source_config or {}).get("doc_class_iri") or (
        next(iter(document_classes)) if len(document_classes) == 1 else None
    )
    used_classes = {candidate.class_iri for candidate in candidates if candidate.kind == "entity"}
    used_classes.add(document_class)
    # Include range labels even when no object instance has been recognized yet.
    range_classes = {
        iri
        for class_iri in used_classes
        for relation in schema.get(class_iri, {}).get("relationships", [])
        for iri in relation.get("range", [])
    }
    classes = {}
    for iri in sorted((used_classes | range_classes) - {None}):
        if iri not in schema:
            continue
        definition = schema[iri]
        classes[iri] = {
            "label": definition.get("label"),
            "parents": definition.get("parents", []),
            "properties": [
                {"iri": prop["iri"], "label": prop.get("label")}
                for prop in definition.get("properties", [])
            ],
            "relationships": [
                {"iri": prop["iri"], "label": prop.get("label"), "range": prop.get("range", [])}
                for prop in definition.get("relationships", [])
            ],
        }
    return {
        "document_class_iri": document_class,
        "document_label": job.source_filename,
        "classes": classes,
    }


@router.get("/jobs/{job_id}/evidence")
@profiled("evidence_read")
def list_evidence(job_id: UUID, db: Session = Depends(get_db), engine=Depends(get_ontology_engine),
                  debug: bool = False):
    from app.services.reasoning.calculation_review import job_calculations
    from app.services.reasoning.rule_service import required_checks

    job = _job(db, job_id)
    state = db.get(EvidenceJobState, job_id, populate_existing=True)
    commits = db.scalars(
        select(EvidenceCommit)
        .where(EvidenceCommit.job_id == job_id)
        .order_by(EvidenceCommit.created_at.desc())
        .limit(100)
    )
    run = state.extraction_run if state else None
    versions = sorted(
        {
            candidate.get("extractor_version", "")
            for candidate in (run or {}).get("candidates", [])
            if candidate.get("extractor_version", "").startswith("generic-semantic-")
        }
    )
    if (run or {}).get("extractor_version"):
        versions = [run["extractor_version"]]
    schema = semantic_schema_from_engine(engine)
    candidates = CandidateStore(db).list(job_id)
    labels = {}
    for iri, definition in schema.items():
        labels[iri] = definition.get("label")
        for predicate in [*definition.get("properties", []), *definition.get("relationships", [])]:
            labels[predicate["iri"]] = predicate.get("label")
    graph_schema = _graph_schema(job, candidates, schema)
    return {
        "schema_version": 1,
        "candidates": [_candidate_json(c, labels) for c in candidates],
        "graph_schema": graph_schema,
        "calculation_required": bool(required_checks([
            {"source_slot_id": "document", "class_iri": graph_schema["document_class_iri"]}
        ])),
        "calculations": job_calculations(db, job_id, candidates=candidates),
        "run": run if debug or run is None else {
            key: value for key, value in run.items()
            if key not in {"candidates", "tasks", "checkpoint"}
        },
        "revision": state.revision if state else 0,
        "extraction_version": {
            "current": SEMANTIC_VERSION,
            "stored": versions,
            "outdated": bool(versions) and versions != [SEMANTIC_VERSION],
        },
        "analysis_id": state.analysis_id if state else None,
        "snapshot_id": state.snapshot_id if state else None,
        "commits": [_commit_json(c) for c in commits],
    }


@router.post("/jobs/{job_id}/evidence/extract", status_code=202)
def extract_evidence(
    job_id: UUID,
    background: BackgroundTasks,
    req: ExtractOptions | None = None,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    from app.api.extraction import _enqueue_annotation

    if req and req.retry_failed and not req.reason.strip():
        raise HTTPException(422, "an explicit retry reason is required")
    job = _job(db, job_id)
    if job.source_type != "word" or not job.document_path or not Path(job.document_path).is_file():
        raise HTTPException(422, "a persisted Word source is required")
    return _enqueue_annotation(
        job_id, background, engine, db, mode="continue", actor=identity.username,
        retry_failed=bool(req and req.retry_failed), pause_after=req.pause_after if req else None,
        reason=req.reason if req else "",
    )


@router.get("/jobs/{job_id}/evidence/coverage")
def evidence_coverage(
    job_id: UUID,
    template_id: UUID | None = None,
    snapshot_id: str | None = None,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(get_current_user),
):
    from app.services.reporting.coverage_v2 import build_evidence_coverage

    with _errors():
        inputs = build_evidence_coverage(
            db,
            _job(db, job_id),
            schema=semantic_schema_from_engine(engine),
            actor=identity.username,
            template_id=template_id,
            snapshot_id=snapshot_id,
        )
        return {
            key: value
            for key, value in inputs.items()
            if key not in {"fact_snapshot", "semantic_schema", "template_schema", "rules"}
        }


@router.post("/jobs/{job_id}/evidence/discovery")
def confirm_discovery(
    job_id: UUID,
    req: CoverageAction,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    from app.services import audit
    from app.services.extraction.gap_workflow import update_run
    from app.services.reporting.coverage_v2 import build_report_inputs

    with _errors():
        state = db.get(EvidenceJobState, job_id, populate_existing=True)
        revision = state.revision if state else None
        inputs = build_report_inputs(
            db,
            _job(db, job_id),
            schema=semantic_schema_from_engine(engine),
            actor=identity.username,
            template_id=req.template_id,
        )
        if inputs["manifest_id"] != req.manifest_id:
            raise CandidateConflict("coverage/discovery revision changed; reload before confirming")
        state = db.get(EvidenceJobState, job_id)
        if (
            not state
            or not state.snapshot_id
            or (state.extraction_run or {}).get("completion") != "complete"
        ):
            raise ValueError("complete source processing and a published snapshot are required")
        tasks = {t.get("coverage_task_id"): t for t in inputs["tasks"]}
        decisions = dict((state.extraction_run or {}).get("discovery_decisions", {}))
        if not req.coverage_task_ids:
            raise ValueError("explicit coverage task IDs are required")
        for key in req.coverage_task_ids:
            task = tasks.get(key)
            if (
                not task
                or task.get("cross_source")
                or task["status"] in {"pending_review", "conflict", "invalid", "unavailable"}
            ):
                raise ValueError("cannot close unresolved or conflicting discovery")
            decisions[key] = {
                "actor": identity.username,
                "reason": req.reason,
                "snapshot_id": state.snapshot_id,
                "discovery_revision": inputs["discovery_revision"],
                "object_iris": [o["instance_iri"] for o in task.get("objects", [])],
                "report_selector_key": task.get("report_selector_key"),
            }
        update_run(db, job_id, revision, {**state.extraction_run, "discovery_decisions": decisions})
        audit.append(
            db,
            "evidence.discovery_confirm",
            actor=identity.username,
            entity_iri=str(job_id),
            details={
                "manifest_id": req.manifest_id,
                "task_ids": req.coverage_task_ids,
                "reason": req.reason,
            },
            commit=False,
        )
        db.commit()
        return evidence_coverage(job_id, req.template_id, None, db, engine, identity)


@router.post("/jobs/{job_id}/evidence/fill-gaps")
def fill_evidence_gaps(
    job_id: UUID,
    req: CoverageAction,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    from app.services import audit
    from app.services.extraction.gap_extraction import run_gap_round
    from app.services.extraction.gap_workflow import finish_round, reserve_round
    from app.services.extraction.local_semantic_model import configured_generic_runner
    from app.services.fact_selector import FactSelector
    from app.services.reporting.coverage_v2 import build_report_inputs

    with _errors():
        job = _job(db, job_id)
        state = db.get(EvidenceJobState, job_id, populate_existing=True)
        revision = state.revision if state else None
        runner = configured_generic_runner(engine)
        inputs = build_report_inputs(
            db, job, schema=runner.schema, actor=identity.username, template_id=req.template_id
        )
        if inputs["manifest_id"] != req.manifest_id:
            raise CandidateConflict("coverage changed; reload before filling gaps")
        state = db.get(EvidenceJobState, job_id)
        ir = _analysis(db, job_id)
        if not state or not ir or not inputs["snapshot_id"]:
            return {"reason": "published_snapshot_and_source_required", "created": 0}
        key = stable_id(
            "gap-round",
            [
                ir.analysis_id,
                inputs["template_hash"],
                inputs["discovery_revision"],
                inputs["snapshot_id"],
                runner.input_id(ir, inputs["input_class_iri"]),
            ],
        )
        candidates = CandidateStore(db).list(job_id)
        if any(t["status"] == "pending_review" for t in inputs["tasks"]):
            return {"reason": "pending_review", "created": 0}
        entry, stopped = reserve_round(
            db, job_id, revision, key, identity.username, runner.budget.max_gap_rounds
        )
        if stopped:
            return {"reason": stopped, "created": 0}
        try:
            values, reason, task_history = run_gap_round(
                ir,
                inputs,
                candidates,
                FactSelector(inputs["fact_snapshot"], schema=runner.schema),
                runner,
            )
        except Exception:
            db.rollback()
            finish_round(db, job_id, key, "execution_failed", 0, [])
            raise
        from app.services.ontology_model_context import capture_schema

        capture_schema(db, runner.schema)
        stored = (
            CandidateStore(db).persist_validated(job_id, values, actor=identity.username)
            if values
            else []
        )
        finish_round(db, job_id, key, reason, len(stored), task_history)
        audit.append(
            db,
            "evidence.fill_gaps",
            actor=identity.username,
            entity_iri=str(job_id),
            details={"input_key": key, "reason": reason, "created": len(stored)},
            commit=False,
        )
        db.commit()
        return {"reason": reason, "created": len(stored), "round": entry["round"]}


@router.post("/jobs/{job_id}/evidence/candidates", status_code=201)
def create_candidate(
    job_id: UUID,
    req: CandidateCreate,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    _job(db, job_id)
    with _errors():
        payload = dict(req.candidate)
        if payload.get("identity"):
            raise ValueError("instance identity is server-owned; use explicit entity resolution")
        for key in (
            "candidate_id",
            "revision",
            "validation_status",
            "validation_issues",
            "review_status",
            "review_source",
            "review_reason",
            "class_label",
            "predicate_label",
            "commit_status",
            "ontology_release",
            "model_identity",
            "task_id",
            "extractor_version",
            "positive_eligible",
            "path_root",
            "relationship_path",
            "dependency_refs",
            "bindings",
        ):
            payload.pop(key, None)
        sources = []
        requested_sources = payload.get("provenance", [])
        if not isinstance(requested_sources, list) or not all(
            isinstance(p, dict) for p in requested_sources
        ):
            raise ValueError("provenance must be an array of typed sources")
        for source in requested_sources:
            source = dict(source)
            if source.get("kind") == "manual":
                source.update(
                    actor=identity.username,
                    review_id="server-pending",
                    reason=req.reason,
                    entered_at=None,
                    previous_value=None,
                )
            sources.append(source)
        payload["provenance"] = sources
        request_hash = evidence_hash([payload, req.reason, identity.username])
        request_key = stable_id("entry-request", [str(job_id), req.request_key])
        store = CandidateStore(db)
        for existing in store.list(job_id):
            if existing.identity.get("request_key") == request_key:
                if existing.identity.get("request_hash") != request_hash:
                    raise CandidateConflict("request key already used with different content")
                return _candidate_json(existing)
        payload.update(
            candidate_id=request_key,
            identity={"request_key": request_key, "request_hash": request_hash},
            validation_status="pending",
            review_status="pending",
            commit_status="not_requested",
        )
        candidate = _validator(db, job_id, engine, identity.username, req.reason)(
            Candidate.model_validate(payload)
        )
        saved = store.persist_validated(job_id, [candidate], actor=identity.username)[0]
        if saved.identity.get("request_hash") != request_hash:
            raise CandidateConflict("concurrent request key reused with different content")
        return _candidate_json(saved)


@router.put("/evidence/candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: str,
    req: CandidateReview,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    with _errors():
        store = CandidateStore(db)
        current = store.get(candidate_id)
        row = db.get(EvidenceCandidateRecord, candidate_id)
        edits = req.edited_payload
        if edits is not None:
            allowed = {
                "text",
                "class_iri",
                "subject",
                "object",
                "predicate_iri",
                "literal",
                "assertion_status",
                "condition_anchors",
                "condition_provenance_indexes",
                "applicable_at",
                "provenance",
                "scope",
            }
            if set(edits) - allowed:
                raise ValueError("edited payload contains server-owned fields")
            # Preserve source identity; manual overrides record the previous value.
            if "provenance" in edits:
                edits = {
                    **edits,
                    "provenance": [
                        {
                            **p,
                            "previous_value": current.literal.model_dump(mode="json")
                            if current.literal
                            else current.text,
                        }
                        if p.get("kind") == "manual"
                        else p
                        for p in edits["provenance"]
                    ],
                }
        return _candidate_json(
            store.review(
                candidate_id,
                req.expected_revision,
                req.decision,
                req.reason,
                identity.username,
                edited_payload=edits,
                validator=_validator(db, row.job_id, engine, identity.username, req.reason),
                expected_review_status=req.expected_review_status,
            )
        )


@router.post("/evidence/candidates/{candidate_id}/resolve")
def resolve_candidate(
    candidate_id: str,
    req: CandidateResolve,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    with _errors():
        store = CandidateStore(db)
        store.get(candidate_id)
        row = db.get(EvidenceCandidateRecord, candidate_id)
        return _candidate_json(
            store.resolve(
                candidate_id,
                req.expected_revision,
                req.target.candidate_id,
                req.target.revision,
                req.reason,
                identity.username,
                validator=_validator(db, row.job_id, engine, identity.username, req.reason),
            )
        )


@router.post("/jobs/{job_id}/evidence/commits")
def request_commit(
    job_id: UUID,
    req: CommitRequest,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    _job(db, job_id)
    with _errors():
        service = _service(db, engine)
        commit = service.request(
            job_id, req.idempotency_key, [i.model_dump() for i in req.items], identity.username
        )
        return _commit_json(service.apply(commit.id))


@router.get("/evidence/commits/{commit_id}")
def get_commit(commit_id: str, db: Session = Depends(get_db)):
    commit = db.get(EvidenceCommit, commit_id, populate_existing=True)
    if commit is None:
        raise HTTPException(404, "commit not found")
    return _commit_json(commit)


@router.post("/evidence/commits/{commit_id}/retry")
def retry_commit(
    commit_id: str,
    db: Session = Depends(get_db),
    engine=Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    with _errors():
        from app.services import audit

        get_commit(commit_id, db)
        audit.append(db, "evidence.commit_retry", actor=identity.username, entity_iri=commit_id)
        return _commit_json(_service(db, engine).apply(commit_id))


@router.get("/jobs/{job_id}/evidence/snapshot")
def get_snapshot(job_id: UUID, snapshot_id: str | None = None, db: Session = Depends(get_db)):
    _job(db, job_id)
    with _errors():
        snapshot = FactCommitService(db, None).published_snapshot(job_id, snapshot_id)
        return {
            "published": snapshot is not None,
            **(snapshot or {"snapshot_id": None, "assertions": []}),
        }


@router.get("/evidence/assertions/{assertion_id}/provenance")
def get_provenance(assertion_id: str, db: Session = Depends(get_db)):
    assertion = db.get(EvidenceAssertion, assertion_id)
    commit = db.get(EvidenceCommit, assertion.commit_id) if assertion else None
    if assertion is None or commit is None or commit.status != "succeeded":
        raise HTTPException(404, "published assertion not found")
    candidate = Candidate.model_validate(assertion.payload["candidate"])
    replays = []
    for source in candidate.provenance:
        if source.kind == "document":
            documents = db.scalars(
                select(DocumentAnalysisRecord).where(
                    DocumentAnalysisRecord.document_hash == source.anchors[0].document_hash,
                    DocumentAnalysisRecord.structure_hash == source.anchors[0].structure_hash,
                    DocumentAnalysisRecord.role == source.document_role,
                )
            )
            row = next(iter(documents), None)
            if row is None:
                replays.append({"status": "unavailable", "reason": "analysis_not_retained"})
            else:
                with _errors():
                    ir = DocumentIR.model_validate(row.payload)
                    replays.append(
                        {
                            "status": "valid",
                            "analysis_id": ir.analysis_id,
                            "excerpts": [ir.resolve(a) for a in source.anchors],
                        }
                    )
        else:
            replays.append(
                {"status": "retained_snapshot", "provenance": source.model_dump(mode="json")}
            )
    return {
        "assertion_id": assertion_id,
        "snapshot_id": commit.snapshot_id,
        "provenance": [p.model_dump(mode="json") for p in candidate.provenance],
        "bindings": [b.model_dump(mode="json") for b in candidate.bindings],
        "replays": replays,
    }
