"""Document extraction API routes (能力二：多源抽取 + 跨源对齐 + 人工审核闭环)."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from threading import RLock
from time import time
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
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    Identity,
    get_current_user,
    get_current_user_sse,
    get_ontology_engine,
    require_role,
)
from app.models.entity_shadow import EntityShadow
from app.models.extraction import (
    ExtractionCandidate,
    ExtractionConfig,
    ExtractionJob,
    GeneratedReport,
)
from app.models.ontology_meta import (
    SOURCE_ENTITY_MAPPING_TYPES,
    OntologyClassMapping,
)
from app.schemas.extraction import (
    ASTCoverageResponse,
    CandidateGroup,
    DocExtractionRequest,
    ExtractionCandidateResponse,
    ExtractionConfigCreate,
    ExtractionConfigResponse,
    ExtractionJobResponse,
    GeneratedReportResponse,
    GroupedCandidatesResponse,
    MergeRequest,
    ReviewRequest,
    SlotDismissRequest,
    SplitRequest,
)
from app.services import audit
from app.services.extraction.performance import measure, timed, tracking
from app.services.extraction.pipeline import run_extraction_pipeline
from app.services.extraction.progress import progress_bus
from app.services.ontology_engine import OntologyEngine

router = APIRouter()
logger = logging.getLogger(__name__)
_annotation_cache_lock = RLock()

_analyst = require_role(ROLE_SENIOR_ANALYST)


_WORD_TEMPLATE_MODE = "template_default"
_WORD_REPOSITORY_PREVIEW_MODE = "doc_repo_preview"
_RETIRED_WORD_RECOGNITION = (
    "WORD_RECOGNITION_RETIRED: use POST /api/document-analysis/runs"
)


def _is_word_source(source_type: object) -> bool:
    return isinstance(source_type, str) and source_type.strip().casefold() == "word"


def _word_job_mode(job: ExtractionJob) -> str | None:
    mode = (job.source_config or {}).get("mode")
    return mode if isinstance(mode, str) else None


def _word_job_allows_annotation(job: ExtractionJob) -> bool:
    """Only the template-default workflow may use the shared legacy annotator.

    Ordinary Word graph recognition moved to ``/document-analysis/runs``. Excel
    annotation remains shared, while document-repository uploads may retain a
    source-only preview without acquiring an annotation execution lease.
    """

    return not _is_word_source(job.source_type) or _word_job_mode(job) == _WORD_TEMPLATE_MODE


def _word_job_allows_preview(job: ExtractionJob) -> bool:
    return not _is_word_source(job.source_type) or _word_job_mode(job) in {
        _WORD_TEMPLATE_MODE,
        _WORD_REPOSITORY_PREVIEW_MODE,
    }


def _reject_retired_word_recognition() -> None:
    raise HTTPException(410, _RETIRED_WORD_RECOGNITION)


def _require_annotation_capability(job: ExtractionJob) -> None:
    if not _word_job_allows_annotation(job):
        _reject_retired_word_recognition()


def _require_preview_capability(job: ExtractionJob) -> None:
    if not _word_job_allows_preview(job):
        _reject_retired_word_recognition()


def _require_result_capability(job: ExtractionJob) -> None:
    """Hide mutable legacy recognition products outside template workflows."""

    if _is_word_source(job.source_type) and _word_job_mode(job) != _WORD_TEMPLATE_MODE:
        _reject_retired_word_recognition()


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
    """后台运行流水线。复用请求会话（FastAPI 在后台任务后才做依赖清理）。

    声明驱动作业（``source_config.class_mapping_id``）无需 ``ExtractionConfig``——
    E6 类绑定即配置源；此时 config 允许为空（FR-007）。
    """
    job = db.get(ExtractionJob, job_id)
    if job is None:
        return
    if _is_word_source(job.source_type):
        if not _word_job_allows_annotation(job):
            logger.warning("Refused retired Word pipeline delivery job=%s", job_id)
            return
        await _precompute_annotation_bg(job_id, engine, db)
        return
    config = db.get(ExtractionConfig, config_id) if config_id else None
    if config is None and not (job.source_config or {}).get("class_mapping_id"):
        return
    await run_extraction_pipeline(job, config, file_path, engine, db)


async def _create_declarative_job(
    background: BackgroundTasks,
    source_type: str,
    class_mapping_id: UUID,
    config_id: UUID | None,
    binding: OntologyClassMapping,
    db: Session,
    engine: OntologyEngine,
    identity: Identity,
    file: UploadFile | None = None,
) -> ExtractionJob:
    """Create + trigger a declaration-driven job (014 US1/US2, FR-007).

    The E6 class binding is the config source, so no ``ExtractionConfig`` is
    required; ``source_config.class_mapping_id`` routes the pipeline to the
    declarative branch. No credential is ever stored (only the binding's
    env-var/connector reference).
    """
    if _is_word_source(source_type):
        _reject_retired_word_recognition()

    source_cfg: dict = {"class_mapping_id": str(class_mapping_id)}
    if config_id is not None:
        source_cfg["config_id"] = str(config_id)

    job = ExtractionJob(
        source_type=source_type,
        source_filename=file.filename if file else binding.target or source_type,
        source_config=source_cfg,
        status="running",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    file_path: Path | None = None
    if file is not None:
        suffix = Path(file.filename or "").suffix.lower()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(await file.read())
        tmp.close()
        uploads_dir = Path("data/uploads")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        file_path = uploads_dir / f"{job.id}{suffix}"
        shutil.move(tmp.name, file_path)
        job.document_path = str(file_path)
        db.commit()
        db.refresh(job)

    audit.append(
        db,
        "extraction.job.create",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": source_type, "class_mapping_id": str(class_mapping_id)},
    )

    background.add_task(_run_pipeline_bg, job.id, config_id, file_path, engine, db)
    return job


@router.post("/jobs", response_model=ExtractionJobResponse, status_code=202)
async def create_job(
    background: BackgroundTasks,
    source_type: str = Form(...),
    config_id: UUID | None = Form(None),
    class_mapping_id: UUID | None = Form(None),
    file: UploadFile | None = File(None),
    db_source: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
    identity_property_iri: str | None = Form(None),
    label_property_iri: str | None = Form(None),
):
    """创建抽取作业并**真实触发**流水线（状态置 running，非 pending, FR-001/002）。

    两种模式：
    - **声明驱动**（014 US1/US2, FR-007）：传 ``class_mapping_id``——E6 源实体绑定
      驱动抽取，无需 ``ExtractionConfig``。未知绑定→404；非源实体绑定→422。
    - **遗留 config 驱动**（Excel/Word/doc_repo/database-reflect）：传 ``config_id``，行为不变。
    """
    if class_mapping_id is not None:
        binding = db.get(OntologyClassMapping, class_mapping_id)
        if binding is None:
            raise HTTPException(404, "class binding not found")
        if binding.mapping_type == "doc_pattern":
            raise HTTPException(422, "EXECUTABLE_PATTERN_RETIRED: use semantic evidence tasks")
        if binding.mapping_type not in SOURCE_ENTITY_MAPPING_TYPES:
            raise HTTPException(
                422,
                "该映射不是结构化源实体绑定（db_table/api_endpoint），无法驱动抽取",
            )
        if _is_word_source(source_type):
            _reject_retired_word_recognition()
        return await _create_declarative_job(
            background,
            source_type,
            class_mapping_id,
            config_id,
            binding,
            db,
            engine,
            identity,
        )

    if _is_word_source(source_type):
        _reject_retired_word_recognition()

    config = db.get(ExtractionConfig, config_id) if config_id else None
    if not config:
        raise HTTPException(404, "extraction config not found")
    # The request field is not a security boundary: a caller must not disguise a
    # retired Word config as Excel/database and reach ``pipeline.parse_word``.
    if _is_word_source(config.source_type):
        _reject_retired_word_recognition()

    file_path: Path | None = None
    if file is not None:
        suffix = Path(file.filename or "").suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(await file.read())
        tmp.close()
        file_path = Path(tmp.name)

    source_cfg: dict = {"config_id": str(config_id)}
    for key, value in (
        ("identity_property_iri", identity_property_iri),
        ("label_property_iri", label_property_iri),
    ):
        if isinstance(value, str):
            if value not in (config.column_mapping or {}).values():
                raise HTTPException(
                    422, "IDENTITY_MAPPING_REQUIRED: property must be explicitly mapped"
                )
            source_cfg[key] = value
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

    if file_path is not None:
        uploads_dir = Path("data/uploads")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        persistent_path = uploads_dir / f"{job.id}{suffix}"
        shutil.move(str(file_path), str(persistent_path))
        file_path = persistent_path
        job.document_path = str(persistent_path)
        db.commit()

    audit.append(
        db,
        "extraction.job.create",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": source_type, "config_id": str(config_id)},
    )

    background.add_task(_run_pipeline_bg, job.id, config_id, file_path, engine, db)
    if job.document_path and source_type == "excel":
        # The structured pipeline runs first. Claim the optional annotation
        # only when it starts, so a long import cannot exhaust a queued lease.
        background.add_task(_precompute_annotation_bg, job.id, engine, db.get_bind(),
                            mode="auxiliary", actor=identity.username)
    return job


@router.post("/jobs/from-document", response_model=ExtractionJobResponse, status_code=202)
def enqueue_document_extraction(
    req: DocExtractionRequest,
    db: Session = Depends(get_db),
    identity: Identity = Depends(_analyst),
):
    """文档 approved/新版本事件 → 入待抽取队列（007 US2，FR-007/Q1）。

    创建 `pending` 的 doc_repo 作业，**不自动发起**抽取管线（记录是事实自动物化、内容是候选
    人工发起）；由授权角色经 `POST /jobs/{job_id}/start` 手动发起 `run_extraction_pipeline`。
    """
    config = db.get(ExtractionConfig, req.config_id)
    if not config:
        raise HTTPException(404, "extraction config not found")

    job = ExtractionJob(
        source_type="doc_repo",
        source_filename=req.doc_ref,
        source_config={
            "doc_ref": req.doc_ref,
            "content_ref": req.content_ref,
            "config_id": str(req.config_id),
        },
        status="pending",  # Q1：入队不自动发起
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    audit.append(
        db,
        "extraction.job.enqueue",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": "doc_repo", "doc_ref": req.doc_ref},
    )
    return job


@router.post("/jobs/{job_id}/start", response_model=ExtractionJobResponse, status_code=202)
async def start_extraction_job(
    job_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """手动发起待抽取作业（授权角色，Q1）：置 running 并后台运行流水线。"""
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)
    if _is_word_source(job.source_type):
        _reject_retired_word_recognition()
    if job.status != "pending":
        raise HTTPException(409, "job already started")

    cfg_id = (job.source_config or {}).get("config_id")
    config = db.get(ExtractionConfig, UUID(cfg_id)) if cfg_id else None
    if not config:
        raise HTTPException(404, "extraction config not found")

    job.status = "running"
    db.commit()
    db.refresh(job)

    audit.append(
        db,
        "extraction.job.start",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": job.source_type},
    )

    background.add_task(_run_pipeline_bg, job.id, config.id, None, engine, db)
    return job


@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)
    return job


_ANNOTATOR_VERSION = 32


def _annotation_cache_path(job_id) -> Path:
    """标注预计算缓存路径：``data/uploads/{job_id}.annotated.json``。"""
    return Path("data/uploads") / f"{job_id}.annotated.json"


def _annotation_cache_is_current(payload: dict) -> bool:
    """Validate every version that changes the annotated Word response."""
    if payload.get("_version") != _ANNOTATOR_VERSION:
        return False
    if payload.get("source_type") != "word":
        return True

    from app.config import settings
    from app.services.extraction.docx_structure import PARSER_VERSION

    return (
        payload.get("_parser_version") == PARSER_VERSION
        and payload.get("_summary_prompt_version") == settings.word_tree_summary_prompt_version
    )


def _capture_model(db, schema):
    from app.services.ontology_model_context import capture_schema

    capture_schema(db, schema)
    db.commit()


def _compute_annotation(
    job: ExtractionJob,
    engine: OntologyEngine,
    progress_fn=None,
    should_pause_fn=None,
    checkpoint=None,
    retry_failed=False,
    pause_after=None,
    preview_only=False,
    checkpoint_fn=None,
    snapshot_fn=None,
    defer_summary_fn=None,
    schema_snapshot_fn=None,
    assert_owner_fn=None,
) -> dict:
    """Shared IR preview and generic evidence extraction; called in a worker thread."""
    from dataclasses import asdict

    from app.config import settings
    from app.services.extraction.document_annotator import annotate_excel, annotate_word

    file_path = Path(job.document_path)
    if job.source_type == "excel":
        content, warnings, triples, ckpt = annotate_excel(
            file_path,
            engine,
            progress_fn=progress_fn,
            should_pause_fn=should_pause_fn,
            checkpoint=checkpoint,
            structure_only=preview_only,
        )
        result = {
            "_version": _ANNOTATOR_VERSION,
            "source_type": "excel",
            "filename": job.source_filename,
            "content": content,
            "warnings": warnings,
            "triples": triples,
            "doc_class": None,
            "relationships": [],
            "preview_only": preview_only,
        }
        if ckpt is not None:
            result["_checkpoint"] = ckpt
        return result
    if job.source_type != "word":
        raise ValueError(f"不支持的源类型标注：{job.source_type}")

    from app.services.extraction.evidence_preview import preview_relationships
    from app.services.extraction.local_semantic_model import configured_generic_runner
    from app.services.extraction.word_analysis import analyze_word_core
    from app.services.extraction.word_tree_summarizer import (
        fallback_word_tree_summaries,
        summarize_word_tree,
    )
    from app.services.llm.local_client import get_local_llm

    role = (
        "default_source"
        if (job.source_config or {}).get("mode") == "template_default"
        else "analysis_source"
    )
    with measure("document_parse"):
        analysis = analyze_word_core(file_path, source_filename=job.source_filename, role=role)
    structure = analysis.structure
    content, warnings, _legacy_triples, _ckpt = annotate_word(
        file_path,
        engine=None,
        structure_only=True,
        rich_style=True,
        structure=structure,
        ir=analysis.ir,
    )
    if preview_only:
        # A document read must not wait for extraction or model-generated summaries.
        # Do not fabricate an evidence run or overwrite the background extraction cache.
        effective_class = (job.source_config or {}).get("doc_class_iri") or ""
        return {
            "_version": _ANNOTATOR_VERSION,
            "_parser_version": structure.parser_version,
            "source_type": "word",
            "filename": job.source_filename,
            "content": content,
            "analysis": analysis.ir.model_dump(mode="json"),
            "warnings": [*structure.warnings, *warnings],
            "triples": [],
            "relationships": [],
            "doc_class": {
                "doc_class_iri": effective_class,
                "label": effective_class,
                "score": 1.0,
                "signals": ["user_override"],
            }
            if effective_class
            else None,
            "section_tree": structure.section_tree.to_dict(),
            "pagination": asdict(structure.pagination),
            "preview_only": True,
            "completion": "incomplete",
            "degraded": False,
        }
    if progress_fn:
        progress_fn("typing")
    runner = configured_generic_runner(engine)
    runner.assert_owner_fn = assert_owner_fn
    if schema_snapshot_fn:
        schema_snapshot_fn(runner.schema)
    runner.priority_paths = [
        tuple(path) for path in (job.source_config or {}).get("extraction_priority_paths", [])
    ]
    effective_class = (job.source_config or {}).get("doc_class_iri") or ""

    def on_snapshot(partial):
        if snapshot_fn:
            snapshot_fn({"source_type": "word", "analysis": analysis.ir.model_dump(mode="json"),
                         "evidence_run": partial.model_dump(
                             mode="json", exclude={"tasks", "checkpoint"})})

    try:
        run = runner.run(
            analysis.ir,
            effective_class=effective_class,
            checkpoint=checkpoint,
            should_pause=should_pause_fn,
            retry_failed=retry_failed,
            pause_after=pause_after,
            checkpoint_fn=checkpoint_fn,
            snapshot_fn=on_snapshot if snapshot_fn else None,
        )
    finally:
        from app.services.extraction.local_semantic_model import ServerTokenizer

        if isinstance(getattr(runner, "tokenizer", None), ServerTokenizer):
            runner.tokenizer.close()
    classification = (
        {
            "doc_class_iri": effective_class,
            "label": engine.get_class_label(effective_class) or effective_class,
            "score": 1.0,
            "signals": ["user_override"],
        }
        if effective_class
        else None
    )
    try:
        if run.completion != "complete" or (should_pause_fn and should_pause_fn()):
            fallback_word_tree_summaries(structure)
        elif defer_summary_fn:
            fallback_word_tree_summaries(structure)

            def finish_summary():
                summarize_word_tree(structure, get_local_llm(),
                                    should_stop_fn=should_pause_fn or assert_owner_fn)
                return structure.section_tree.to_dict()

            defer_summary_fn(finish_summary)
        else:
            summarize_word_tree(structure, get_local_llm(), progress_fn=progress_fn)
    except Exception:
        logger.warning("Word 章节摘要失败，保留原文结构", exc_info=True)
        fallback_word_tree_summaries(structure)
    result = {
        "_version": _ANNOTATOR_VERSION,
        "_parser_version": structure.parser_version,
        "_summary_prompt_version": settings.word_tree_summary_prompt_version,
        "source_type": "word",
        "filename": job.source_filename,
        "content": content,
        "analysis": analysis.ir.model_dump(mode="json"),
        "warnings": [*structure.warnings, *warnings, *run.diagnostics],
        "triples": [],  # legacy dictionary candidates are never persisted from this path
        "doc_class": classification,
        "relationships": preview_relationships(run.candidates, runner.schema),
        "evidence_run": run.model_dump(mode="json"),
        "section_tree": structure.section_tree.to_dict(),
        "pagination": asdict(structure.pagination),
        "completion": run.completion,
        "degraded": run.degraded,
    }
    if run.completion != "complete" and run.checkpoint:
        result["_checkpoint"] = run.checkpoint
    return result


def _write_annotation_cache(job_id, payload: dict, *, expected_run=None) -> None:
    from app.services.extraction.checkpoint_journal import write_snapshot

    with _annotation_cache_lock:
        cache_path = _annotation_cache_path(job_id)
        if expected_run is not None:
            latest = (json.loads(cache_path.read_text(encoding="utf-8"))
                      if cache_path.is_file() else {})
            if latest.get("annotation_run_id") != expected_run:
                return
        write_snapshot(cache_path, payload)


@router.get("/jobs/{job_id}/annotated-document")
def get_annotated_document(
    job_id: UUID,
    refresh: bool = False,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    """读取已有标注；Word 无有效缓存时只解析正文，不等待模型抽取或摘要。

    ``?refresh=1`` 对 Word 仅重读源文档；语义抽取由显式写接口/后台任务驱动。
    正文预览不写候选、作业状态或标注缓存，不会覆盖并发抽取的结果。
    同步 SQL/文件读取和解析由 FastAPI 在线程池执行。
    """
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)
    _require_preview_capability(job)

    cache_path = _annotation_cache_path(job_id)
    source_only_preview = (
        _is_word_source(job.source_type)
        and _word_job_mode(job) == _WORD_REPOSITORY_PREVIEW_MODE
    )
    if not source_only_preview and not refresh and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if _annotation_cache_is_current(cached):
                return cached
            logger.info("标注缓存版本过期，读取正文预览：%s", cache_path)
        except Exception:
            logger.warning("标注缓存损坏，回退正文预览：%s", cache_path, exc_info=True)

    if not job.document_path or not Path(job.document_path).is_file():
        # 刷新但源文档已清理：优雅降级为已有缓存（即便版本旧），而非 404。
        if not source_only_preview and cache_path.is_file():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("标注缓存损坏：%s", cache_path, exc_info=True)
        raise HTTPException(404, "源文档不可用（已清理或未持久化）")
    if job.source_type not in ("word", "excel"):
        raise HTTPException(400, f"不支持的源类型标注：{job.source_type}")

    # Both formats use the fenced background worker for recognition. A GET only previews.
    return _compute_annotation(job, engine, preview_only=True)


def _persist_ner_triples(job_id, triples: list[dict], db: Session, *, commit=True) -> int:
    """NER 三元组 → ExtractionCandidate（入复核队列，candidate_kind='ner_triple'）。"""
    added = 0
    for triple in triples:
        if not triple.get("properties"):
            continue
        props = {p["iri"]: p["value"] for p in triple["properties"]}
        db.add(
            ExtractionCandidate(
                job_id=job_id,
                target_class_iri=triple["entity_class_iri"],
                extracted_properties=props,
                candidate_kind="ner_triple",
                group_key=f"ner:{triple['entity_text']}",
                source_ref=(
                    f"ner#seg{triple['segment_index']}:{triple['span_start']}-{triple['span_end']}"
                ),
                alignment_result="new",
                review_status="pending",
            )
        )
        added += 1
    if added and commit:
        db.commit()
    return added


@timed("graph_transaction")
def _persist_evidence_payload(
    job_id,
    payload: dict,
    db: Session,
    *,
    actor="evidence-extractor",
    incremental=False,
    commit=True,
) -> dict:
    """Persist only server-produced evidence, not legacy flattened triples or client state."""
    from sqlalchemy import func, select

    from app.models.evidence import EvidenceCandidateRecord, EvidenceJobState
    from app.schemas.evidence import Candidate
    from app.services.extraction.candidate_store import CandidateStore
    from app.services.extraction.document_ir import DocumentIR

    if (
        payload.get("source_type") != "word"
        or "analysis" not in payload
        or "evidence_run" not in payload
    ):
        raise ValueError("shared IR and evidence run are required")
    store = CandidateStore(db)
    ir = DocumentIR.model_validate(payload["analysis"])
    run = payload["evidence_run"]
    candidates = [Candidate.model_validate(c) for c in run["candidates"]]
    stored = store.persist_validated(job_id, candidates, actor=actor, commit=False)
    # Checkpoints retain source-local IDs for deterministic task restoration;
    # public candidates use job-isolated IDs from the candidate store.
    store.save_analysis(
        job_id,
        ir,
        {**run, "candidates": [c.model_dump(mode="json") for c in stored]},
        commit=False,
    )
    job = db.get(ExtractionJob, job_id)
    job.total_candidates = db.scalar(select(func.count()).select_from(EvidenceCandidateRecord)
                                    .where(EvidenceCandidateRecord.job_id == job_id))
    job.status = "annotating" if incremental else ("reviewing" if stored else "completed")
    job.error_message = None
    revision = db.get(EvidenceJobState, job_id).revision
    counts = {"candidate_count": job.total_candidates, "data_revision": revision}
    if commit:
        db.commit()
    return counts


def _annotation_checkpoint_path(job_id) -> Path:
    return Path("data/uploads") / f"{job_id}.annotation_checkpoint.json"


def _write_annotation_checkpoint(job_id, ckpt: dict) -> None:
    from app.services.extraction.checkpoint_journal import write_snapshot

    write_snapshot(_annotation_checkpoint_path(job_id), ckpt)


def _load_annotation_checkpoint(job_id) -> dict | None:
    from app.services.extraction.checkpoint_journal import read_checkpoint

    try:
        return read_checkpoint(_annotation_checkpoint_path(job_id))
    except (OSError, ValueError, KeyError):
        logger.warning("标注断点读取失败 job=%s", job_id, exc_info=True)
        return None


def _clear_annotation_checkpoint(job_id) -> None:
    from app.services.extraction.checkpoint_journal import journal_path

    _annotation_checkpoint_path(job_id).unlink(missing_ok=True)
    journal_path(_annotation_checkpoint_path(job_id)).unlink(missing_ok=True)


_ANNOTATION_STAGE_PCT = {
    "gliner": 10,
    "typing": 30,
    "triples": 50,
    "done": 60,
    "summarizing": 80,
}


def _checkpoint_counts(value):
    from app.config import settings

    return {
        "tasks_processed": value.get("attempt_count", 0),
        "tasks_completed": len(value.get("completed", {})),
        "tasks_failed": sum(not f.get("interrupted") for f in value.get("failures", {}).values()),
        "model_calls": value.get("model_calls", 0),
        "stage_statistics": value.get("stage_statistics", {}),
        "has_checkpoint": True,
        "can_resume": value.get("attempt_count", 0) < settings.evidence_max_tasks,
    }


def _claim_annotation(job_id, db, *, mode="continue", actor="evidence-extractor",
                      retry_failed=False, pause_after=None, template_id=None, reason=""):
    """All starts share one SQL owner and one authoritative checkpoint journal."""
    from app.models.evidence import EvidenceJobState
    from app.models.extraction import AnnotationExecution
    from app.services.extraction.annotation_execution import ExecutionBusy, claim

    if retry_failed and not reason.strip():
        raise HTTPException(422, "an explicit retry reason is required")
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    _require_annotation_capability(job)
    from app.models.extraction import AstTemplate

    config = job.source_config or {}
    selected = template_id or config.get("recognition_template_id") or config.get("template_id")
    selected_row = db.get(AstTemplate, UUID(str(selected))) if selected else None
    if selected_row and (selected_row.schema_json or {}).get("demo_profile"):
        raise HTTPException(409, "演示模板使用共享静态图谱，无需抽取")
    if job.source_type not in ("word", "excel"):
        raise HTTPException(422, "仅 Word/Excel 作业支持关系识别")
    if not job.document_path or not Path(job.document_path).is_file():
        raise HTTPException(422, "源文档不可用，无法重新识别")
    managed = db.get(AnnotationExecution, job_id) is not None
    options = {"mode": mode, "retry_failed": retry_failed, "pause_after": pause_after}
    try:
        run_id = claim(db, job_id, actor=actor, options=options)
        checkpoint = None
        if mode in {"resume", "continue"}:
            checkpoint = _load_annotation_checkpoint(job_id)
            if checkpoint is None and _annotation_checkpoint_path(job_id).exists():
                raise HTTPException(409, "识别断点损坏，请检查后重新识别")
            if checkpoint is None and not managed:
                # One-time adoption of the old synchronous endpoint's SQL checkpoint.
                # Once managed, a stale SQL run must never replace the journal.
                state = db.get(EvidenceJobState, job_id)
                checkpoint = (state.extraction_run or {}).get("checkpoint") if state else None
            if mode == "resume" and checkpoint is None:
                raise HTTPException(409, "没有可恢复的标注断点")
            from app.config import settings

            if checkpoint and checkpoint.get("attempt_count", 0) >= settings.evidence_max_tasks:
                raise HTTPException(422, "本轮已达到处理上限，请检查未通过的任务和抽取配置")
        if mode in {"rerun", "start", "auxiliary"}:
            _backfill_job_document_context(job, db)
            _set_annotation_template(job, template_id, db)
        else:
            _backfill_job_document_context(job, db)
            if "extraction_priority_paths" not in (job.source_config or {}):
                _set_annotation_template(job, None, db)
        # Validate before touching files; the claim still holds the row write lock.
        if mode in {"rerun", "start", "auxiliary"}:
            _clear_annotation_checkpoint(job_id)
            with _annotation_cache_lock:
                _annotation_cache_path(job_id).unlink(missing_ok=True)
        elif checkpoint is not None and not _annotation_checkpoint_path(job_id).is_file():
            _write_annotation_checkpoint(job_id, checkpoint)
        head = db.get(AnnotationExecution, job_id, populate_existing=True)
        head.progress = {
            "job_id": str(job_id), "run_id": run_id, "stage": "annotating",
            "annotation_stage": "queued", "pct": 0, "status": "running", "degraded": False,
            "has_checkpoint": checkpoint is not None, "can_resume": checkpoint is not None,
            **(_checkpoint_counts(checkpoint) if checkpoint else {}),
        }
        if retry_failed:
            audit.append(db, "evidence.extract_retry", actor=actor, entity_iri=str(job_id),
                         details={"reason": reason, "run_id": run_id,
                                  "input_id": (checkpoint or {}).get("input_id")}, commit=False)
        if mode != "auxiliary":
            job.status, job.error_message = "annotating", None
        db.commit()
    except ExecutionBusy as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except BaseException:
        db.rollback()
        raise
    if mode != "auxiliary":
        progress_bus.reset(str(job_id))
    progress_bus.publish(str(job_id), dict(head.progress))
    return {"job_id": str(job_id), "run_id": run_id, "status": "queued",
            "has_checkpoint": checkpoint is not None}


def _enqueue_annotation(job_id, background, engine, db, **options):
    receipt = _claim_annotation(job_id, db, **options)
    # Pass only immutable identifiers and the connection factory, never a request session.
    background.add_task(_precompute_annotation_bg, job_id, engine, db.get_bind(),
                        run_id=receipt["run_id"])
    return receipt


async def _precompute_annotation_bg(job_id, engine: OntologyEngine, bind, *, run_id=None,
                                    mode="start", actor="evidence-extractor"):
    """The worker owns its SQL session and renews its cross-process lease."""
    import asyncio

    from app.services.extraction.annotation_execution import WorkerLease, begin_worker
    from app.services.llm.model_runtime import model_scope

    if isinstance(bind, Session):  # internal legacy callers; never passed by HTTP enqueue
        bind = bind.get_bind()

    def worker():
        with Session(bind=bind, expire_on_commit=False) as worker_db, tracking() as performance:
            job = worker_db.get(ExtractionJob, job_id)
            if job is None or not _word_job_allows_annotation(job):
                logger.warning("Refused retired Word worker delivery job=%s", job_id)
                return
            token = run_id
            if token is None:
                token = _claim_annotation(job_id, worker_db, mode=mode, actor=actor)["run_id"]
            if not begin_worker(worker_db, job_id, token):
                return
            with WorkerLease(bind, job_id, token) as lease:
                with model_scope(bind=bind, job_id=job_id, run_id=token, should_stop=lease.check):
                    _annotation_worker(job_id, engine, worker_db, performance, lease)

    await asyncio.to_thread(worker)


def _annotation_worker(job_id, engine, db, performance, lease):
    from app.models.extraction import AnnotationExecution
    from app.services.extraction.annotation_execution import ExecutionLost, fence

    job = db.get(ExtractionJob, job_id)
    if job is None or not _word_job_allows_annotation(job):
        logger.warning("Stopped retired Word worker job=%s", job_id)
        return
    job_id_str = str(job_id)
    from app.config import settings

    started_at = time()
    counts = {}
    from app.services.extraction.checkpoint_journal import CheckpointJournal

    journal = CheckpointJournal(_annotation_checkpoint_path(job_id))
    summaries = []
    run_token = lease.run_id
    head = db.get(AnnotationExecution, job_id)
    options, actor = dict(head.options), head.actor
    controls_job_status = options.get("mode") != "auxiliary"
    counts.update({key: head.progress[key] for key in _checkpoint_counts({})
                   if key in head.progress})
    db.commit()

    def event(sub_stage, **extra):
        return {
                "job_id": job_id_str,
                "run_id": run_token,
                "stage": "annotating",
                "annotation_stage": sub_stage,
                "pct": _ANNOTATION_STAGE_PCT.get(sub_stage, 0),
                "status": "running",
                "degraded": False,
                "started_at": started_at,
                "updated_at": time(),
                **counts,
                **extra,
        }

    def on_progress(sub_stage: str):
        value = event(sub_stage)
        with fence(db, job_id, run_token) as head:
            head.progress = value
        progress_bus.publish(job_id_str, value)

    def on_checkpoint(value: dict):
        counts.update(_checkpoint_counts(value))
        progress = event("extracting")
        with fence(db, job_id, run_token) as head:
            journal.save(value)
            head.progress = progress
        progress_bus.publish(job_id_str, progress)

    def on_snapshot(partial):
        with fence(db, job_id, run_token) as head:
            counts.update(_persist_evidence_payload(job_id, partial, db, incremental=True,
                                                   actor=actor, commit=False))
            has_relationship = any(
                c["kind"] == "relationship" for c in partial["evidence_run"]["candidates"]
            )
            if "first_relationship_seconds" not in counts and has_relationship:
                counts["first_relationship_seconds"] = round(time() - started_at, 3)
            head.progress = event("extracting")
            value = dict(head.progress)
        progress_bus.publish(job_id_str, value)

    def should_pause() -> bool:
        return lease.check()

    def on_model_progress(request):
        # The model loop owns this short session; never share the worker's Session
        # with a cancellation monitor or hold a graph transaction during HTTP wait.
        with Session(db.get_bind()) as progress_db:
            with fence(progress_db, job_id, run_token) as progress_head:
                value = dict(progress_head.progress)
                value.update(model_request=request, http_attempts=request["http_attempts"],
                             updated_at=time())
                if request.get("model_calls") is not None:
                    value["model_calls"] = request["model_calls"]
                progress_head.progress = value
        counts["http_attempts"] = request["http_attempts"]
        counts["model_request"] = request
        if request.get("model_calls") is not None:
            counts["model_calls"] = request["model_calls"]
        progress_bus.publish(job_id_str, value)

    try:
        if job is None or not job.document_path or not Path(job.document_path).is_file():
            raise ValueError("persisted source unavailable")
        lease.check()
        checkpoint = _load_annotation_checkpoint(job_id)
        from app.services.llm.model_runtime import ModelCancelled, model_scope

        with model_scope(progress=on_model_progress):
            payload = _compute_annotation(
                job,
                engine,
                on_progress,
                should_pause,
                checkpoint,
                retry_failed=options.get("retry_failed", False),
                pause_after=options.get("pause_after"),
                checkpoint_fn=on_checkpoint,
                snapshot_fn=on_snapshot,
                defer_summary_fn=summaries.append,
                schema_snapshot_fn=lambda schema: _capture_model(db, schema),
                assert_owner_fn=lease.check,
            )

        payload["annotation_run_id"] = run_token
        ckpt = payload.pop("_checkpoint", None)
        if ckpt is not None:
            with fence(db, job_id, run_token) as head:
                if job.source_type == "word":
                    counts.update(_persist_evidence_payload(job_id, payload, db,
                                                           actor=actor, commit=False))
                _write_annotation_cache(job_id, payload)
                journal.save(ckpt, force=True)
                if controls_job_status:
                    job.status = "paused"
                head.status = "paused"
                head.progress = event(
                    "paused", status="paused", has_checkpoint=True,
                    can_resume=ckpt.get("attempt_count", 0) < settings.evidence_max_tasks,
                )
                value = dict(head.progress)
            progress_bus.publish(job_id_str, value)
            return

        for warn in payload.get("warnings") or []:
            logger.warning("标注告警 job=%s: %s", job_id, warn)
        with fence(db, job_id, run_token) as head:
            if job.source_type == "word":
                counts.update(_persist_evidence_payload(job_id, payload, db,
                                                       actor=actor, commit=False))
            else:
                triples = payload.get("triples") or []
                _persist_ner_triples(job_id, triples, db, commit=False)
                if controls_job_status:
                    job.status = "reviewing" if triples else "completed"
            if payload.get("evidence_run"):
                payload["evidence_run"]["performance"] = performance.snapshot()
            _write_annotation_cache(job_id, payload)
            head.progress = event("summarizing" if summaries else "extracting")
            value = dict(head.progress)
        progress_bus.publish(job_id_str, value)
        # Graph candidates are already visible; summaries retain the same owner.
        for summarize in summaries:
            try:
                if lease.check():
                    break
                with model_scope(progress=on_model_progress, stage="chapter_summary"):
                    tree = summarize()
                with fence(db, job_id, run_token):
                    cache = _annotation_cache_path(job_id)
                    latest = (json.loads(cache.read_text(encoding="utf-8"))
                              if cache.is_file() else {})
                    if latest.get("annotation_run_id") == run_token:
                        latest["section_tree"] = tree
                        _write_annotation_cache(job_id, latest, expected_run=run_token)
            except ExecutionLost:
                raise
            except ModelCancelled:
                break
            except Exception:
                logger.warning("Word 章节摘要失败，保留已保存的图谱 job=%s", job_id, exc_info=True)
        if lease.check():
            with fence(db, job_id, run_token) as head:
                if controls_job_status:
                    job.status = "paused"
                head.status = "paused"
                head.progress = event("paused", status="paused", has_checkpoint=True,
                                      can_resume=True)
                value = dict(head.progress)
            progress_bus.publish(job_id_str, value)
            return
        with fence(db, job_id, run_token) as head:
            _clear_annotation_checkpoint(job_id)
            counts.update(has_checkpoint=False, can_resume=False)
            value = event("complete", stage="done", status="done", pct=100)
            head.status, head.progress = "complete", value
        progress_bus.publish(job_id_str, value)
    except ExecutionLost:
        db.rollback()
        logger.info("Stopped stale annotation worker job=%s run=%s", job_id, run_token)
    except Exception:
        db.rollback()
        logger.warning("标注预计算失败 job=%s", job_id, exc_info=True)
        try:
            with fence(db, job_id, run_token) as head:
                if journal.previous is not None:
                    journal.save(journal.previous, force=True)
                job = db.get(ExtractionJob, job_id)
                if job and controls_job_status:
                    job.status = "failed"
                    job.error_message = (
                        "evidence extraction or persistence failed; no facts published"
                    )
                head.status = "failed"
                head.progress = event("failed", status="failed",
                    has_checkpoint=_annotation_checkpoint_path(job_id).is_file())
                value = dict(head.progress)
            progress_bus.publish(job_id_str, value)
        except ExecutionLost:
            pass


@router.post("/jobs/auto", response_model=ExtractionJobResponse, status_code=202)
async def create_auto_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    source_type: str = Form(...),
    target_class_iris: str | None = Form(None),
    config_id: UUID | None = Form(None),
    doc_class_iri: str | None = Form(None),
    purpose: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """Create structured extraction jobs or a source-only doc-repository preview.

    Ordinary Word recognition is retired here. ``doc_repo_preview`` persists a
    Word source so the document repository can render its structure, but never
    claims a legacy annotation lease or creates graph candidates.
    """
    is_word = _is_word_source(source_type)
    if is_word and purpose != _WORD_REPOSITORY_PREVIEW_MODE:
        _reject_retired_word_recognition()
    if not is_word:
        if config_id is None:
            raise HTTPException(
                422, "SEMANTIC_MAPPING_REQUIRED: select an extraction configuration"
            )
        config = db.get(ExtractionConfig, config_id)
        if config is None or config.source_type != source_type or not config.column_mapping:
            raise HTTPException(
                422, "SEMANTIC_MAPPING_REQUIRED: configuration must map source fields"
            )
        return await create_job(
            background,
            source_type,
            config_id,
            None,
            file,
            None,
            db,
            engine,
            identity,
        )
    suffix = Path(file.filename or "").suffix or ".bin"
    filename = file.filename or "unknown"

    source_config: dict = {"mode": _WORD_REPOSITORY_PREVIEW_MODE}
    if doc_class_iri:
        source_config["doc_class_iri"] = doc_class_iri
    job = ExtractionJob(
        source_type="word",
        source_filename=filename,
        source_config=source_config,
        status="completed",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    uploads_dir = Path("data/uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    persistent_path = uploads_dir / f"{job.id}{suffix}"
    content = await file.read()
    persistent_path.write_bytes(content)
    job.document_path = str(persistent_path)
    db.commit()

    audit.append(
        db,
        "extraction.job.create_preview",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": "word", "mode": _WORD_REPOSITORY_PREVIEW_MODE},
    )
    return job


@router.get("/jobs/{job_id}/progress")
async def job_progress(
    job_id: UUID,
    identity: Identity = Depends(get_current_user_sse),
    db: Session = Depends(get_db),
):
    """SSE：逐阶段推送 parsing→extracting→aligning→reviewing（FR-002）。"""
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    _require_preview_capability(job)
    from app.services.extraction.annotation_execution import public_progress

    if public_progress(db, job_id) is not None:
        import asyncio

        from app.models.extraction import AnnotationExecution

        head = db.get(AnnotationExecution, job_id)
        # Preserve the structured import's stage history. Optional Excel
        # annotation has its own durable status and cannot replace import results.
        pipeline_events = [e for e in progress_bus.history(str(job_id))
                           if not e.get("annotation_stage")] if (
            head.options.get("mode") == "auxiliary"
        ) else []
        bind = db.get_bind()
        db.rollback()  # SSE must not hold a request transaction for its lifetime.

        def latest():
            with Session(bind) as reader:
                return public_progress(reader, job_id)

        async def durable_events():
            for value in pipeline_events:
                yield f"data: {json.dumps(value, ensure_ascii=False)}\n\n"
            previous = None
            deadline = time() + 30
            while time() < deadline:
                value = await asyncio.to_thread(latest)
                if value is None:
                    return
                if value != previous:
                    yield f"data: {json.dumps(value, ensure_ascii=False)}\n\n"
                    previous = value
                if value.get("status") != "running":
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(durable_events(), media_type="text/event-stream")
    # Process-local events disappear on restart, while the SQL status may still
    # say annotating. Report an interruption instead of streaming nothing forever.
    fallback = None
    if not progress_bus.history(str(job_id)) and job.source_type in ("word", "excel"):
        from app.config import settings

        checkpoint = _load_annotation_checkpoint(job_id)
        terminal = {
            "annotating": "interrupted",
            "paused": "paused",
            "failed": "failed",
            "reviewing": "complete",
            "completed": "complete",
        }.get(job.status)
        if terminal:
            fallback = {
                "job_id": str(job_id),
                "stage": "annotating",
                "annotation_stage": terminal,
                "pct": 100 if terminal == "complete" else 0,
                "status": "done" if terminal == "complete" else terminal,
                "degraded": False,
                "has_checkpoint": checkpoint is not None,
                "can_resume": checkpoint is not None
                and checkpoint.get("attempt_count", 0) < settings.evidence_max_tasks,
                "tasks_processed": (checkpoint or {}).get("attempt_count", 0),
                "tasks_completed": len((checkpoint or {}).get("completed", {})),
                "tasks_failed": len((checkpoint or {}).get("failures", {})),
                "model_calls": (checkpoint or {}).get("model_calls", 0),
            }

    async def event_gen():
        if fallback is not None:
            yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
            return
        annotation_events = all(
            e.get("annotation_stage") for e in progress_bus.history(str(job_id))
        )
        async for ev in progress_bus.stream(str(job_id), latest_only=annotation_events):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Annotation control endpoints (pause / resume / rerun)
# ---------------------------------------------------------------------------


@router.post("/jobs/{job_id}/annotation/pause")
def pause_annotation(
    job_id: UUID,
    identity: Identity = Depends(_analyst),
    db: Session = Depends(get_db),
):
    """请求暂停正在运行的标注任务（下一阶段间生效）。"""
    from app.services.extraction.annotation_execution import request_pause

    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    _require_annotation_capability(job)
    if not request_pause(db, job_id):
        raise HTTPException(409, "该作业没有正在运行的识别任务")
    return {"status": "pause_requested"}


@router.post("/jobs/{job_id}/annotation/resume", status_code=202)
async def resume_annotation(
    job_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """Resume through the same fenced worker as continue/retry and fresh runs."""
    receipt = _enqueue_annotation(job_id, background, engine, db, mode="resume",
                                  actor=identity.username)
    return {**receipt, "status": "resumed"}


@router.post("/jobs/{job_id}/annotation/rerun", status_code=202)
async def rerun_annotation(
    job_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
    template_id: UUID | None = None,
):
    """Start a new generation only after claiming exclusive ownership."""
    receipt = _enqueue_annotation(job_id, background, engine, db, mode="rerun",
                                  actor=identity.username, template_id=template_id)
    return {**receipt, "status": "restarted"}


def _set_annotation_template(job, template_id, db):
    """Freeze the selected template's declarative priorities for this run/resume."""
    from app.models.extraction import AstTemplate
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.template_priorities import template_priority_paths

    config = dict(job.source_config or {})
    selected = template_id or config.get("recognition_template_id") or config.get("template_id")
    if not selected:
        return
    row = db.get(AstTemplate, UUID(str(selected)))
    if row is None:
        if template_id:
            raise HTTPException(404, "模板不存在")
        return
    root_class = config.get("doc_class_iri") or row.iri_pattern or ""
    config.update(
        recognition_template_id=str(row.id),
        extraction_priority_template_hash=evidence_hash(row.schema_json),
        extraction_priority_paths=template_priority_paths(row.schema_json, root_class),
    )
    job.source_config = config


