"""Document extraction API routes (能力二：多源抽取 + 跨源对齐 + 人工审核闭环)."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
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
from app.schemas.extraction import (
    ASTCoverageResponse,
    CandidateGroup,
    DocExtractionRequest,
    ExtractionCandidateResponse,
    ExtractionConfigCreate,
    ExtractionConfigResponse,
    ExtractionJobResponse,
    GeneratedReportResponse,
    GroupCoverageResponse,
    GroupedCandidatesResponse,
    MergeRequest,
    ReviewRequest,
    SectionCoverageResponse,
    SlotCoverageResponse,
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
    """后台运行流水线。复用请求会话（FastAPI 在后台任务后才做依赖清理）。"""
    job = db.get(ExtractionJob, job_id)
    config = db.get(ExtractionConfig, config_id)
    if job is None or config is None:
        return
    await run_extraction_pipeline(job, config, file_path, engine, db)


@router.post("/jobs", response_model=ExtractionJobResponse, status_code=202)
async def create_job(
    background: BackgroundTasks,
    source_type: str = Form(...),
    config_id: UUID = Form(...),
    file: UploadFile | None = File(None),
    db_source: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """创建抽取作业并**真实触发**流水线（状态置 running，非 pending, FR-001/002）。"""
    config = db.get(ExtractionConfig, config_id)
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
        db, "extraction.job.create", actor=identity.username,
        entity_iri=str(job.id),
        details={"source_type": source_type, "config_id": str(config_id)},
    )

    background.add_task(_run_pipeline_bg, job.id, config_id, file_path, engine, db)
    if job.document_path and source_type in ("word", "excel"):
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
        source_config={"doc_ref": req.doc_ref, "content_ref": req.content_ref,
                       "config_id": str(req.config_id)},
        status="pending",  # Q1：入队不自动发起
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    audit.append(
        db, "extraction.job.enqueue", actor=identity.username, entity_iri=str(job.id),
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
        db, "extraction.job.start", actor=identity.username, entity_iri=str(job.id),
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


_ANNOTATOR_VERSION = 14


def _annotation_cache_path(job_id) -> Path:
    """标注预计算缓存路径：``data/uploads/{job_id}.annotated.json``。"""
    return Path("data/uploads") / f"{job_id}.annotated.json"


def _compute_annotation(
    job: ExtractionJob,
    engine: OntologyEngine,
    progress_fn=None,
    should_pause_fn=None,
    checkpoint=None,
) -> dict:
    """三阶段标注 → drawer 响应负载。阻塞 CPU，须经线程。

    Word 文档走「分类前置」管线：先分类 → 缩窄候选类 → NER → 关系抽取，
    Stage-2 嵌入归类仅在文档类型子图（~50 类 vs 320 全量）内匹配，精度显著提升。
    """
    from app.services.extraction.document_annotator import annotate_excel, annotate_word

    file_path = Path(job.document_path)

    # ── Word：分类前置，缩窄 NER 候选集 ──
    doc_class_iri = None
    doc_class_result = None
    if job.source_type == "word":
        try:
            from app.services.extraction.docx_structure import parse_docx_structure
            from app.services.extraction.document_classifier import classify

            structure = parse_docx_structure(file_path)
            doc_class_result = classify(structure, engine)
            doc_class_iri = (
                doc_class_result["doc_class_iri"] if doc_class_result else None
            )
        except Exception:
            logger.warning("文档分类失败，使用全量候选集", exc_info=True)

    if job.source_type == "word":
        content, warnings, triples, ckpt = annotate_word(
            file_path, engine, progress_fn, should_pause_fn, checkpoint,
            doc_class_iri=doc_class_iri,
        )
    elif job.source_type == "excel":
        content, warnings, triples, ckpt = annotate_excel(
            file_path, engine, progress_fn=progress_fn,
            should_pause_fn=should_pause_fn, checkpoint=checkpoint,
        )
    else:
        raise ValueError(f"不支持的源类型标注：{job.source_type}")
    result = {
        "_version": _ANNOTATOR_VERSION,
        "source_type": job.source_type,
        "filename": job.source_filename,
        "content": content,
        "warnings": warnings,
        "triples": triples,
        "doc_class": None,
        "relationships": [],
    }
    # 文档级分类 + 全量关系/属性抽取（纯规则、离线、无新增模型调用）。仅 Word；
    # 仅在完整标注完成（无 checkpoint，未暂停）时计算，避免对部分结果连边。
    if job.source_type == "word" and ckpt is None:
        try:
            from app.services.extraction.relation_extractor import extract_relationships

            graph = extract_relationships(
                engine, file_path, triples, doc_class=doc_class_result,
            )
            result["doc_class"] = graph["doc_class"]
            result["relationships"] = graph["relationships"]
        except Exception:
            logger.warning("关系抽取失败，本次跳过（不影响标注主路径）", exc_info=True)
    if ckpt is not None:
        result["_checkpoint"] = ckpt
    return result


def _write_annotation_cache(job_id, payload: dict) -> None:
    cache_path = _annotation_cache_path(job_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@router.get("/jobs/{job_id}/annotated-document")
async def get_annotated_document(
    job_id: UUID,
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
):
    """三阶段 NER 标注后的源文档 → 前端可渲染结构 + 属性三元组（drawer「查看标注」）。

    优先读抽取时预计算的缓存（即时返回，源文档已清理也能用）；缓存缺失则按需
    实时计算并回填缓存。响应含 ``triples``（实体属性三元组）和 ``warnings``。
    """
    import asyncio

    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404)

    cache_path = _annotation_cache_path(job_id)
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("_version") == _ANNOTATOR_VERSION:
                return cached
            logger.info("标注缓存版本过期，重新计算：%s", cache_path)
        except Exception:
            logger.warning("标注缓存损坏，回退实时计算：%s", cache_path, exc_info=True)

    if not job.document_path or not Path(job.document_path).is_file():
        raise HTTPException(404, "源文档不可用（已清理或未持久化）")
    if job.source_type not in ("word", "excel"):
        raise HTTPException(400, f"不支持的源类型标注：{job.source_type}")

    payload = await asyncio.to_thread(_compute_annotation, job, engine)
    await asyncio.to_thread(_write_annotation_cache, job_id, payload)
    return payload


def _persist_ner_triples(job_id, triples: list[dict], db: Session) -> int:
    """NER 三元组 → ExtractionCandidate（入复核队列，candidate_kind='ner_triple'）。"""
    added = 0
    for triple in triples:
        if not triple.get("properties"):
            continue
        props = {p["iri"]: p["value"] for p in triple["properties"]}
        db.add(ExtractionCandidate(
            job_id=job_id,
            target_class_iri=triple["entity_class_iri"],
            extracted_properties=props,
            candidate_kind="ner_triple",
            group_key=f"ner:{triple['entity_text']}",
            source_ref=(
                f"ner#seg{triple['segment_index']}"
                f":{triple['span_start']}-{triple['span_end']}"
            ),
            alignment_result="new",
            review_status="pending",
        ))
        added += 1
    if added:
        db.commit()
    return added


def _annotation_checkpoint_path(job_id) -> Path:
    return Path("data/uploads") / f"{job_id}.annotation_checkpoint.json"


def _write_annotation_checkpoint(job_id, ckpt: dict) -> None:
    path = _annotation_checkpoint_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ckpt, ensure_ascii=False), encoding="utf-8")


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


_ANNOTATION_STAGE_PCT = {"gliner": 10, "typing": 30, "triples": 50, "done": 60}


async def _precompute_annotation_bg(
    job_id, engine: OntologyEngine, db: Session, checkpoint=None,
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

    def on_progress(sub_stage: str):
        progress_bus.publish(job_id_str, {
            "job_id": job_id_str,
            "stage": "annotating",
            "annotation_stage": sub_stage,
            "pct": _ANNOTATION_STAGE_PCT.get(sub_stage, 0),
            "status": "running",
            "degraded": False,
        })

    def should_pause() -> bool:
        return get_annotation_control(job_id_str) == "pause"

    try:
        payload = await asyncio.to_thread(
            _compute_annotation, job, engine, on_progress, should_pause, checkpoint,
        )

        ckpt = payload.pop("_checkpoint", None)
        if ckpt is not None:
            await asyncio.to_thread(_write_annotation_checkpoint, job_id, ckpt)
            progress_bus.publish(job_id_str, {
                "job_id": job_id_str,
                "stage": "annotating",
                "annotation_stage": "paused",
                "pct": _ANNOTATION_STAGE_PCT.get(ckpt.get("completed_stage", ""), 0),
                "status": "paused",
                "degraded": False,
            })
            return

        clear_annotation_control(job_id_str)
        _clear_annotation_checkpoint(job_id)

        for warn in payload.get("warnings") or []:
            logger.warning("标注告警 job=%s: %s", job_id, warn)
        await asyncio.to_thread(_write_annotation_cache, job_id, payload)
        triples = payload.get("triples") or []
        if triples:
            await asyncio.to_thread(_persist_ner_triples, job_id, triples, db)

        progress_bus.publish(job_id_str, {
            "job_id": job_id_str,
            "stage": "done",
            "annotation_stage": "complete",
            "pct": 100,
            "status": "done",
            "degraded": False,
        })
    except Exception:
        logger.warning("标注预计算失败 job=%s", job_id, exc_info=True)
        progress_bus.publish(job_id_str, {
            "job_id": job_id_str,
            "stage": "annotating",
            "annotation_stage": "failed",
            "pct": 0,
            "status": "failed",
            "degraded": False,
        })


_AUTO_EXTRACT_KEYWORDS = ["临床备样", "生产信息", "备样生产"]


@router.post("/jobs/auto", response_model=ExtractionJobResponse, status_code=202)
async def create_auto_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    source_type: str = Form(...),
    target_class_iris: str | None = Form(None),
    db: Session = Depends(get_db),
    engine: OntologyEngine = Depends(get_ontology_engine),
    identity: Identity = Depends(_analyst),
):
    """自动抽取：上传文件 → 按文件名关键词或指定目标类列表 → 多类抽取汇入同一 Job。"""
    suffix = Path(file.filename or "").suffix or ".bin"
    filename = file.filename or "unknown"

    job = ExtractionJob(
        source_type=source_type,
        source_filename=filename,
        source_config={"mode": "auto"},
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

    iris: list[str] = []
    if target_class_iris:
        iris = json.loads(target_class_iris)

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
        db, "extraction.job.create", actor=identity.username,
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
            db.query(ExtractionCandidate)
            .filter(ExtractionCandidate.job_id == job.id)
            .count()
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
):
    """SSE：逐阶段推送 parsing→extracting→aligning→reviewing（FR-002）。"""

    async def event_gen():
        async for ev in progress_bus.stream(str(job_id)):
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
    from app.services.extraction.progress import clear_annotation_control

    checkpoint = _load_annotation_checkpoint(job_id)
    clear_annotation_control(str(job_id))
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
    from app.services.extraction.progress import clear_annotation_control

    _clear_annotation_checkpoint(job_id)
    _annotation_cache_path(job_id).unlink(missing_ok=True)
    clear_annotation_control(str(job_id))
    background.add_task(_precompute_annotation_bg, job_id, engine, db)
    return {"status": "restarted"}


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
        group_models.append(CandidateGroup(
            group_key=gkey,
            canonical_candidate_id=canonical.id if canonical else None,
            candidates=[ExtractionCandidateResponse.model_validate(m) for m in members],
        ))

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
    """确认入库：落 committed_iri 并尽力投影到 KG/影子表（VR-2）。

    doc_repo 来源候选（`source_ref` 为 facts# 文档 IRI）额外注入溯源回链 `extractedFrom`
    并缺省继承文档阶段（007 US2，FR-004/SC-002，data-model §4）；非文档候选行为不变（零回归）。
    """
    iri = candidate.aligned_iri or f"{candidate.target_class_iri}_{candidate.id.hex[:8]}"
    candidate.committed_iri = iri
    candidate.review_status = "committed"

    src = candidate.source_ref or ""
    if src.startswith(_FACTS_NS):  # 仅 doc_repo 来源候选注入回链
        props = dict(candidate.extracted_properties or {})
        props["extractedFrom"] = src                       # C4.1：回链文档个体
        phase = _document_phase(src, db)
        if phase:
            props.setdefault("hasDevelopmentPhase", phase)  # C4.3：缺省继承、冲突不覆盖
        candidate.extracted_properties = props

    try:  # best-effort World projection; fake engine 下为 no-op
        engine.project_entities([candidate.extracted_properties])
    except Exception:  # pragma: no cover
        pass
    return iri


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
        db, action, actor=identity.username, entity_iri=str(candidate_id),
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
        db, "extraction.candidate.merge", actor=identity.username,
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
        db, "extraction.candidate.split", actor=identity.username,
        entity_iri=str(candidate_id), details={"derived": len(derived)},
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
    return (
        settings.llm_report_merge_values
        or settings.llm_report_narrative_enabled
    )


def _build_and_save_report(
    job_id: UUID,
    job_source_filename: str,
    document_path: str | None,
    dismissed_ids: set[str] | None,
    actor: str,
    report_id: UUID,
) -> None:
    """Shared report build logic used by both sync and async paths (013)."""
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from app.db import SessionLocal
    from app.services.reporting.docx_renderer import render_risk_report
    from app.services.reporting.risk_report_generator import RiskReportGenerator

    db = SessionLocal()
    try:
        gen_report = db.get(GeneratedReport, report_id)
        if gen_report:
            gen_report.report_status = "running"
            db.commit()

        cache_path = _annotation_cache_path(job_id)
        result = _json.loads(cache_path.read_text(encoding="utf-8"))
        edges = result.get("relationships", [])

        generator = RiskReportGenerator(db)
        report, manifest = generator.generate_with_coverage(
            edges,
            source_filename=job_source_filename,
            dismissed_slot_ids=dismissed_ids,
            document_path=document_path,
        )
        docx_bytes = render_risk_report(report, manifest)

        reports_dir = _Path("data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        file_name = f"{job_id}_{ts}.docx"
        file_path = reports_dir / file_name
        file_path.write_bytes(docx_bytes)

        if gen_report:
            gen_report.file_path = str(file_path)
            gen_report.file_size = len(docx_bytes)
            gen_report.rules_fired_count = generator.rules_fired_count
            gen_report.rules_summary = {
                "rows": [
                    {"hazid": r.hazid, "pre": r.pre_control_level, "post": r.post_control_level}
                    for r in report.assessment_rows
                ],
                "coverage": manifest.to_dict(),
            }
            gen_report.report_status = "completed"
            gen_report.report_error = None
            audit.append(
                db, "report.generate", actor=actor,
                entity_iri=str(job_id),
                details={
                    "report_id": str(report_id),
                    "rules_fired_count": generator.rules_fired_count,
                    "report_type": "risk_assessment",
                    "coverage": manifest.summary(),
                },
                commit=False,
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        gen_report = db.get(GeneratedReport, report_id)
        if gen_report:
            gen_report.report_status = "failed"
            gen_report.report_error = str(exc)[:500]
            db.commit()
        logging.getLogger(__name__).warning("报告生成失败: %s", exc, exc_info=True)
    finally:
        db.close()


@router.post("/jobs/{job_id}/risk-report")
def generate_risk_report(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """Generate and persist a risk assessment report for the extraction job.

    When LLM enhancement flags are on (013), creates a pending report row and
    runs generation in the background (async path).  Otherwise uses the original
    synchronous path for backward compatibility (SC-005).
    """
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path as _Path
    from uuid import uuid4

    from fastapi.responses import FileResponse

    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")

    cache_path = _annotation_cache_path(job_id)
    if not cache_path.exists():
        raise HTTPException(422, "文档未分类，无法生成风险评估报告")

    result = _json.loads(cache_path.read_text(encoding="utf-8"))
    doc_class = result.get("doc_class")
    if not doc_class:
        raise HTTPException(422, "文档未分类，无法生成风险评估报告")
    if "CMCReport" not in doc_class.get("doc_class_iri", ""):
        raise HTTPException(422, "仅支持 CMCReport 类型文档生成风险评估报告")

    edges = result.get("relationships", [])
    if not edges:
        raise HTTPException(422, "未检测到关系数据，无法生成风险评估报告")

    dismissed_rows = (
        db.query(SlotDismissal.slot_id)
        .filter(SlotDismissal.job_id == job_id)
        .all()
    )
    dismissed_ids = {r.slot_id for r in dismissed_rows} or None
    report_id = uuid4()

    if _llm_report_flags_active():
        gen_report = GeneratedReport(
            id=report_id,
            job_id=job_id,
            report_type="risk_assessment",
            file_path="",
            actor=identity.username,
            report_status="pending",
        )
        db.add(gen_report)
        db.commit()

        background_tasks.add_task(
            _build_and_save_report,
            job_id=job_id,
            job_source_filename=job.source_filename or "",
            document_path=job.document_path,
            dismissed_ids=dismissed_ids,
            actor=identity.username,
            report_id=report_id,
        )
        return {"report_id": str(report_id), "status": "pending"}

    from app.services.reporting.docx_renderer import render_risk_report
    from app.services.reporting.risk_report_generator import RiskReportGenerator

    generator = RiskReportGenerator(db)
    report, manifest = generator.generate_with_coverage(
        edges, source_filename=job.source_filename or "",
        dismissed_slot_ids=dismissed_ids,
    )
    docx_bytes = render_risk_report(report, manifest)

    reports_dir = _Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    file_name = f"{job_id}_{ts}.docx"
    file_path = reports_dir / file_name
    file_path.write_bytes(docx_bytes)

    gen_report = GeneratedReport(
        id=report_id,
        job_id=job_id,
        report_type="risk_assessment",
        file_path=str(file_path),
        file_size=len(docx_bytes),
        rules_fired_count=generator.rules_fired_count,
        rules_summary={
            "rows": [
                {"hazid": r.hazid, "pre": r.pre_control_level, "post": r.post_control_level}
                for r in report.assessment_rows
            ],
            "coverage": manifest.to_dict(),
        },
        actor=identity.username,
        report_status="completed",
    )
    db.add(gen_report)

    audit.append(
        db, "report.generate", actor=identity.username,
        entity_iri=str(job_id),
        details={
            "report_id": str(report_id),
            "rules_fired_count": generator.rules_fired_count,
            "report_type": "risk_assessment",
            "coverage": manifest.summary(),
        },
        commit=False,
    )
    db.commit()

    src_name = (job.source_filename or "report").replace(".docx", "")
    download_name = f"风险评估表_{src_name}.docx"
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


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
        .filter(GeneratedReport.job_id == job_id)
        .order_by(GeneratedReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(404, "该作业尚未生成风险评估报告")

    file_path = Path(report.file_path)
    if not file_path.exists():
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
    if not report or report.job_id != job_id:
        raise HTTPException(404, "报告不存在")
    return {
        "id": str(report.id),
        "job_id": str(report.job_id),
        "report_type": report.report_type,
        "file_path": report.file_path,
        "file_size": report.file_size,
        "rules_fired_count": report.rules_fired_count,
        "actor": report.actor,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "report_status": report.report_status,
        "report_error": report.report_error,
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
    if not report or report.job_id != job_id:
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
) -> ASTCoverageResponse:
    """Shared helper: run coverage validation and build the tree response.

    012 extension: accepts optional *template_id* to override default template
    resolution. When omitted, resolves via ``resolve_template()`` (three-tier
    fallback: mapping → default → filesystem).
    """
    from app.models.extraction import AstTemplate
    from app.models.ontology_meta import OntologyDecisionRule
    from app.services.reasoning.fact_bridge import edges_to_facts
    from app.services.reporting.ast_template import ReportTemplate, resolve_template
    from app.services.reporting.coverage_validator import validate_coverage

    cache_path = _annotation_cache_path(job_id)
    if not cache_path.exists():
        raise HTTPException(422, "文档未分类，无法生成覆盖预览")

    result = json.loads(cache_path.read_text(encoding="utf-8"))
    doc_class = result.get("doc_class")
    if not doc_class:
        raise HTTPException(422, "文档未分类，无法生成覆盖预览")

    doc_class_iri = doc_class.get("doc_class_iri", "")

    # Template resolution (012): explicit ID → DB lookup; else three-tier fallback
    tpl_name = ""
    tpl_version = ""
    if template_id is not None:
        row = db.get(AstTemplate, template_id)
        if not row:
            raise HTTPException(404, "模板不存在")
        template = ReportTemplate.model_validate(row.schema_json)
        tpl_name = row.name
        tpl_version = row.version
    else:
        template, _match_source, _db_id = resolve_template(doc_class_iri, db)
        if _db_id is not None:
            row = db.get(AstTemplate, _db_id)
            if row:
                tpl_name = row.name
                tpl_version = row.version

    # 012 T031: Ontology-driven dynamic slot expansion
    try:
        from app.config import settings as _settings
        if _settings.local_llm_enabled and doc_class_iri:
            from app.services.ontology_engine import ontology_engine
            from app.services.reporting.template_expander import expand_template_with_ontology
            if ontology_engine.is_loaded:
                template = expand_template_with_ontology(template, doc_class_iri, ontology_engine)
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("本体扩展异常，跳过", exc_info=True)

    edges = result.get("relationships", [])

    dismissed_rows = (
        db.query(SlotDismissal.slot_id)
        .filter(SlotDismissal.job_id == job_id)
        .all()
    )
    dismissed_ids = {r.slot_id for r in dismissed_rows}

    facts = edges_to_facts(list(edges))

    rules = (
        db.query(OntologyDecisionRule)
        .filter(
            OntologyDecisionRule.rule_group == "risk_assessment",
            OntologyDecisionRule.is_disabled == False,  # noqa: E712
        )
        .order_by(OntologyDecisionRule.priority)
        .all()
    )

    manifest = validate_coverage(
        template, edges, rules, facts,
        dismissed_slot_ids=dismissed_ids if dismissed_ids else None,
    )

    # 012 LLM gap filling: attempt to fill missing_required slots via local LLM
    if manifest.missing_required > 0:
        try:
            from app.config import settings

            if settings.local_llm_enabled:
                from app.services.extraction.llm_gap_filler import fill_coverage_gaps

                job = db.get(ExtractionJob, job_id)
                doc_path = job.document_path if job else None
                llm_fills = fill_coverage_gaps(manifest, doc_path, template)
                if llm_fills:
                    fills_by_id = {f["slot_id"]: f for f in llm_fills}
                    for sc in manifest.slots:
                        base_id = sc.slot_id.split("[")[0]
                        fill = fills_by_id.get(sc.slot_id) or fills_by_id.get(base_id)
                        if fill and sc.status == "missing_required":
                            sc.status = "filled"
                            sc.value = fill["value"]
                            sc.source_span = fill.get("source_span")
                            sc.is_llm_sourced = True
                            sc.source_kind = "llm_extraction"
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "LLM 补抽集成异常，跳过", exc_info=True,
            )

    slot_map: dict[str, list] = {}
    for sc in manifest.slots:
        base_id = sc.slot_id.split("[")[0]
        slot_map.setdefault(base_id, []).append(sc)

    sections: list[SectionCoverageResponse] = []
    for section in template.sections:
        groups: list[GroupCoverageResponse] = []
        for group in section.groups:
            slots_out: list[SlotCoverageResponse] = []
            for slot in group.slots:
                matched = slot_map.get(slot.slot_id, [])
                for sc in matched:
                    slots_out.append(SlotCoverageResponse(
                        slot_id=sc.slot_id,
                        label=sc.label,
                        status=sc.status,
                        source_kind=sc.source_kind,
                        value=sc.value,
                        source_ref=sc.source_ref,
                        rule_key=sc.rule_key,
                        hazid=sc.hazid,
                        note=sc.note,
                        source_span=sc.source_span,
                        is_llm_sourced=sc.is_llm_sourced,
                    ))
                if not matched:
                    slots_out.append(SlotCoverageResponse(
                        slot_id=slot.slot_id,
                        label=slot.label,
                        status="blank_optional",
                        source_kind=slot.source.kind,
                    ))
            groups.append(GroupCoverageResponse(
                group_id=group.group_id,
                title=group.title,
                kind=group.kind,
                slots=slots_out,
                is_dynamic=group.group_id.startswith("ontology_"),
            ))
        sections.append(SectionCoverageResponse(
            section_id=section.section_id,
            title=section.title,
            groups=groups,
        ))

    return ASTCoverageResponse(
        template_id=manifest.template_id,
        template_name=tpl_name,
        template_version=tpl_version,
        total_slots=manifest.total_slots,
        filled=manifest.filled,
        inferred=manifest.inferred,
        missing_required=manifest.missing_required,
        blank_optional=manifest.blank_optional,
        manual=manifest.manual,
        dismissed=manifest.dismissed,
        sections=sections,
    )


@router.get("/jobs/{job_id}/ast-coverage", response_model=ASTCoverageResponse)
def get_ast_coverage(
    job_id: UUID,
    template_id: UUID | None = None,
    db: Session = Depends(get_db),
    identity: Identity = Depends(get_current_user),
):
    """Run coverage validation and return the AST tree with slot status (011 FR-API-001).

    012 extension: optional *template_id* query param overrides default
    template resolution.
    """
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    return _build_ast_coverage_response(job_id, db, template_id=template_id)


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
        .filter(GeneratedReport.job_id == job_id)
        .order_by(GeneratedReport.created_at.desc())
        .all()
    )


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
        db, "slot.dismiss", actor=identity.username,
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
        db, "slot.undismiss", actor=identity.username,
        entity_iri=str(job_id),
        details={"slot_id": slot_id, "job_id": str(job_id)},
        commit=False,
    )
    db.commit()

    return _build_ast_coverage_response(job_id, db)
