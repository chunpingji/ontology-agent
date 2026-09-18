"""One owner/template/source execution head and one latest display artifact."""

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.entity_shadow import EntityShadow
from app.models.extraction import AnnotationExecution, AstTemplate, ExtractionJob
from app.services.document_analysis.application import DocumentAnalysisError
from app.services.document_analysis.report_documents import _JOB_KEYS, ReportDocumentRuns
from app.services.extraction.annotation_execution import (
    ACTIVE,
    ExecutionBusy,
    ExecutionLost,
    WorkerLease,
    begin_worker,
    claim,
    fence,
    now,
)

from .pde_review import REVIEW_VERSION, TARGET_FILENAME, attach_comparisons
from .policy import MODE, PRIVATE_MODE, digest, error, resolve, response_fields
from .runner import freeze_ontology, render_source, run

logger = logging.getLogger(__name__)
NAMESPACE = UUID("720a8ba8-7bf8-41e1-ac10-01e5c2aaeb58")
HRS1597_CONTEXT_SOURCE = "HRS-1597原料临床备样生产信息表_--脱敏-原 - PDE-冲突.docx"
HRS1597_CONTEXT_TEMPLATE = "风险评估文档V1版"


def private_job_id(owner, template_id, source_job_id):
    return uuid5(NAMESPACE, json.dumps([owner, str(template_id), str(source_job_id), MODE]))


def is_private(job):
    return job and (job.source_config or {}).get("mode") == PRIVATE_MODE


def _registered_job(document):
    props = document.properties_json or {}
    return next((str(props[key]) for key in _JOB_KEYS if props.get(key)), None)


def source(db, template_id, source_job_id, *, validate_type=True):
    template = db.get(AstTemplate, template_id)
    job = db.get(ExtractionJob, source_job_id)
    if template is None or job is None or is_private(job):
        raise error("SOURCE_NOT_FOUND", "模板或源登记不存在", 404)
    config = job.source_config or {}
    if template.default_source_job_id != job.id:
        documents = db.scalars(select(EntityShadow).where(EntityShadow.module == "document"))
        registered = [
            doc
            for doc in documents
            if _registered_job(doc) == str(job.id)
            and (not config.get("doc_ref") or config["doc_ref"] == doc.iri)
            and doc.class_iri == config.get("doc_class_iri")
        ]
        if not registered:
            raise error("SOURCE_NOT_FOUND", "源不是模板默认源或可读取的登记文档", 404)
    if validate_type and (
        job.source_type not in {"word", "doc", "docx"}
        or config.get("doc_class_iri") != template.iri_pattern
    ):
        raise error("SOURCE_TYPE_MISMATCH", "本体指引1.0仅支持与模板根类一致的 Word 原件", 422)
    return template, job


def context(db, document_iri, template_id=None):
    try:
        document, job, _ = ReportDocumentRuns(SimpleNamespace(db=db))._source(document_iri)
    except DocumentAnalysisError as exc:
        raise error(exc.code, exc.message, exc.status_code) from exc
    if is_private(job):
        raise error("SOURCE_NOT_FOUND", "内部本体指引1.0作业不能作为公共源", 404)
    options, registered = [], []
    explicit = str(template_id) if template_id else None
    registration = (job.source_config or {}).get("template_id")
    templates = select(AstTemplate).where(AstTemplate.iri_pattern == document.class_iri)
    selection_locked = job.source_filename == HRS1597_CONTEXT_SOURCE
    if selection_locked:
        # This document follows the latest named template, including from old deep links.
        latest = db.scalar(
            templates.where(AstTemplate.name == HRS1597_CONTEXT_TEMPLATE)
            .order_by(AstTemplate.created_at.desc(), AstTemplate.id.desc())
            .limit(1)
        )
        if latest is None:
            raise error("TEMPLATE_NOT_FOUND", "未找到风险评估文档V1版的可用模板", 404)
        explicit = str(latest.id)
        templates = templates.where(AstTemplate.id == latest.id)
    for template in db.scalars(templates):
        if (template.schema_json or {}).get("demo_profile"):
            resolve(template)  # Reject conflicting static/Finder configuration here too.
            if explicit == str(template.id):
                options.append(
                    {
                        "template_id": str(template.id),
                        "name": template.name,
                        "version": template.version,
                        "schema_version": template.schema_json.get("schema_version", 1),
                        "recognition_mode": "static_demo",
                        "finder_profile_id": None,
                    }
                )
            continue
        item = {
            "template_id": str(template.id),
            "name": template.name,
            "version": template.version,
            "schema_version": template.schema_json.get("schema_version", 1),
            **response_fields(template),
        }
        options.append(item)
        if template.default_source_job_id == job.id or str(template.id) == registration:
            registered.append(item)
    selected = next((item for item in options if item["template_id"] == explicit), None)
    if explicit and selected is None:
        raise error("SOURCE_TYPE_MISMATCH", "指定模板与登记源不匹配", 422)
    if not explicit and len(registered) == 1:
        selected = registered[0]
    return {
        "document_iri": document_iri,
        "source_job_id": str(job.id),
        "selected": selected,
        "templates": options,
        "selection_required": not explicit and len(registered) > 1,
        "selection_locked": selection_locked,
    }