_DOCUMENT_JOB_KEYS = (
    "job_id",
    "jobId",
    "source_job_id",
    "extraction_job_id",
    "hasJob",
    "sourceJob",
)


def _backfill_job_document_context(job: ExtractionJob, db: Session) -> None:
    """Recover authoritative document type/provenance for historical upload jobs.

    Current uploads persist ``doc_class_iri`` on the job before the document shadow
    exists.  Older uploads only retained the reverse ``document -> job`` reference,
    so a rerun would otherwise scan the entire ontology instead of using the user's
    selected input type.  Existing job metadata always wins.
    """
    config = dict(job.source_config or {})
    if config.get("doc_class_iri") and config.get("doc_ref"):
        return
    job_id = str(job.id)
    shadows = db.query(EntityShadow).filter(EntityShadow.module == "document").all()
    for shadow in shadows:
        properties = shadow.properties_json or {}
        if not any(str(properties.get(key) or "") == job_id for key in _DOCUMENT_JOB_KEYS):
            continue
        config.setdefault("doc_class_iri", shadow.class_iri)
        config.setdefault("doc_ref", shadow.iri)
        job.source_config = config
        return
    # Old template sources can predate the persisted document type on the job.
    if config.get("template_id") and not config.get("doc_class_iri"):
        from app.models.extraction import AstTemplate

        template = db.get(AstTemplate, UUID(str(config["template_id"])))
        if template and template.iri_pattern:
            job.source_config = {**config, "doc_class_iri": template.iri_pattern}


