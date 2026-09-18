"""Append expert feedback without changing facts or calibration acceptance."""

import hashlib
from datetime import UTC
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentRunArtifact
from app.models.extraction import GeneratedReport
from app.models.report_expert_opinion import ReportExpertOpinion
from app.schemas.report_expert_opinion import ExpertOpinion, OpinionContext
from app.services import audit
from app.services.document_analysis.application import DocumentAnalysisApplication
from app.services.document_analysis.report_documents import ReportDocumentRuns
from app.services.extraction.evidence_identity import evidence_hash

EXPERT_ROLES = {"senior_analyst", "qa"}


class ExpertOpinionService:
    def __init__(self, db, identity):
        self.db, self.identity = db, identity
        self.application = DocumentAnalysisApplication(db, ontology_engine=None)

    def context(self, target, run_id=None, *, graph_id=None, latest_graph=True):
        if target.document_iri:
            sources = ReportDocumentRuns(self.application)
            _document, job, origin = sources._source(target.document_iri, word_only=False)
            path = sources._path(job)
            filename, version = job.source_filename or path.name, origin["document_version"]
        else:
            report = self.db.get(GeneratedReport, target.report_id)
            if (not report or report.job_id != target.job_id or report.deleted_at is not None
                    or (report.report_type == "batch_record_demo"
                        and report.actor != self.identity.username)):
                raise HTTPException(404, "报告不存在")
            if report.report_status not in {None, "completed"}:
                raise HTTPException(409, "报告尚未生成完成")
            if report.report_run_id:
                from app.services.reporting.report_run_service import ReportRunService
                artifact = ReportRunService(self.db).download(
                    report.report_run_id, report.report_artifact_id,
                )
                path = Path(artifact.file_path)
            else:
                path = Path(report.file_path)
            filename, version = path.name, str(report.id)
        if not path.is_file():
            raise HTTPException(404, "原文件不可用，暂不能绑定专家意见")
        with path.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        result = OpinionContext(target=target, filename=filename, source_hash=source_hash,
                                source_version=version, recognition_run_id=run_id)
        if run_id:
            if not target.document_iri:
                raise HTTPException(422, "生成报告不能关联文档识别运行")
            run = self.application.get_run(run_id, self.identity.username)
            source = self.db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
            origin = (source.payload or {}).get("origin", {}) if source else {}
            if (origin.get("document_iri") != target.document_iri
                    or run.document_hash != source_hash):
                raise HTTPException(409, "识别运行与当前原文版本不一致，请刷新预览")
            if latest_graph:
                graph_id = (run.artifact_manifest.get("graph") or {}).get("artifact_id")
            if graph_id:
                link = self.db.scalar(select(DocumentRunArtifact).where(
                    DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
                    DocumentRunArtifact.artifact_kind == "graph",
                    DocumentRunArtifact.artifact_id == graph_id,
                ).limit(1))
                if not link:
                    raise HTTPException(404, "图谱版本不存在或不属于当前运行")
                result.graph_artifact_id = link.artifact_id
                result.graph_content_hash = link.content_hash
        elif graph_id:
            raise HTTPException(422, "图谱版本必须关联识别运行")
        return result

    def _query(self, target):
        return select(ReportExpertOpinion).where(
            ReportExpertOpinion.owner_id == self.identity.username,
            ReportExpertOpinion.target_hash == evidence_hash(target),
        )

    @staticmethod
    def public(row):
        return ExpertOpinion(
            opinion_id=row.opinion_id, revision=row.revision, author=row.owner_id,
            author_role=row.actor_role,
            created_at=row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None
            else row.created_at, **row.payload,
        )

    def listing(self, target, run_id=None, *, offset=0, limit=50):
        context = self.context(target, run_id)
        query = self._query(target)
        total = self.db.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.db.scalars(query.order_by(ReportExpertOpinion.created_at.desc(),
                                             ReportExpertOpinion.opinion_id.desc())
                               .offset(offset).limit(limit))
        return dict(context=context, can_submit=self.identity.role in EXPERT_ROLES,
                    items=[self.public(row) for row in rows], total=total,
                    offset=offset, limit=limit)

    def export(self, target):
        self.context(target)
        rows = self.db.scalars(self._query(target).order_by(
            ReportExpertOpinion.created_at, ReportExpertOpinion.opinion_id,
        ))
        return {"schema_version": "report-expert-opinions-v1", "target": target,
                "calibration_approved": False, "opinions": [self.public(row) for row in rows]}

    def create(self, request):
        if self.identity.role not in EXPERT_ROLES:
            raise HTTPException(403, "仅高级分析师和 QA 可提交专家意见")
        request_hash = evidence_hash(request)

        def replay():
            existing = self.db.scalar(select(ReportExpertOpinion).where(
                ReportExpertOpinion.owner_id == self.identity.username,
                ReportExpertOpinion.request_key == request.request_key,
            ))
            if existing and existing.request_hash != request_hash:
                raise HTTPException(409, "该提交标识已用于其他内容，请重新提交")
            return existing

        context = self.context(request.context.target, request.context.recognition_run_id,
                               graph_id=request.context.graph_artifact_id, latest_graph=False)
        previous = replay()
        if previous:
            return self.public(previous)
        if context != request.context:
            raise HTTPException(409, "原文或审核上下文已变化，请刷新后重试；意见尚未保存")
        row = ReportExpertOpinion(
            owner_id=self.identity.username, actor_role=self.identity.role,
            target_hash=evidence_hash(context.target), request_key=request.request_key,
            request_hash=request_hash,
            payload=request.model_dump(mode="json", exclude={"request_key"}),
        )
        try:
            self.db.add(row)
            self.db.flush()
            audit.append(self.db, "report.expert_opinion.created", actor=self.identity.username,
                         entity_iri=context.target.document_iri or str(context.target.report_id),
                         details={"opinion_id": str(row.opinion_id), "revision": 1,
                                  "role": self.identity.role, "source_hash": context.source_hash,
                                  "request_hash": request_hash}, commit=False)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            previous = replay()
            if previous:
                return self.public(previous)
            raise
        except Exception:
            self.db.rollback()
            raise
        return self.public(row)
