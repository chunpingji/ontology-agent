"""Read a registered template source into the independent document-run domain."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import or_, select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.models.extraction import AstTemplate, ExtractionJob
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
)
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.template_priorities import template_priority_paths

TEMPLATE_ORIGIN_VERSION = "template-document-origin-v1"


class TemplateDocumentRuns:
    def __init__(self, application: DocumentAnalysisApplication):
        self.application = application
        self.db = application.db

    def _source(self, template_id: UUID, job_id: UUID):
        template = self.db.get(AstTemplate, template_id)
        job = self.db.get(ExtractionJob, job_id)
        if template is None or job is None:
            raise DocumentAnalysisError("SOURCE_NOT_FOUND", "模板或源文档不存在", status_code=404)
        if (template.schema_json or {}).get("demo_profile"):
            raise DocumentAnalysisError(
                "RUN_STATE_CONFLICT", "演示模板使用共享静态图谱，无需抽取", status_code=409)
        config = job.source_config or {}
        root = config.get("doc_class_iri")
        if not root or (template.iri_pattern and template.iri_pattern not in root):
            raise DocumentAnalysisError(
                "SOURCE_TYPE_MISMATCH", "源文档登记类型与模板不匹配", status_code=422)
        return template, job, root

    def latest(self, owner_id: str, template_id: UUID, job_id: UUID):
        self._source(template_id, job_id)
        origin = DocumentAnalysisArtifact.payload["origin"]
        return self.db.scalar(
            select(DocumentAnalysisRun)
            .join(DocumentAnalysisArtifact,
                  DocumentAnalysisArtifact.artifact_id == DocumentAnalysisRun.source_artifact_ref)
            .where(
                DocumentAnalysisRun.owner_id == owner_id,
                DocumentAnalysisRun.deletion_state == "none",
                or_(DocumentAnalysisRun.expires_at.is_(None),
                    DocumentAnalysisRun.expires_at > datetime.now(UTC)),
                DocumentAnalysisArtifact.artifact_kind == "source",
                origin["kind"].as_string() == TEMPLATE_ORIGIN_VERSION,
                origin["template_id"].as_string() == str(template_id),
                origin["source_job_id"].as_string() == str(job_id),
            )
            .order_by(DocumentAnalysisRun.created_at.desc(),
                      DocumentAnalysisRun.recognition_run_id.desc()).limit(1)
        )

    async def create(self, owner_id: str, template_id: UUID, job_id: UUID, request_key: str):
        template, job, root = self._source(template_id, job_id)
        if not job.document_path or not Path(job.document_path).is_file():
            raise DocumentAnalysisError("SOURCE_NOT_FOUND", "源文档原件不可用", status_code=404)
        template_hash = evidence_hash(template.schema_json)
        paths = template_priority_paths(template.schema_json, root)
        config = job.source_config or {}
        # Preserve explicitly registered operator priorities only for this exact
        # template revision. Values/expected answers never enter the run input.
        if (config.get("recognition_template_id") == str(template_id)
                and config.get("extraction_priority_template_hash") == template_hash
                and config.get("extraction_priority_source") == "operator-request-and-template"):
            paths = [tuple(path) for path in config.get("extraction_priority_paths", [])] + paths
        origin = {
            "kind": TEMPLATE_ORIGIN_VERSION,
            "template_id": str(template_id), "source_job_id": str(job_id),
            "template_hash": template_hash,
            "priority_paths": [list(path) for path in dict.fromkeys(paths)],
        }
        with Path(job.document_path).open("rb") as source:
            upload = UploadFile(file=source, filename=job.source_filename or Path(source.name).name)
            return await self.application.create_run(
                owner_id=owner_id, file=upload, root_class_iri=root, request_key=request_key,
                metadata_mode="cached_summary", origin=origin,
            )