@router.get("/jobs/{job_id}/candidates", response_model=GroupedCandidatesResponse)
def list_candidates(job_id: UUID, db: Session = Depends(get_db)):
    """按 group_key 归组返回候选；未归组的单列（FR-009/SC-003）。"""
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    _require_result_capability(job)
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
        group_models.append(
            CandidateGroup(
                group_key=gkey,
                canonical_candidate_id=canonical.id if canonical else None,
                candidates=[ExtractionCandidateResponse.model_validate(m) for m in members],
            )
        )

    return GroupedCandidatesResponse(
        job_id=job_id,
        groups=group_models,
        ungrouped=[ExtractionCandidateResponse.model_validate(m) for m in ungrouped],
    )


_FACTS_NS = "http://slpra.org/facts#"


def _document_phase(doc_iri: str, db: Session) -> str | None:
    """取文档个体（facts# 影子行）的研发阶段 IRI；不存在则 None（不臆造）。"""
    shadow = db.query(EntityShadow).filter(EntityShadow.iri == doc_iri).one_or_none()
    if shadow is None:
        return None
    return (shadow.properties_json or {}).get("hasDevelopmentPhase")


def _commit_candidate(candidate: ExtractionCandidate, engine, db: Session) -> str:
    """Legacy dictionary records cannot prove per-value provenance or graph publication."""
    raise HTTPException(409, "legacy_unverified：请重新抽取或逐值录入证据候选，再独立审核与提交")


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
    _require_result_capability(candidate.job)
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
        db,
        action,
        actor=identity.username,
        entity_iri=str(candidate_id),
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
    _require_result_capability(target.job)
    affected = [target]
    for sid in req.source_ids:
        src = db.get(ExtractionCandidate, sid)
        if not src:
            continue
        _require_result_capability(src.job)
        src.merged_into_id = target.id
        src.review_status = "merged"
        affected.append(src)
    db.commit()
    audit.append(
        db,
        "extraction.candidate.merge",
        actor=identity.username,
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
    _require_result_capability(candidate.job)
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
        db,
        "extraction.candidate.split",
        actor=identity.username,
        entity_iri=str(candidate_id),
        details={"derived": len(derived)},
    )
    for c in derived:
        db.refresh(c)
    return derived


# --------------------------------------------------------------------------- #
# 010 — Risk assessment report generation (FR-006/FR-013/FR-014)
# --------------------------------------------------------------------------- #


def _llm_report_flags_active() -> bool:
    """Check if any LLM report enhancement flag is on (013)."""
    from app.config import settings

    return settings.llm_report_merge_values or settings.llm_report_narrative_enabled


def _build_and_save_report(
    job_id: UUID,
    report_id: UUID,
    **_legacy_arguments,
) -> None:
    from app.db import SessionLocal
    from app.services.reporting.report_run_service import ReportRunService

    with SessionLocal() as db:
        record = db.get(GeneratedReport, report_id)
        if record is None or record.job_id != job_id or not record.report_run_id:
            raise ValueError("TEMPLATE_MIGRATION_REQUIRED")
        ReportRunService(db).execute(record.report_run_id)


@router.post("/jobs/{job_id}/risk-report", status_code=202)
def generate_risk_report(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    template_id: UUID | None = None,
    snapshot_id: str | None = None,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    from app.services.reporting.legacy_facade import generate

    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    _require_result_capability(job)
    return generate(db, job_id, identity.username, template_id=template_id, snapshot_id=snapshot_id)


@router.get("/jobs/{job_id}/risk-report")
def get_risk_report(
    job_id: UUID,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """Retrieve the most recently generated risk assessment report."""
    from fastapi.responses import FileResponse

    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")

    report = (
        db.query(GeneratedReport)
        .filter(
            GeneratedReport.job_id == job_id,
            GeneratedReport.report_type != "batch_record_demo",
            GeneratedReport.deleted_at.is_(None),
            or_(
                GeneratedReport.report_status == "completed",
                and_(GeneratedReport.report_status.is_(None), GeneratedReport.file_size > 0),
            ),
        )
        .order_by(GeneratedReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(404, "该作业尚未生成风险评估报告")

    if report.report_run_id:
        from app.services.reporting.report_run_service import ReportRunService

        artifact = ReportRunService(db).download(report.report_run_id, report.report_artifact_id)
        file_path = Path(artifact.file_path)
    else:
        file_path = Path(report.file_path)
    if not file_path.is_file():
        raise HTTPException(404, "报告文件不存在")

    src_name = (job.source_filename or "report").replace(".docx", "")
    download_name = f"风险评估表_{src_name}.docx"
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


# --------------------------------------------------------------------------- #
# 013 — Async report status poll + download by report_id
# --------------------------------------------------------------------------- #


@router.get("/jobs/{job_id}/reports/{report_id}")
def get_report_status(
    job_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """Poll the status of an async-generated report (013)."""
    report = db.get(GeneratedReport, report_id)
    if not report or report.job_id != job_id or report.deleted_at is not None:
        raise HTTPException(404, "报告不存在")
    if report.report_type == "batch_record_demo" and report.actor != identity.username:
        raise HTTPException(404, "报告不存在")
    return {
        "id": str(report.id),
        "job_id": str(report.job_id),
        "report_type": report.report_type,
        "file_path": report.file_path,
        "file_size": report.file_size,
        "rules_fired_count": report.rules_fired_count,
        "rules_summary": report.rules_summary,
        "actor": report.actor,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "report_status": report.report_status,
        "report_error": report.report_error,
        "narratives": report.narratives,
        "report_run_id": report.report_run_id,
        "report_artifact_id": report.report_artifact_id,
    }


@router.get("/jobs/{job_id}/reports/{report_id}/download")
def download_report_by_id(
    job_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """Download a completed report by its ID (013)."""
    from fastapi.responses import FileResponse

    report = db.get(GeneratedReport, report_id)
    if not report or report.job_id != job_id or report.deleted_at is not None:
        raise HTTPException(404, "报告不存在")
    if report.report_type == "batch_record_demo" and report.actor != identity.username:
        raise HTTPException(404, "报告不存在")
    if report.report_status not in {None, "completed"}:
        raise HTTPException(409, f"报告尚未完成（status={report.report_status}）")

    if report.report_run_id:
        from app.services.reporting.report_run_service import ReportRunService

        artifact = ReportRunService(db).download(report.report_run_id, report.report_artifact_id)
        file_path = Path(artifact.file_path)
    else:
        file_path = Path(report.file_path)
    if not file_path.exists():
        raise HTTPException(404, "报告文件不存在")

    job = db.get(ExtractionJob, job_id)
    src_name = (job.source_filename or "report").replace(".docx", "") if job else "report"
    prefix = "批记录报告_演示草稿" if report.report_type == "batch_record_demo" else "风险评估表"
    download_name = f"{prefix}_{src_name}.docx"
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


# --------------------------------------------------------------------------- #
# 011 AST Coverage
# --------------------------------------------------------------------------- #


def _build_ast_coverage_response(job_id, db, template_id=None, engine=None, *, actor):
    from app.services.reporting.coverage_v2 import build_report_inputs

    return build_report_inputs(
        db, db.get(ExtractionJob, job_id), schema={}, actor=actor, template_id=template_id
    )


@router.get("/jobs/{job_id}/ast-coverage")
def get_ast_coverage(
    job_id: UUID,
    template_id: UUID | None = None,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(get_current_user),
):
    """Run coverage validation and return the AST tree with slot status (011 FR-API-001).

    012 extension: optional *template_id* query param overrides default
    template resolution.
    """
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    _require_result_capability(job)
    return _build_ast_coverage_response(
        job_id, db, template_id=template_id, engine=engine, actor=identity.username
    )


@router.get(
    "/jobs/{job_id}/reports",
    response_model=list[GeneratedReportResponse],
)
def list_reports(
    job_id: UUID,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """List all historical reports for a job (011 FR-API-002)."""
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    return (
        db.query(GeneratedReport)
        .filter(
            GeneratedReport.job_id == job_id,
            or_(GeneratedReport.report_type != "batch_record_demo",
                GeneratedReport.actor == identity.username),
            GeneratedReport.deleted_at.is_(None),
            or_(
                GeneratedReport.report_status == "completed",
                and_(GeneratedReport.report_status.is_(None), GeneratedReport.file_size > 0),
            ),
        )
        .order_by(GeneratedReport.created_at.desc())
        .all()
    )


@router.delete("/jobs/{job_id}/reports/{report_id}", status_code=204)
def delete_report(
    job_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_role(ROLE_SENIOR_ANALYST)),
):
    """Soft-delete a generated report."""
    from datetime import datetime, timezone

    report = db.get(GeneratedReport, report_id)
    if not report or report.job_id != job_id:
        raise HTTPException(404, "报告不存在")
    if report.report_type == "batch_record_demo" and report.actor != identity.username:
        raise HTTPException(404, "报告不存在")
    if report.deleted_at is not None:
        raise HTTPException(404, "报告不存在")

    report.deleted_at = datetime.now(timezone.utc)
    audit.append(
        db,
        "report.delete",
        actor=identity.username,
        entity_iri=str(report_id),
        details={"job_id": str(job_id)},
        commit=False,
    )
    db.commit()


@router.post("/jobs/{job_id}/ast-coverage/dismiss", response_model=ASTCoverageResponse)
def dismiss_slot(
    job_id: UUID,
    body: SlotDismissRequest,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    from app.services.reporting.template_v2 import ReportingError

    raise ReportingError("VERSIONED_REQUIREMENT_EDIT_REQUIRED", status=409)


@router.delete(
    "/jobs/{job_id}/ast-coverage/dismiss/{slot_id}",
    response_model=ASTCoverageResponse,
)
def undismiss_slot(
    job_id: UUID,
    slot_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    from app.services.reporting.template_v2 import ReportingError

    raise ReportingError("VERSIONED_REQUIREMENT_EDIT_REQUIRED", status=409)
