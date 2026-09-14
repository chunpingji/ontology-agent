"""Read report summaries without loading stored report bodies or graph snapshots."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.extraction import ExtractionJob, GeneratedReport
from app.schemas.extraction import GeneratedReportListResponse, GeneratedReportSummary


def visible_report_conditions(actor: str):
    """Keep global summaries and the existing per-job list equally visible."""
    return (
        or_(GeneratedReport.report_type != "batch_record_demo", GeneratedReport.actor == actor),
        GeneratedReport.deleted_at.is_(None),
        or_(
            GeneratedReport.report_status == "completed",
            and_(GeneratedReport.report_status.is_(None), GeneratedReport.file_size > 0),
        ),
    )


def list_report_summaries(
    db: Session, actor: str, *, page: int, page_size: int
) -> GeneratedReportListResponse:
    conditions = visible_report_conditions(actor)
    total = db.scalar(
        select(func.count()).select_from(GeneratedReport).join(ExtractionJob).where(*conditions)
    )
    rows = db.execute(
        select(
            GeneratedReport.id,
            GeneratedReport.job_id,
            ExtractionJob.source_filename,
            GeneratedReport.report_type,
            GeneratedReport.file_size,
            GeneratedReport.created_at,
        )
        .join(ExtractionJob)
        .where(*conditions)
        .order_by(GeneratedReport.created_at.desc(), GeneratedReport.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    return GeneratedReportListResponse(
        items=[GeneratedReportSummary(**row) for row in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )
