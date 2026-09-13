"""Bridge registered report documents to owned runs using only their original source."""

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import or_, select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.models.entity_shadow import EntityShadow
from app.models.extraction import ExtractionJob
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
)

REPORT_DOCUMENT_ORIGIN = "report-document-origin-v1"
_JOB_KEYS = ("job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob")


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ReportDocumentRuns:
    def __init__(self, application: DocumentAnalysisApplication):
        self.application = application
        self.db = application.db

    def _source(self, document_iri: str, *, word_only: bool = True):
        document = self.db.scalar(select(EntityShadow).where(
            EntityShadow.iri == document_iri, EntityShadow.module == "document",
        ))
        if document is None:
            raise DocumentAnalysisError("SOURCE_NOT_FOUND", "文档登记不存在", status_code=404)
        props = document.properties_json or {}
        raw_job_id = next((props[key] for key in _JOB_KEYS if props.get(key)), None)
        try:
            job_id = UUID(str(raw_job_id))
        except ValueError as exc:
            raise DocumentAnalysisError(
                "SOURCE_NOT_FOUND", "文档未关联可读取的原件", status_code=404,
            ) from exc
        job = self.db.get(ExtractionJob, job_id)
        if job is None:
            raise DocumentAnalysisError("SOURCE_NOT_FOUND", "文档源作业不存在", status_code=404)
        if word_only and job.source_type.strip().lower() not in {"word", "doc", "docx"}:
            raise DocumentAnalysisError(
                "SOURCE_TYPE_MISMATCH", "该文档不是 Word 原件", status_code=422,
            )
        config = job.source_config or {}
        if (config.get("doc_class_iri") and config["doc_class_iri"] != document.class_iri) or (
            config.get("doc_ref") and config["doc_ref"] != document.iri
        ):
            raise DocumentAnalysisError(
                "SOURCE_TYPE_MISMATCH", "文档登记与源作业的类型或归属不一致", status_code=422,
            )
        origin = {
            "kind": REPORT_DOCUMENT_ORIGIN,
            "document_iri": document.iri,
            "source_job_id": str(job.id),
            "document_version": str(props.get("_version", 1)),
            "root_class_iri": document.class_iri,
        }
        return document, job, origin

    @staticmethod
    def _path(job: ExtractionJob) -> Path:
        if not job.document_path or not Path(job.document_path).is_file():
            raise DocumentAnalysisError(
                "SOURCE_NOT_FOUND", "文档原件不可用（已清理或未持久化）", status_code=404,
            )
        return Path(job.document_path)

    def latest(self, owner_id: str, document_iri: str):
        _document, job, expected = self._source(document_iri)
        origin = DocumentAnalysisArtifact.payload["origin"]
        query = select(DocumentAnalysisRun).join(
            DocumentAnalysisArtifact,
            DocumentAnalysisArtifact.artifact_id == DocumentAnalysisRun.source_artifact_ref,
        ).where(
            DocumentAnalysisRun.owner_id == owner_id,
            DocumentAnalysisRun.deletion_state == "none",
            or_(DocumentAnalysisRun.expires_at.is_(None),
                DocumentAnalysisRun.expires_at > datetime.now(UTC)),
            DocumentAnalysisArtifact.artifact_kind == "source",
            *(origin[key].as_string() == value for key, value in expected.items()),
        )
        # If the library copy was removed, an owned run can still replay its frozen source.
        if job.document_path and Path(job.document_path).is_file():
            query = query.where(DocumentAnalysisRun.document_hash == _file_hash(
                Path(job.document_path),
            ))
        return self.db.scalar(query.order_by(
            DocumentAnalysisRun.created_at.desc(), DocumentAnalysisRun.recognition_run_id.desc(),
        ).limit(1))

    async def create(self, owner_id: str, document_iri: str, request_key: str):
        document, job, origin = self._source(document_iri)
        path = self._path(job)
        with path.open("rb") as source:
            upload = UploadFile(file=source, filename=job.source_filename or path.name)
            return await self.application.create_run(
                owner_id=owner_id, file=upload, root_class_iri=document.class_iri,
                request_key=request_key, metadata_mode="generate_summary", origin=origin,
            )

    def preview(self, document_iri: str) -> dict:
        from app.services.extraction.doc_converter import ensure_docx
        from app.services.extraction.document_annotator import annotate_word
        from app.services.extraction.word_analysis import analyze_word_core

        document, job, origin = self._source(document_iri)
        path = self._path(job)
        try:
            docx_path = Path(ensure_docx(str(path)))
            analysis = analyze_word_core(
                docx_path, source_filename=job.source_filename, original_path=path,
            )
            content, warnings, _triples, _checkpoint = annotate_word(
                docx_path, engine=None, structure_only=True, rich_style=True,
                structure=analysis.structure, ir=analysis.ir,
            )
        except Exception as exc:
            raise DocumentAnalysisError(
                "INVALID_WORD", "Word 原件解析失败，请检查文件格式或转换服务", status_code=422,
            ) from exc
        return {
            "document_iri": document.iri,
            "source_job_id": job.id,
            "document_version": origin["document_version"],
            "root_class_iri": document.class_iri,
            "filename": job.source_filename or path.name,
            "document_hash": analysis.ir.document_hash,
            "analysis_id": analysis.ir.analysis_id,
            "structure_hash": analysis.ir.structure_hash,
            "content": content,
            "section_tree": analysis.structure.section_tree.to_dict(),
            "pagination": asdict(analysis.structure.pagination),
            "warnings": list(dict.fromkeys([*analysis.structure.warnings, *warnings])),
        }
