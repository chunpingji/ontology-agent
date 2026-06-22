"""Document extraction API routes (能力二：多源抽取 + 跨源对齐 + 人工审核闭环)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    Identity,
    get_current_user_sse,
    get_ontology_engine,
    require_role,
)
from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.schemas.extraction import (
    CandidateGroup,
    ExtractionCandidateResponse,
    ExtractionConfigCreate,
    ExtractionConfigResponse,
    ExtractionJobResponse,
    GroupedCandidatesResponse,
    MergeRequest,
    ReviewRequest,
    SplitRequest,
)
from app.services import audit
from app.services.extraction.pipeline import run_extraction_pipeline
from app.services.extraction.progress import progress_bus
from app.services.ontology_engine import OntologyEngine

router = APIRouter()

_analyst = require_role(ROLE_SENIOR_ANALYST)


# --- Extraction Configs ---

@router.get("/configs", response_model=list[ExtractionConfigResponse])
def list_configs(db: Session = Depends(get_db)):
    return db.query(ExtractionConfig).filter(ExtractionConfig.is_active.is_(True)).all()


@router.post("/configs", response_model=ExtractionConfigResponse, status_code=201)
def create_config(req: ExtractionConfigCreate, db: Session = Depends(get_db)):
    config = ExtractionConfig(**req.model_dump())
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.delete("/configs/{config_id}", status_code=204)
def delete_config(config_id: UUID, db: Session = Depends(get_db)):
    config = db.get(ExtractionConfig, config_id)
    if not config:
        raise HTTPException(404)
    config.is_active = False
    db.commit()


# --- Extraction Jobs ---

@router.get("/jobs", response_model=list[ExtractionJobResponse])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(ExtractionJob).order_by(ExtractionJob.created_at.desc()).all()


async def _run_pipeline_bg(job_id, config_id, file_path, engine, db: Session):
    """后台运行流水线。复用请求会话（FastAPI 在后台任务后才做依赖清理）。"""
    job = db.get(ExtractionJob, job_id)
    config = db.get(ExtractionConfig, config_id)
    if job is None or config is None:
        return
    await run_extraction_pipeline(job, config, file_path, engine, db)


@router.post("/jobs", response_model=ExtractionJobResponse, status_code=202)
async def create_job(
    background: BackgroundTasks,
    source_type: str = Form(...),
    config_id: UUID = Form(...),
    file: UploadFile | None = File(None),
    db_source: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """创建抽取作业并**真实触发**流水线（状态置 running，非 pending, FR-001/002）。"""
    config = db.get(ExtractionConfig, config_id)
    if not config:
        raise HTTPException(404, "extraction config not found")

    file_path: Path | None = None
    if file is not None:
        suffix = Path(file.filename or "").suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(await file.read())
        tmp.close()
        file_path = Path(tmp.name)

    source_cfg: dict = {"config_id": str(config_id)}
    if db_source:
        source_cfg["db_source"] = json.loads(db_source)

    job = ExtractionJob(
        source_type=source_type,
        source_filename=file.filename if file else source_type,
        source_config=source_cfg,
        status="running",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    audit.append(
        db, "extraction.job.create", actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": source_type, "config_id": str(config_id)},
    )

    background.add_task(_run_pipeline_bg, job.id, config_id, file_path, engine, db)
    return job


@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)
    return job


@router.get("/jobs/{job_id}/progress")
async def job_progress(
    job_id: UUID,
    identity: Identity = Depends(get_current_user_sse),
):
    """SSE：逐阶段推送 parsing→extracting→aligning→reviewing（FR-002）。"""

    async def event_gen():
        async for ev in progress_bus.stream(str(job_id)):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/jobs/{job_id}/candidates", response_model=GroupedCandidatesResponse)
def list_candidates(job_id: UUID, db: Session = Depends(get_db)):
    """按 group_key 归组返回候选；未归组的单列（FR-009/SC-003）。"""
    rows = db.query(ExtractionCandidate).filter(ExtractionCandidate.job_id == job_id).all()
    groups: dict[str, list[ExtractionCandidate]] = {}
    ungrouped: list[ExtractionCandidate] = []
    for c in rows:
        if c.group_key:
            groups.setdefault(c.group_key, []).append(c)
        else:
            ungrouped.append(c)

    group_models = []
    for gkey, members in groups.items():
        canonical = next((m for m in members if m.is_canonical), None)
        group_models.append(CandidateGroup(
            group_key=gkey,
            canonical_candidate_id=canonical.id if canonical else None,
            candidates=[ExtractionCandidateResponse.model_validate(m) for m in members],
        ))

    return GroupedCandidatesResponse(
        job_id=job_id,
        groups=group_models,
        ungrouped=[ExtractionCandidateResponse.model_validate(m) for m in ungrouped],
    )


def _commit_candidate(candidate: ExtractionCandidate, engine, db: Session) -> str:
    """确认入库：落 committed_iri 并尽力投影到 KG/影子表（VR-2）。"""
    iri = candidate.aligned_iri or f"{candidate.target_class_iri}_{candidate.id.hex[:8]}"
    candidate.committed_iri = iri
    candidate.review_status = "committed"
    try:  # best-effort World projection; fake engine 下为 no-op
        engine.project_entities([candidate.extracted_properties])
    except Exception:  # pragma: no cover
        pass
    return iri


@router.put("/candidates/{candidate_id}/review", response_model=ExtractionCandidateResponse)
def review_candidate(
    candidate_id: UUID,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    candidate = db.get(ExtractionCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404)
    if req.edited_properties:
        candidate.extracted_properties = req.edited_properties

    if req.status == "confirmed":
        _commit_candidate(candidate, engine, db)
        action = "extraction.candidate.commit"
    else:
        candidate.review_status = req.status
        action = "extraction.candidate.review"

    db.commit()
    db.refresh(candidate)
    audit.append(
        db, action, actor=identity.username, entity_iri=str(candidate_id),
        details={"status": candidate.review_status},
    )
    return candidate


@router.post("/candidates/merge", response_model=list[ExtractionCandidateResponse])
def merge_candidates(
    req: MergeRequest,
    db: Session = Depends(get_db),
    identity: Identity = Depends(_analyst),
):
    """合并：source 候选并入 target（落 merged_into_id, FR-010）。"""
    target = db.get(ExtractionCandidate, req.target_id)
    if not target:
        raise HTTPException(404, "target candidate not found")
    affected = [target]
    for sid in req.source_ids:
        src = db.get(ExtractionCandidate, sid)
        if not src:
            continue
        src.merged_into_id = target.id
        src.review_status = "merged"
        affected.append(src)
    db.commit()
    audit.append(
        db, "extraction.candidate.merge", actor=identity.username,
        entity_iri=str(req.target_id),
        details={"source_ids": [str(s) for s in req.source_ids]},
    )
    for c in affected:
        db.refresh(c)
    return affected


@router.post("/candidates/{candidate_id}/split", response_model=list[ExtractionCandidateResponse])
def split_candidate(
    candidate_id: UUID,
    req: SplitRequest,
    db: Session = Depends(get_db),
    identity: Identity = Depends(_analyst),
):
    """拆分：原候选置 split，派生若干新候选（FR-010）。"""
    candidate = db.get(ExtractionCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404)
    candidate.review_status = "split"
    derived = []
    for props in req.splits:
        nc = ExtractionCandidate(
            job_id=candidate.job_id,
            target_class_iri=candidate.target_class_iri,
            extracted_properties=props,
            candidate_kind=candidate.candidate_kind,
            source_ref=candidate.source_ref,
            review_status="pending",
            alignment_result="new",
        )
        db.add(nc)
        derived.append(nc)
    db.commit()
    audit.append(
        db, "extraction.candidate.split", actor=identity.username,
        entity_iri=str(candidate_id), details={"derived": len(derived)},
    )
    for c in derived:
        db.refresh(c)
    return derived
