"""Explicit, audited specialization of the one authorized draft template."""

from copy import deepcopy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models.extraction import AstTemplate
from app.services import audit
from app.services.reporting import batch_demo
from app.services.reporting.report_run_service import schema_hash
from app.services.reporting.template_v2 import StaticDemoProfile, TemplateV2

DOCUMENT_IRI = "http://slpra.org/facts#upload-5f09c789-81f2-4e8e-b310-0ffcb6fe23b3"
FIELDS = (
    "name", "version", "schema_json", "schema_version", "schema_hash", "iri_pattern",
    "status", "is_default", "default_source_job_id", "default_source_path",
    "default_source_filename", "sample_text", "sample_content_json", "sample_analysis",
    "sample_docx_path",
)


def _snapshot(row):
    values = {key: getattr(row, key) for key in FIELDS}
    return {key: str(value) if isinstance(value, UUID) else deepcopy(value)
            for key, value in values.items()}


def specialize(db, *, expected_hash, actor, document_iri=DOCUMENT_IRI, apply=False):
    """Preview by default; apply locks the draft and commits configuration + audit once."""
    query = select(AstTemplate).where(AstTemplate.id == batch_demo.TEMPLATE_ID)
    row = db.scalar(query.with_for_update() if apply else query)
    if row is None:
        raise HTTPException(404, "指定模板不存在")
    loaded = batch_demo.read_source(db, document_iri)
    if loaded is None:
        raise HTTPException(409, "指定文档不适用于演示模板")
    graph, contract, source, job = loaded
    if (row.schema_json or {}).get("demo_profile"):
        batch_demo.registered_template(db, contract, source, job)
        return {"changed": False, "template_id": str(row.id),
                "schema_hash": schema_hash(row.schema_json)}
    if schema_hash(row.schema_json) != expected_hash:
        raise HTTPException(409, "模板已变化，请重新检查后再特化")
    if row.status != "draft" or row.is_default:
        raise HTTPException(409, "只允许特化非默认草稿模板")
    if row.iri_pattern != contract["root_class_iri"]:
        raise HTTPException(409, "模板文档类型不匹配")
    schema = TemplateV2.model_validate(row.schema_json)
    if schema.template_revision_id != str(row.id):
        raise HTTPException(409, "模板修订身份不匹配")
    schema = schema.model_copy(update={
        "demo_profile": StaticDemoProfile(
            fixture_id=graph["fixture_id"], contract_id=contract["contract_id"],
            document_iri=document_iri,
        ),
        "calculation_checks": [], "sections": [],
    }).model_dump(mode="json")
    before = _snapshot(row)
    after = {**before, "name": contract["name"], "version": "v" + contract["version"],
             "schema_json": schema, "schema_hash": schema_hash(schema), "schema_version": 2,
             "default_source_job_id": str(job.id), "default_source_path": job.document_path,
             "default_source_filename": job.source_filename, "sample_text": None,
             "sample_content_json": None, "sample_analysis": None, "sample_docx_path": None}
    result = {"changed": True, "applied": apply, "template_id": str(row.id),
              "before": before, "after": after, "source": source,
              "contract_hash": batch_demo.digest(contract)}
    if apply:
        try:
            for key, value in after.items():
                setattr(row, key, UUID(value) if key == "default_source_job_id" else value)
            audit.append(db, "template.specialize_demo", actor=actor, entity_iri=str(row.id),
                         details=result, commit=False)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return result
