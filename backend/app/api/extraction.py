"""Document extraction API routes (能力二：多源抽取 + 跨源对齐 + 人工审核闭环)."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
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
    SlotDismissal,
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
from app.services.extraction.pipeline import run_extraction_pipeline
from app.services.extraction.progress import progress_bus
from app.services.ontology_engine import OntologyEngine

router = APIRouter()
logger = logging.getLogger(__name__)

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
    """后台运行流水线。复用请求会话（FastAPI 在后台任务后才做依赖清理）。

    声明驱动作业（``source_config.class_mapping_id``）无需 ``ExtractionConfig``——
    E6 类绑定即配置源；此时 config 允许为空（FR-007）。
    """
    job = db.get(ExtractionJob, job_id)
    if job is None:
        return
    if job.source_type == "word":
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
        if binding.mapping_type not in SOURCE_ENTITY_MAPPING_TYPES:
            raise HTTPException(
                422,
                "该映射不是源实体绑定（db_table/api_endpoint/doc_pattern），无法驱动抽取",
            )
        if binding.mapping_type == "doc_pattern":
            if file is None:
                raise HTTPException(422, "doc_pattern requires a DOCX file")
            if Path(file.filename or "").suffix.lower() != ".docx":
                raise HTTPException(422, "doc_pattern only accepts .docx files")
        return await _create_declarative_job(
            background,
            source_type,
            class_mapping_id,
            config_id,
            binding,
            db,
            engine,
            identity,
            file=file if binding.mapping_type == "doc_pattern" else None,
        )

    config = db.get(ExtractionConfig, config_id) if config_id else None
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
        background.add_task(_precompute_annotation_bg, job.id, engine, db)
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


_ANNOTATOR_VERSION = 27


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
                "doc_class_iri": effective_class, "label": effective_class,
                "score": 1.0, "signals": ["user_override"],
            } if effective_class else None,
            "section_tree": structure.section_tree.to_dict(),
            "pagination": asdict(structure.pagination),
            "preview_only": True,
            "completion": "incomplete",
            "degraded": False,
        }
    if progress_fn:
        progress_fn("typing")
    runner = configured_generic_runner(engine)
    effective_class = (job.source_config or {}).get("doc_class_iri") or ""
    run = runner.run(
        analysis.ir,
        effective_class=effective_class,
        checkpoint=checkpoint,
        should_pause=should_pause_fn,
        retry_failed=retry_failed,
        pause_after=pause_after,
        checkpoint_fn=checkpoint_fn,
    )
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


def _write_annotation_cache(job_id, payload: dict) -> None:
    cache_path = _annotation_cache_path(job_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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

    cache_path = _annotation_cache_path(job_id)
    if not refresh and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if _annotation_cache_is_current(cached):
                return cached
            logger.info("标注缓存版本过期，读取正文预览：%s", cache_path)
        except Exception:
            logger.warning("标注缓存损坏，回退正文预览：%s", cache_path, exc_info=True)

    if not job.document_path or not Path(job.document_path).is_file():
        # 刷新但源文档已清理：优雅降级为已有缓存（即便版本旧），而非 404。
        if cache_path.is_file():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("标注缓存损坏：%s", cache_path, exc_info=True)
        raise HTTPException(404, "源文档不可用（已清理或未持久化）")
    if job.source_type not in ("word", "excel"):
        raise HTTPException(400, f"不支持的源类型标注：{job.source_type}")

    if job.source_type == "word":
        return _compute_annotation(job, engine, preview_only=True)

    # Preserve the existing Excel path; the model-free preview change is Word-specific.
    payload = _compute_annotation(job, engine)
    _write_annotation_cache(job_id, payload)
    return payload


def _persist_ner_triples(job_id, triples: list[dict], db: Session) -> int:
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
    if added:
        db.commit()
    return added


def _persist_evidence_payload(
    job_id,
    payload: dict,
    db: Session,
    *,
    actor="evidence-extractor",
) -> None:
    """Persist only server-produced evidence, not legacy flattened triples or client state."""
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
    store.save_analysis(job_id, ir)
    stored = store.persist_validated(job_id, candidates, actor=actor)
    # Checkpoints retain source-local IDs for deterministic task restoration;
    # public candidates use job-isolated IDs from the candidate store.
    store.save_analysis(
        job_id,
        ir,
        {**run, "candidates": [c.model_dump(mode="json") for c in stored]},
    )
    job = db.get(ExtractionJob, job_id)
    job.total_candidates = len(store.list(job_id))
    job.status = "reviewing" if stored else "completed"
    job.error_message = None
    db.commit()


def _annotation_checkpoint_path(job_id) -> Path:
    return Path("data/uploads") / f"{job_id}.annotation_checkpoint.json"


def _write_annotation_checkpoint(job_id, ckpt: dict) -> None:
    import os
    import tempfile

    path = _annotation_checkpoint_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A restart or concurrent reader must see a complete old or new checkpoint.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            json.dump(ckpt, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _load_annotation_checkpoint(job_id) -> dict | None:
    path = _annotation_checkpoint_path(job_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _clear_annotation_checkpoint(job_id) -> None:
    _annotation_checkpoint_path(job_id).unlink(missing_ok=True)


_ANNOTATION_STAGE_PCT = {
    "gliner": 10,
    "typing": 30,
    "triples": 50,
    "done": 60,
    "summarizing": 80,
}


async def _precompute_annotation_bg(
    job_id,
    engine: OntologyEngine,
    db: Session,
    checkpoint=None,
):
    """后台预计算文档标注，支持子阶段进度推送和暂停/恢复。

    CPU 阻塞经 ``asyncio.to_thread`` 卸到线程；告警落日志。
    三元组中有属性的实体自动创建 ``ner_triple`` 候选入复核队列。
    失败仅记录、不影响抽取主流程。
    """
    import asyncio

    from app.services.extraction.progress import (
        clear_annotation_control,
        get_annotation_control,
        progress_bus,
    )

    job = db.get(ExtractionJob, job_id)
    if job is None or job.source_type not in ("word", "excel"):
        return
    if not job.document_path or not Path(job.document_path).is_file():
        return

    job_id_str = str(job_id)
    from app.config import settings

    started_at = time()
    counts = {}

    def on_progress(sub_stage: str):
        progress_bus.publish(
            job_id_str,
            {
                "job_id": job_id_str,
                "stage": "annotating",
                "annotation_stage": sub_stage,
                "pct": _ANNOTATION_STAGE_PCT.get(sub_stage, 0),
                "status": "running",
                "degraded": False,
                "started_at": started_at,
                "updated_at": time(),
                **counts,
            },
        )

    def on_checkpoint(value: dict):
        _write_annotation_checkpoint(job_id, value)
        counts.update(
            tasks_processed=value.get("attempt_count", 0),
            tasks_completed=len(value.get("completed", {})),
            tasks_failed=len(value.get("failures", {})),
            model_calls=value.get("model_calls", 0),
        )
        on_progress("extracting")

    def should_pause() -> bool:
        return get_annotation_control(job_id_str) == "pause"

    try:
        payload = await asyncio.to_thread(
            _compute_annotation,
            job,
            engine,
            on_progress,
            should_pause,
            checkpoint,
            checkpoint_fn=on_checkpoint,
        )

        ckpt = payload.pop("_checkpoint", None)
        if ckpt is not None:
            # A bounded/pause result can contain fully validated candidates. Publish
            # them for review and graph preview while retaining the resume checkpoint.
            if job.source_type == "word":
                await asyncio.to_thread(_persist_evidence_payload, job_id, payload, db)
            await asyncio.to_thread(_write_annotation_cache, job_id, payload)
            await asyncio.to_thread(_write_annotation_checkpoint, job_id, ckpt)
            job = db.get(ExtractionJob, job_id)
            if job:
                job.status = "paused"
                db.commit()
            progress_bus.publish(
                job_id_str,
                {
                    "job_id": job_id_str,
                    "stage": "annotating",
                    "annotation_stage": "paused",
                    "pct": _ANNOTATION_STAGE_PCT.get(ckpt.get("completed_stage", ""), 0),
                    "status": "paused",
                    "degraded": False,
                    "has_checkpoint": True,
                    "can_resume": ckpt.get("attempt_count", 0) < settings.evidence_max_tasks,
                    "started_at": started_at,
                    "updated_at": time(),
                    **counts,
                },
            )
            return

        clear_annotation_control(job_id_str)
        _clear_annotation_checkpoint(job_id)

        for warn in payload.get("warnings") or []:
            logger.warning("标注告警 job=%s: %s", job_id, warn)
        if job.source_type == "word":
            await asyncio.to_thread(_persist_evidence_payload, job_id, payload, db)
        await asyncio.to_thread(_write_annotation_cache, job_id, payload)
        triples = payload.get("triples") or []
        if triples:
            await asyncio.to_thread(_persist_ner_triples, job_id, triples, db)

        progress_bus.publish(
            job_id_str,
            {
                "job_id": job_id_str,
                "stage": "done",
                "annotation_stage": "complete",
                "pct": 100,
                "status": "done",
                "degraded": False,
                "started_at": started_at,
                "updated_at": time(),
                **counts,
            },
        )
    except Exception:
        db.rollback()
        job = db.get(ExtractionJob, job_id)
        if job and job.source_type == "word":
            job.status = "failed"
            job.error_message = "evidence extraction or persistence failed; no facts published"
            db.commit()
        logger.warning("标注预计算失败 job=%s", job_id, exc_info=True)
        progress_bus.publish(
            job_id_str,
            {
                "job_id": job_id_str,
                "stage": "annotating",
                "annotation_stage": "failed",
                "pct": 0,
                "status": "failed",
                "degraded": False,
                "has_checkpoint": _annotation_checkpoint_path(job_id).is_file(),
                "started_at": started_at,
                "updated_at": time(),
                **counts,
            },
        )


_AUTO_EXTRACT_KEYWORDS = ["临床备样", "生产信息", "备样生产"]


@router.post("/jobs/auto", response_model=ExtractionJobResponse, status_code=202)
async def create_auto_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    source_type: str = Form(...),
    target_class_iris: str | None = Form(None),
    doc_class_iri: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """自动抽取：上传文件 → 按文件名关键词/指定目标类/文档类型子图 → 多类抽取汇入同一 Job。

    ``doc_class_iri``（报告中心上传时用户指定的文档类型）非空时既约束候选抽取目标类
    （相关类多跳子图），又随 ``source_config`` 传入标注管线约束 NER 候选类（定向识别）。
    """
    suffix = Path(file.filename or "").suffix or ".bin"
    filename = file.filename or "unknown"

    source_config: dict = {"mode": "auto"}
    if doc_class_iri:
        source_config["doc_class_iri"] = doc_class_iri
    job = ExtractionJob(
        source_type=source_type,
        source_filename=filename,
        source_config=source_config,
        status="running",
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

    if source_type == "word":
        # The ontology is a semantic menu, not a filename-driven finder registry.
        audit.append(
            db,
            "extraction.job.create",
            actor=identity.username,
            entity_iri=str(job.id),
            details={"source_type": source_type, "mode": "generic_evidence"},
        )
        background.add_task(_precompute_annotation_bg, job.id, engine, db)
        return job

    iris: list[str] = []
    if target_class_iris:
        iris = json.loads(target_class_iris)
    if not iris and doc_class_iri:
        # 用户指定的文档类型 → 相关类多跳子图，约束候选抽取目标类（空则回退下方启发式）。
        from app.services.extraction.ontology_typer import relevant_classes_for_doc_type

        iris = sorted(relevant_classes_for_doc_type(engine, doc_class_iri))

    if not iris:
        is_clinical = any(kw in filename for kw in _AUTO_EXTRACT_KEYWORDS)
        if is_clinical:
            from app.models.system_config import SystemConfig

            cfg_row = db.get(SystemConfig, "default_extraction_targets")
            if cfg_row and isinstance(cfg_row.value, list):
                iris = cfg_row.value
        if not iris:
            for mod in engine.get_modules():
                for node in engine.get_class_hierarchy(mod.key):
                    _collect_iris_from_tree(node, iris)

    audit.append(
        db,
        "extraction.job.create",
        actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": source_type, "mode": "auto", "target_count": len(iris)},
    )

    background.add_task(_run_auto_pipeline_bg, job.id, iris, persistent_path, engine, db)
    if source_type in ("word", "excel"):
        background.add_task(_precompute_annotation_bg, job.id, engine, db)
    return job


def _collect_iris_from_tree(node, out: list[str]) -> None:
    out.append(node.iri)
    for child in node.children:
        _collect_iris_from_tree(child, out)


async def _run_auto_pipeline_bg(
    job_id, target_iris: list[str], file_path: Path, engine, db: Session
):
    """多类自动抽取：为每个目标类构造临时 config 调用现有 pipeline，候选汇入同一 Job。"""
    job = db.get(ExtractionJob, job_id)
    if job is None:
        return
    if job.source_type == "word":
        await _precompute_annotation_bg(job_id, engine, db)
        return
    try:
        for iri in target_iris:
            config = ExtractionConfig(
                name=f"auto-{iri.rsplit('#', 1)[-1].rsplit('/', 1)[-1]}",
                target_class_iri=iri,
                source_type=job.source_type,
            )
            db.add(config)
            db.flush()
            await run_extraction_pipeline(job, config, file_path, engine, db)
            db.delete(config)
            db.flush()
        # 多类汇入同一 Job：run_extraction_pipeline 内按单类覆盖 total_candidates，
        # 这里用真实落库候选数回填，使界面计数与库内一致（修正逐类覆盖的显示 bug）。
        job.total_candidates = (
            db.query(ExtractionCandidate).filter(ExtractionCandidate.job_id == job.id).count()
        )
        job.status = "reviewing"
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)[:500]
    db.commit()


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
    # Process-local events disappear on restart, while the SQL status may still
    # say annotating. Report an interruption instead of streaming nothing forever.
    fallback = None
    if not progress_bus.history(str(job_id)) and job.source_type in ("word", "excel"):
        from app.config import settings

        checkpoint = _load_annotation_checkpoint(job_id)
        terminal = {
            "annotating": "interrupted", "paused": "paused", "failed": "failed",
            "reviewing": "complete", "completed": "complete",
        }.get(job.status)
        if terminal:
            fallback = {
                "job_id": str(job_id), "stage": "annotating", "annotation_stage": terminal,
                "pct": 100 if terminal == "complete" else 0,
                "status": "done" if terminal == "complete" else terminal,
                "degraded": False, "has_checkpoint": checkpoint is not None,
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
):
    """请求暂停正在运行的标注任务（下一阶段间生效）。"""
    from app.services.extraction.progress import set_annotation_control

    set_annotation_control(str(job_id), "pause")
    return {"status": "pause_requested"}


@router.post("/jobs/{job_id}/annotation/resume")
async def resume_annotation(
    job_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """从上次暂停的检查点恢复标注。"""
    from app.services.extraction.progress import clear_annotation_control, progress_bus

    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    if job.status == "annotating" and progress_bus.annotation_is_running(str(job_id)):
        raise HTTPException(409, "该作业正在识别，请等待当前任务完成")
    checkpoint = _load_annotation_checkpoint(job_id)
    if checkpoint is None:
        raise HTTPException(409, "没有可恢复的标注断点")
    from app.config import settings

    if (
        job.source_type == "word"
        and checkpoint.get("attempt_count", 0) >= settings.evidence_max_tasks
    ):
        raise HTTPException(422, "本轮已达到处理上限，请检查未通过的任务和抽取配置")
    clear_annotation_control(str(job_id))
    progress_bus.reset(str(job_id))
    progress_bus.publish(str(job_id), {
        "job_id": str(job_id), "stage": "annotating", "annotation_stage": "queued",
        "pct": 0, "status": "running", "degraded": False,
    })
    job.status = "annotating"
    job.error_message = None
    db.commit()
    background.add_task(_precompute_annotation_bg, job_id, engine, db, checkpoint)
    return {"status": "resumed", "has_checkpoint": checkpoint is not None}


@router.post("/jobs/{job_id}/annotation/rerun")
async def rerun_annotation(
    job_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """丢弃缓存和检查点，重新运行三阶段标注。"""
    from app.services.extraction.progress import clear_annotation_control, progress_bus

    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    if job.source_type not in ("word", "excel"):
        raise HTTPException(422, "仅 Word/Excel 作业支持重新识别")
    if not job.document_path or not Path(job.document_path).is_file():
        raise HTTPException(422, "源文档不可用，无法重新识别")
    if job.status == "annotating" and progress_bus.annotation_is_running(str(job_id)):
        raise HTTPException(409, "该作业正在识别，请等待当前任务完成")
    _backfill_job_document_context(job, db)
    _clear_annotation_checkpoint(job_id)
    _annotation_cache_path(job_id).unlink(missing_ok=True)
    clear_annotation_control(str(job_id))
    progress_bus.reset(str(job_id))
    progress_bus.publish(str(job_id), {
        "job_id": str(job_id), "stage": "annotating", "annotation_stage": "queued",
        "pct": 0, "status": "running", "degraded": False,
    })
    job.status = "annotating"
    job.error_message = None
    db.commit()
    background.add_task(_precompute_annotation_bg, job_id, engine, db)
    return {"status": "restarted"}


_DOCUMENT_JOB_KEYS = (
    "job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob",
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


def _narratives_payload(report) -> dict | None:
    """Build the persisted narratives blob for the web reading pane (015).

    Only the LLM-generated prose is captured so the reading pane can render it
    under an AI indicator; deterministic values stay out. Returns ``None`` when
    there is no narrative content, keeping the column null for legacy reports.
    """
    subject = (
        report.subject_description if "subject_description" in report.llm_generated_fields else None
    )
    conclusion = report.conclusion if "conclusion" in report.llm_generated_fields else None
    sections = report.section_narratives or []
    # 016+: LLM-synthesized 语义化插槽 正文 (projection of Section.coverage + prompt).
    semantic_slots = report.semantic_slots or []
    if not subject and not conclusion and not sections and not semantic_slots:
        return None
    return {
        "subject_description": subject,
        "conclusion": conclusion,
        "sections": sections,
        "semantic_slots": semantic_slots,
    }


def _risk_report_source_document(job: ExtractionJob, db: Session) -> tuple[str, str]:
    """Resolve the source document's typed name and stable provenance reference."""
    source_config = job.source_config or {}
    document_ref = str(source_config.get("doc_ref") or "").strip()
    if document_ref:
        shadow = db.query(EntityShadow).filter(EntityShadow.iri == document_ref).one_or_none()
        if shadow is not None:
            document_name = str(
                (shadow.properties_json or {}).get("documentName") or shadow.label_zh or ""
            ).strip()
            if document_name:
                return document_name, document_ref
    return (
        str(job.source_filename or "").strip(),
        document_ref or f"urn:slpra:extraction-job:{job.id}",
    )


