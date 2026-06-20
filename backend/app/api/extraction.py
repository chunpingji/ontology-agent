"""Document extraction API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.schemas.extraction import (
    ExtractionCandidateResponse,
    ExtractionConfigCreate,
    ExtractionConfigResponse,
    ExtractionJobResponse,
    ReviewRequest,
)

router = APIRouter()


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


@router.post("/jobs", response_model=ExtractionJobResponse, status_code=201)
def create_job(
    source_type: str,
    config_id: UUID | None = None,
    file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    job = ExtractionJob(
        source_type=source_type,
        source_filename=file.filename if file else None,
        source_config={"config_id": str(config_id)} if config_id else None,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)
    return job


@router.get("/jobs/{job_id}/candidates", response_model=list[ExtractionCandidateResponse])
def list_candidates(job_id: UUID, db: Session = Depends(get_db)):
    return (
        db.query(ExtractionCandidate)
        .filter(ExtractionCandidate.job_id == job_id)
        .all()
    )


@router.put("/candidates/{candidate_id}/review", response_model=ExtractionCandidateResponse)
def review_candidate(candidate_id: UUID, req: ReviewRequest, db: Session = Depends(get_db)):
    candidate = db.get(ExtractionCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404)
    candidate.review_status = req.status
    if req.edited_properties:
        candidate.extracted_properties = req.edited_properties
    db.commit()
    db.refresh(candidate)
    return candidate