def _path(job):
    path = Path(job.document_path) if job.document_path else None
    if path is None or not path.is_file():
        raise error("SOURCE_NOT_FOUND", "源文档原件不可用", 404)
    return path


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _input(template, job, binding, snapshot):
    from app.services.extraction.document_ir import IR_VERSION, STRUCTURE_POLICY_VERSION
    from app.services.extraction.docx_structure import PARSER_VERSION

    return {
        "parser_version": str(PARSER_VERSION),
        "ir_version": IR_VERSION,
        "structure_policy_version": STRUCTURE_POLICY_VERSION,
        "template_hash": digest(template.schema_json),
        "root_class_iri": binding.root_class_iri,
        "source_hash": _file_hash(_path(job)),
        "source_filename": job.source_filename,
        "profile_id": binding.profile_id,
        "profile_version": binding.profile["version"],
        "profile_hash": digest(binding.profile),
        "ontology_hash": digest(snapshot),
        **({"pde_review_version": REVIEW_VERSION}
           if job.source_filename == TARGET_FILENAME else {}),
    }


def _status(head):
    if head.status in ACTIVE:
        expiry = head.lease_expires_at
        if (expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry) <= now():
            return "interrupted"
    return head.status


class FinderService:
    def __init__(self, db, engine=None):
        self.db, self.engine = db, engine

    def _head(self, owner, template_id, source_job_id):
        return self.db.get(
            AnnotationExecution,
            private_job_id(owner, template_id, source_job_id),
            populate_existing=True,
        )

    def status(self, owner, template_id, source_job_id, *, check_stale=True):
        template, job = source(self.db, template_id, source_job_id, validate_type=False)
        head = self._head(owner, template_id, source_job_id)
        result = {
            "mode": MODE,
            "template_id": str(template_id),
            "source_job_id": str(source_job_id),
            "execution_id": None,
            "status": "not_started",
            "stage": None,
            "input": None,
            "has_result": False,
            "stale": False,
            "error": None,
            "counts": {},
        }
        if head is None:
            if resolve(template) is None:
                raise error("RECOGNITION_MODE_MISMATCH", "该模板未启用本体指引1.0")
            return result
        result.update(
            execution_id=head.options["execution_id"],
            status=_status(head),
            stage=head.progress.get("stage"),
            input=head.options["input"],
            error=head.progress.get("error"),
            counts=head.progress.get("counts", {}),
        )
        if head.status == "completed":
            try:
                self._cache(head, result["execution_id"])
                result["has_result"] = True
            except (OSError, ValueError, KeyError, TypeError):
                result["error"] = "当前展示缓存不可用，请重新识别"
        if check_stale:
            try:
                binding = resolve(template)
                if binding is None:
                    result["stale"] = True
                else:
                    snapshot = freeze_ontology(self.engine, binding)
                    result["stale"] = (
                        _input(template, job, binding, snapshot) != head.options["input"]
                    )
            except Exception:
                # A saved result may still be viewed after removal of the library original/config.
                result["stale"] = True
        return result

    def start(self, owner, template_id, source_job_id, request, background):
        template, job = source(self.db, template_id, source_job_id)
        source_path, filename = str(_path(job)), job.source_filename
        binding = resolve(template)
        if binding is None:
            raise error("RECOGNITION_MODE_MISMATCH", "该模板未启用本体指引1.0")
        try:
            snapshot = freeze_ontology(self.engine, binding)
        except (ValueError, KeyError) as exc:
            raise error("FINDER_CONFIG_INVALID", str(exc)) from exc
        identity = _input(template, job, binding, snapshot)
        expected = str(request.expected_execution_id) if request.expected_execution_id else None
        request_hash = digest({"input": identity, "expected": expected})
        private_id = private_job_id(owner, template_id, source_job_id)
        db = self.db
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError("Finder ownership requires PostgreSQL or SQLite")
        try:
            db.execute(
                insert(ExtractionJob)
                .values(
                    id=private_id,
                    source_type="word",
                    status="pending",
                    source_config={
                        "mode": PRIVATE_MODE,
                        "owner": owner,
                        "template_id": str(template_id),
                        "source_job_id": str(source_job_id),
                    },
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            private = db.scalar(
                select(ExtractionJob)
                .where(ExtractionJob.id == private_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            head = self._head(owner, template_id, source_job_id)
            if head and head.options.get("request_key") == request.request_key:
                if head.options.get("request_hash") != request_hash:
                    raise error("INPUT_VERSION_CONFLICT", "相同请求键对应不同输入")
                db.commit()
                return self.status(owner, template_id, source_job_id, check_stale=False)
            current_id = head.options.get("execution_id") if head else None
            if current_id != expected:
                raise error("INPUT_VERSION_CONFLICT", "执行已更新，请刷新后重新识别")
            options = {
                "recognition_mode": MODE,
                "execution_id": str(uuid4()),
                "input": identity,
                "request_key": request.request_key,
                "request_hash": request_hash,
            }
            token = claim(db, private_id, actor=owner, options=options)
            head = db.get(AnnotationExecution, private_id, populate_existing=True)
            head.progress = {"stage": "queued"}
            private.status = "running"
            db.commit()
        except ExecutionBusy as exc:
            db.rollback()
            raise error("EXECUTION_BUSY", "本体指引1.0正在识别，请等待完成") from exc
        except BaseException:
            db.rollback()
            raise
        background.add_task(
            worker,
            db.get_bind(),
            private_id,
            token,
            source_path,
            filename,
            binding,
            snapshot,
        )
        return self.status(owner, template_id, source_job_id, check_stale=False)

    @staticmethod
    def _cache(head, execution_id):
        if head.status != "completed" or head.options.get("execution_id") != execution_id:
            raise ValueError("execution is not the current completed result")
        path = settings.template_finder_storage_dir / str(head.job_id) / "latest.json"
        payload = json.loads(path.read_text())
        if payload["execution_id"] != execution_id or payload["input"] != head.options["input"]:
            raise ValueError("cache identity mismatch")
        return payload

    def result(self, owner, template_id, source_job_id, execution_id, part):
        source(self.db, template_id, source_job_id, validate_type=False)
        head = self._head(owner, template_id, source_job_id)
        try:
            if head is None:
                raise ValueError("not started")
            payload = self._cache(head, execution_id)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise error("RESULT_UNAVAILABLE", "当前执行结果不可用，请读取最新状态") from exc
        return {
            "mode": MODE,
            "template_id": str(template_id),
            "source_job_id": str(source_job_id),
            "execution_id": execution_id,
            "input": payload["input"],
            **payload[part],
        }

    def preview(self, template_id, source_job_id):
        from app.services.extraction.word_analysis import analyze_word_core

        template, job = source(self.db, template_id, source_job_id)
        if resolve(template) is None:
            raise error("RECOGNITION_MODE_MISMATCH", "该模板未启用本体指引1.0")
        # A temporary, request-owned parse copy is removed before this read returns.
        with tempfile.TemporaryDirectory(prefix="finder-preview-") as folder:
            path = Path(folder) / "source.docx"
            path.write_bytes(_path(job).read_bytes())
            try:
                analysis = analyze_word_core(path, source_filename=job.source_filename)
                rendered = render_source(analysis, path, job.source_filename)
            except Exception as exc:
                raise error("INVALID_WORD", "Word 原件解析失败", 422) from exc
        return {
            "mode": MODE,
            "template_id": str(template_id),
            "source_job_id": str(source_job_id),
            "execution_id": None,
            **rendered,
        }


def worker(bind, private_id, token, source_path, filename, binding, snapshot):
    from app.services.extraction.word_analysis import analyze_word_core

    with Session(bind) as db:
        if not begin_worker(db, private_id, token):
            return
        try:
            with WorkerLease(bind, private_id, token) as lease:
                with fence(db, private_id, token) as head:
                    options = dict(head.options)
                    head.progress = {"stage": "parsing"}
                raw = Path(source_path).read_bytes()
                if hashlib.sha256(raw).hexdigest() != options["input"]["source_hash"]:
                    raise ValueError("原件已变更，请重新识别")
                with tempfile.TemporaryDirectory(prefix="template-finder-") as folder:
                    path = Path(folder) / "source.docx"
                    path.write_bytes(raw)
                    analysis = analyze_word_core(path, source_filename=filename)
                    lease.check()
                    with fence(db, private_id, token) as head:
                        head.progress = {"stage": "finding"}
                    graph = run(analysis, binding, snapshot)
                    attach_comparisons(graph, filename)
                    with fence(db, private_id, token) as head:
                        head.progress = {"stage": "rendering", "counts": graph["counts"]}
                    rendered = render_source(analysis, path, filename)
                graph.update(
                    {
                        key: rendered[key]
                        for key in (
                            "document_hash",
                            "parser_version",
                            "structure_hash",
                            "analysis_id",
                        )
                    }
                )
                payload = {
                    "execution_id": options["execution_id"],
                    "input": options["input"],
                    "graph": graph,
                    "source": rendered,
                }
                directory = settings.template_finder_storage_dir / str(private_id)
                with fence(db, private_id, token) as head:
                    directory.mkdir(parents=True, exist_ok=True)
                    temp = directory / f".{token}.tmp"
                    try:
                        temp.write_text(json.dumps(payload, ensure_ascii=False))
                        os.replace(temp, directory / "latest.json")
                    finally:
                        temp.unlink(missing_ok=True)
                    head.status = "completed"
                    head.progress = {"stage": "completed", "counts": graph["counts"]}
                    db.get(ExtractionJob, private_id).status = "completed"
        except ExecutionLost:
            return
        except Exception:
            logger.exception("Finder display execution failed job=%s", private_id)
            try:
                with fence(db, private_id, token) as head:
                    head.status = "failed"
                    head.progress = {
                        "stage": "failed",
                        "error": "本体指引1.0执行失败，请检查原件和配置后重试",
                    }
                    db.get(ExtractionJob, private_id).status = "failed"
            except ExecutionLost:
                pass