def _pde_conflict_decision_payload(db: Session, job_id: UUID) -> dict:
    """Snapshot the human PDE-conflict decision for report generation.

    No row means the conflict is still pending.  The returned object is JSON-safe
    so the async background task and the persisted report share the exact decision
    that was effective when generation started.
    """
    from app.models.pde_conflict import PdeConflictDecision
    from app.services.reasoning.pde_conflict import CONFLICT_KEY

    row = (
        db.query(PdeConflictDecision)
        .filter(
            PdeConflictDecision.job_id == job_id,
            PdeConflictDecision.conflict_key == CONFLICT_KEY,
        )
        .one_or_none()
    )
    if row is None:
        return {
            "conflict_key": CONFLICT_KEY,
            "chosen": "pending",
            "note": "",
            "actor": "",
            "version": 0,
            "decided_at": None,
        }
    return {
        "conflict_key": row.conflict_key,
        "chosen": row.chosen,
        "note": row.note,
        "actor": row.actor,
        "version": row.version,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


def _resolve_sample_docx_path(tpl_db_id, db) -> str | None:
    """Resolve the sample .docx path from the AstTemplate row for template-based output."""
    if tpl_db_id is None:
        return None
    from app.models.extraction import AstTemplate

    row = db.get(AstTemplate, tpl_db_id)
    if row and row.sample_docx_path:
        if Path(row.sample_docx_path).is_file():
            return row.sample_docx_path
        # DB 指向的模板文件已不在磁盘（历史删除/迁移遗留等）：显式告警，让「静默回退到硬编码
        # 默认格式」可观测，而非无声吞掉——否则文件生命周期问题会被掩盖成「输出没套模板」。
        logger.warning(
            "模板 %s 的 sample_docx_path 指向缺失文件 %s，报告将回退默认格式",
            tpl_db_id,
            row.sample_docx_path,
        )
    return None


def _build_and_save_report(
    job_id: UUID,
    report_id: UUID,
    **_legacy_arguments,
) -> None:
    """Render the already frozen manifest in an independent worker session."""
    from app.config import settings
    from app.db import SessionLocal
    from app.models.evidence import EvidenceCoverage
    from app.services.reporting.snapshot_report import render_snapshot_report

    db = SessionLocal()
    try:
        record = db.get(GeneratedReport, report_id)
        if record is None or record.job_id != job_id:
            raise ValueError("report/job mismatch")
        frozen = db.get(EvidenceCoverage, record.coverage_manifest_id)
        if frozen is None or frozen.job_id != job_id:
            raise ValueError("frozen published report inputs are required")
        record.report_status = "running"
        record.rules_summary = {
            "_progress": {
                "stage": "render",
                "percent": 45,
                "detail": "正在读取已冻结事实快照并排版",
            }
        }
        db.commit()
        report, manifest, data = render_snapshot_report(frozen.payload)
        output = Path(settings.report_output_dir)
        output.mkdir(parents=True, exist_ok=True)
        file_path = output / f"{report_id}.docx"
        file_path.write_bytes(data)
        record.file_path = str(file_path)
        record.file_size = len(data)
        record.report_status = "completed"
        record.report_error = None
        record.rules_summary = {
            "_progress": {
                "stage": "completed",
                "percent": 100,
                "detail": "快照报告已生成，缺口已明确标注",
            },
            "coverage": manifest.to_dict(),
            "template_id": frozen.payload["template_id"],
            "template_version": frozen.payload["template_version"],
            "fact_snapshot_id": record.evidence_snapshot_id,
        }
        record.narratives = _narratives_payload(report)
        audit.append(
            db,
            "report.generate",
            actor=record.actor,
            entity_iri=str(job_id),
            details={
                "report_id": str(report_id),
                "snapshot_id": record.evidence_snapshot_id,
                "manifest_id": frozen.id,
                "template_version": frozen.payload["template_version"],
            },
            commit=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        record = db.get(GeneratedReport, report_id)
        if record:
            record.report_status = "failed"
            record.report_error = str(exc)[:500]
            db.commit()
        logging.getLogger(__name__).warning("快照报告生成失败: %s", exc, exc_info=True)
    finally:
        db.close()


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
    """Freeze inputs now; the worker never reads a mutable annotation preview."""
    from uuid import uuid4

    from app.services.extraction.extraction_tasks import semantic_schema_from_engine
    from app.services.reporting.snapshot_report import build_report_inputs, freeze_report_inputs

    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    try:
        inputs = build_report_inputs(
            db,
            job,
            schema=semantic_schema_from_engine(engine),
            template_id=template_id,
            snapshot_id=snapshot_id,
        )
        frozen = freeze_report_inputs(db, job_id, inputs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    record = GeneratedReport(
        id=uuid4(),
        job_id=job_id,
        file_path="",
        actor=identity.username,
        report_type="risk_assessment",
        report_status="pending",
        evidence_snapshot_id=inputs["snapshot_id"],
        coverage_manifest_id=frozen.id,
        selector_version=inputs["selector_version"],
        source_discovery_hash=inputs["discovery_revision"],
    )
    db.add(record)
    audit.append(
        db,
        "report.freeze",
        actor=identity.username,
        entity_iri=str(job_id),
        details={
            "report_id": str(record.id),
            "manifest_id": frozen.id,
            "snapshot_id": inputs["snapshot_id"],
        },
        commit=False,
    )
    db.commit()
    background_tasks.add_task(_build_and_save_report, job_id=job_id, report_id=record.id)
    return {
        "report_id": str(record.id),
        "status": "pending",
        "snapshot_id": inputs["snapshot_id"],
        "manifest_id": frozen.id,
        "template_id": inputs["template_id"],
        "template_version": inputs["template_version"],
    }


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
            GeneratedReport.deleted_at.is_(None),
            GeneratedReport.report_status == "completed",
        )
        .order_by(GeneratedReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(404, "该作业尚未生成风险评估报告")

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
):
    """Poll the status of an async-generated report (013)."""
    report = db.get(GeneratedReport, report_id)
    if not report or report.job_id != job_id or report.deleted_at is not None:
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
    }


@router.get("/jobs/{job_id}/reports/{report_id}/download")
def download_report_by_id(
    job_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
):
    """Download a completed report by its ID (013)."""
    from fastapi.responses import FileResponse

    report = db.get(GeneratedReport, report_id)
    if not report or report.job_id != job_id or report.deleted_at is not None:
        raise HTTPException(404, "报告不存在")
    if report.report_status != "completed":
        raise HTTPException(409, f"报告尚未完成（status={report.report_status}）")

    file_path = Path(report.file_path)
    if not file_path.exists():
        raise HTTPException(404, "报告文件不存在")

    job = db.get(ExtractionJob, job_id)
    src_name = (job.source_filename or "report").replace(".docx", "") if job else "report"
    download_name = f"风险评估表_{src_name}.docx"
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


# --------------------------------------------------------------------------- #
# 011 AST Coverage
# --------------------------------------------------------------------------- #


def _build_ast_coverage_response(
    job_id: UUID,
    db: Session,
    template_id: UUID | None = None,
    engine=None,
) -> ASTCoverageResponse:
    """The legacy UI now presents the same instance manifest used by reports."""
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine
    from app.services.reporting.snapshot_report import build_report_inputs, presentation_manifest

    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(404, "作业不存在")
    try:
        if engine is None:
            from app.services.ontology_engine import get_loaded_engine

            engine = get_loaded_engine()
        inputs = build_report_inputs(
            db,
            job,
            schema=semantic_schema_from_engine(engine) if engine else {},
            template_id=template_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    manifest = presentation_manifest(inputs)
    sections = []
    for section in inputs["template_schema"]["sections"]:
        section_tasks = [t for t in inputs["tasks"] if t["section_id"] == section["section_id"]]
        slots = [
            {
                "slot_id": task.get("coverage_task_id", task["target_id"]),
                "label": task["label"],
                "status": task["status"],
                "source_kind": "published_snapshot",
                "value": "; ".join(obj["text"] for obj in task.get("objects", [])) or None,
                "source_ref": ", ".join(
                    task.get("assertion_ids", []) + task.get("negative_assertion_ids", [])
                )
                or None,
                "note": task["reason"] + " · " + (task.get("subject_instance_iri") or "主体未发布"),
            }
            for task in section_tasks
        ]
        sections.append(
            {
                "section_id": section["section_id"],
                "title": section["title"],
                "groups": [
                    {
                        "group_id": section["section_id"] + ".instances",
                        "title": "实例覆盖（已发布快照）",
                        "kind": "fields",
                        "slots": slots,
                    }
                ],
            }
        )
    return ASTCoverageResponse(
        **{k: v for k, v in manifest.summary().items() if k != "missing_slot_ids"},
        template_name=inputs["template_name"],
        template_version=inputs["template_version"],
        sections=sections,
        snapshot_id=inputs["snapshot_id"],
        manifest_id=inputs["manifest_id"],
        instance_manifest=manifest.instance_manifest,
    )


@router.get("/jobs/{job_id}/ast-coverage", response_model=ASTCoverageResponse)
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
    return _build_ast_coverage_response(job_id, db, template_id=template_id, engine=engine)


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
            GeneratedReport.deleted_at.is_(None),
            GeneratedReport.report_status == "completed",
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
    """Mark a slot as not applicable (011 FR-API-004)."""
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")

    existing = (
        db.query(SlotDismissal)
        .filter(SlotDismissal.job_id == job_id, SlotDismissal.slot_id == body.slot_id)
        .first()
    )
    if existing:
        raise HTTPException(409, "该槽位已标记为不适用")

    dismissal = SlotDismissal(
        job_id=job_id,
        slot_id=body.slot_id,
        dismissed_by=identity.username,
    )
    db.add(dismissal)

    audit.append(
        db,
        "slot.dismiss",
        actor=identity.username,
        entity_iri=str(job_id),
        details={"slot_id": body.slot_id, "job_id": str(job_id)},
        commit=False,
    )
    db.commit()

    return _build_ast_coverage_response(job_id, db)


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
    """Undo a slot dismissal (011 FR-API-005)."""
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")

    dismissal = (
        db.query(SlotDismissal)
        .filter(SlotDismissal.job_id == job_id, SlotDismissal.slot_id == slot_id)
        .first()
    )
    if not dismissal:
        raise HTTPException(404, "该槽位未被标记为不适用")

    db.delete(dismissal)

    audit.append(
        db,
        "slot.undismiss",
        actor=identity.username,
        entity_iri=str(job_id),
        details={"slot_id": slot_id, "job_id": str(job_id)},
        commit=False,
    )
    db.commit()

    return _build_ast_coverage_response(job_id, db)
