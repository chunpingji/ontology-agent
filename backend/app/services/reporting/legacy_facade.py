"""Old URLs resolve through V2; historical artifacts remain readable."""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.extraction import AstTemplate, ExtractionJob, GeneratedReport
from app.services.reporting.report_run_service import ReportRunService
from app.services.reporting.template_v2 import ReportingError


def selected_template(db, job_id, template_id=None):
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise ReportingError("SOURCE_NOT_FOUND", status=404)
    ref = template_id or (job.source_config or {}).get("template_id")
    row = (
        db.get(AstTemplate, UUID(str(ref)))
        if ref
        else db.scalar(
            select(AstTemplate)
            .where(AstTemplate.is_default.is_(True))
            .order_by(AstTemplate.created_at.desc())
            .limit(1)
        )
    )
    if row is None:
        raise ReportingError("TEMPLATE_NOT_FOUND", status=404)
    if row.schema_json.get("schema_version") != 2:
        raise ReportingError("TEMPLATE_MIGRATION_REQUIRED", status=409, template_id=str(row.id))
    return job, row


def run_for_job(db, job_id, actor, *, template_id=None, snapshot_id=None, preview=False):
    job, template = selected_template(db, job_id, template_id)
    slots = template.schema_json.get("source_slots", [])
    require_single_source(slots)
    request = {
        "template_id": str(template.id),
        "idempotency_key": uuid4().hex,
        "source_bindings": {
            slots[0]["source_slot_id"]: {
                "job_id": str(job.id),
                "snapshot_id": snapshot_id,
            }
        }
        if slots
        else {},
        "mode": "data" if preview else "report",
        "purpose": "draft",
    }
    service = ReportRunService(db)
    run, _ = service.start(request, actor, preview=preview)
    db.commit()
    service.execute(run.id)
    return service, run


def require_single_source(slots):
    if len(slots) > 1:
        raise ReportingError(
            "EXPLICIT_SOURCE_BINDINGS_REQUIRED",
            status=409,
            source_slots=[slot["source_slot_id"] for slot in slots],
        )


def generate(db, job_id, actor, *, template_id=None, snapshot_id=None):
    service, run = run_for_job(db, job_id, actor, template_id=template_id, snapshot_id=snapshot_id)
    record = db.scalar(select(GeneratedReport).where(GeneratedReport.report_run_id == run.id))
    return {
        "report_id": str(record.id) if record else None,
        "run_id": run.id,
        "status": run.execution_status,
        "snapshot_id": run.input_snapshot_id,
        "manifest_id": run.input_snapshot_id,
        **service.response(run),
    }
