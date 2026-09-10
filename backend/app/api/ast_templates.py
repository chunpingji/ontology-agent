"""AST 模板管理 API（012-ast-template-llm-pipeline, contracts/ast-templates-api）。"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import (
    ROLE_SENIOR_ANALYST,
    get_current_user,
    get_ontology_engine,
    require_role,
)
from app.models.extraction import AstTemplate, AstTemplateTrainingPair, ExtractionJob
from app.schemas.extraction import (
    AstTemplateCreate,
    AstTemplateMetaUpdate,
    AstTemplateResponse,
    AstTemplateUpdate,
    CoverageDocClassesRequest,
    CoverageDocClassesResponse,
    GenerateSectionPromptRequest,
    GenerateSectionPromptResponse,
    PreviewSectionNarrativeRequest,
    PreviewSectionNarrativeResponse,
    SuggestSlotsRequest,
    TemplateMatchResponse,
    TrainingPairResponse,
)
from app.services import audit
from app.services.reporting.ast_template import ReportTemplate
from app.services.reporting.report_run_service import schema_hash
from app.services.reporting.template_v2 import ReportingError, TemplateV2

router = APIRouter()

_log = logging.getLogger(__name__)

_maintainer = require_role(ROLE_SENIOR_ANALYST)

# 015 基本信息上传落盘：复用抽取管线的 data/uploads 约定（extraction.py），仅存路径。
_UPLOADS = Path("data/uploads")


async def _save_upload(file: UploadFile, stem: str) -> str:
    """把上传文件写入 data/uploads/<stem><suffix>，返回持久化路径字符串。"""
    _UPLOADS.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix or ".bin"
    dest = _UPLOADS / f"{stem}{suffix}"
    dest.write_bytes(await file.read())
    return str(dest)


def _best_effort_unlink(path: str | None) -> None:
    """删除文件并吞掉/记录任何异常。用于「主操作已成功或已失败、清理绝不能再抛」的场景：
    清理失败（权限/IO/文件系统）不得掩盖原始异常，也不得把已提交成功的操作变成 500。"""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # pragma: no cover - 防御性：清理失败仅记录，不影响主流程
        _log.warning("清理文件失败（已忽略）：%s", path, exc_info=True)


def _count_slots(schema_json: dict) -> int:
    if schema_json.get("schema_version") == 2:
        from app.services.reporting.template_compiler import walk_groups

        template = TemplateV2.model_validate(schema_json)
        return sum(
            len(group.units)
            for section in template.sections
            for group, _ in walk_groups(section.groups)
        )
    count = 0
    for sec in schema_json.get("sections", []):
        for grp in sec.get("groups", []):
            count += len(grp.get("slots", []))
    return count


def _template_response(t: AstTemplate) -> AstTemplateResponse:
    return AstTemplateResponse(
        id=t.id,
        name=t.name,
        version=t.version,
        doc_no=t.doc_no,
        iri_pattern=t.iri_pattern,
        status=t.status,
        slot_count=_count_slots(t.schema_json),
        is_default=t.is_default,
        created_by=t.created_by,
        owner=t.owner,
        sample_docx_filename=Path(t.sample_docx_path).name if t.sample_docx_path else None,
        default_source_filename=t.default_source_filename,
        default_source_job_id=t.default_source_job_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
        schema_version=t.schema_json.get("schema_version", 1),
        template_family_id=t.template_family_id or str(t.id),
        revision_no=t.revision_no or 1,
        schema_hash=schema_hash(t.schema_json),
    )


@router.get("", response_model=list[AstTemplateResponse])
def list_templates(db: Session = Depends(get_db)):
    """返回每个模板名称的最新版本（按 created_at 降序取首条）。"""
    rows = db.query(AstTemplate).order_by(AstTemplate.created_at.desc()).all()
    seen_names: set[str] = set()
    latest: list[AstTemplateResponse] = []
    for r in rows:
        if r.name in seen_names:
            continue
        seen_names.add(r.name)
        latest.append(_template_response(r))
    return latest


@router.get("/{template_id}")
def get_template(
    template_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    engine: object = Depends(get_ontology_engine),
):
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")

    resp = _template_response(row)
    pairs = sorted(row.training_pairs, key=lambda p: p.created_at)
    # 同名模板的所有版本（供编辑器切换历史版本）。
    siblings = (
        db.query(AstTemplate)
        .filter(AstTemplate.name == row.name)
        .order_by(AstTemplate.created_at.desc())
        .all()
    )
    versions = [
        {"id": str(s.id), "version": s.version, "created_at": s.created_at.isoformat()}
        for s in siblings
    ]
    return {
        **resp.model_dump(),
        "schema_json": row.schema_json,
        "sample_text": row.sample_text,
        "sample_content_json": row.sample_content_json,
        "sample_analysis": row.sample_analysis,
        "training_pairs": [TrainingPairResponse.model_validate(p).model_dump() for p in pairs],
        "versions": versions,
    }


@router.post("", response_model=AstTemplateResponse, status_code=201)
def create_template(
    req: AstTemplateCreate,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
    engine: object = Depends(get_ontology_engine),
):
    try:
        (
            TemplateV2 if req.schema_json.get("schema_version") == 2 else ReportTemplate
        ).model_validate(req.schema_json)
    except Exception as exc:
        raise HTTPException(422, f"Template validation failed: {exc}") from exc

    existing = (
        db.query(AstTemplate)
        .filter(AstTemplate.name == req.name, AstTemplate.version == req.version)
        .first()
    )
    if existing:
        raise HTTPException(
            409, f"Template 'name={req.name}, version={req.version}' already exists"
        )

    identity_id = uuid4()
    schema = req.schema_json
    if schema.get("schema_version") == 2:
        schema = (
            TemplateV2.model_validate(schema)
            .model_copy(
                update={
                    "template_revision_id": str(identity_id),
                    "template_family_id": str(identity_id),
                    "revision_no": 1,
                }
            )
            .model_dump(mode="json")
        )
    if schema.get("schema_version") == 2:
        from app.services.extraction.extraction_tasks import semantic_schema_from_engine
        from app.services.reporting.template_preparation import prepare_template

        schema = prepare_template(
            db, schema, document_class=req.iri_pattern,
            classes=lambda: semantic_schema_from_engine(engine),
        ).model_dump(mode="json")
    row = AstTemplate(
        id=identity_id,
        name=req.name,
        version=req.version,
        doc_no=req.doc_no,
        iri_pattern=req.iri_pattern,
        schema_json=schema,
        schema_version=schema.get("schema_version", 1),
        template_family_id=str(identity_id),
        revision_no=1,
        schema_hash=schema_hash(schema),
        sample_text=req.sample_text,
        sample_content_json=req.sample_content_json,
        sample_analysis=req.sample_analysis or (req.sample_content_json or {}).get("analysis"),
        created_by=getattr(identity, "username", "system"),
    )
    db.add(row)
    audit.append(
        db,
        "template.create",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": req.name, "version": req.version},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _template_response(row)


@router.put("/{template_id}", response_model=AstTemplateResponse, status_code=201)
def update_template(
    template_id: UUID,
    req: AstTemplateUpdate,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    old = db.get(AstTemplate, template_id)
    if not old:
        raise HTTPException(404, "模板不存在")
    if old.schema_json.get("schema_version") == 2 or req.schema_json.get("schema_version") == 2:
        raise ReportingError("VERSIONED_REVISION_ENDPOINT_REQUIRED", status=409)

    try:
        ReportTemplate.model_validate(req.schema_json)
    except Exception as exc:
        raise HTTPException(422, f"Template validation failed: {exc}") from exc

    new_version = req.version or _auto_version(old.version)

    existing = (
        db.query(AstTemplate)
        .filter(AstTemplate.name == old.name, AstTemplate.version == new_version)
        .first()
    )
    if existing and existing.id != old.id:
        raise HTTPException(
            409, f"Version '{new_version}' already exists for template '{old.name}'"
        )

    row = AstTemplate(
        name=old.name,
        version=new_version,
        doc_no=old.doc_no,
        iri_pattern=old.iri_pattern,
        status="draft",
        schema_json=req.schema_json,
        sample_text=old.sample_text,
        sample_content_json=old.sample_content_json,
        sample_analysis=old.sample_analysis,
        sample_docx_path=old.sample_docx_path,
        owner=old.owner,
        default_source_path=old.default_source_path,
        default_source_filename=old.default_source_filename,
        default_source_job_id=old.default_source_job_id,
        is_default=old.is_default,
        created_by=getattr(identity, "username", "system"),
    )
    db.add(row)
    audit.append(
        db,
        "template.update",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": old.name, "from_version": old.version, "to_version": new_version},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _template_response(row)


def _auto_version(current: str) -> str:
    if current.startswith("v") and current[1:].isascii() and current[1:].isdigit():
        return f"v{int(current[1:]) + 1}"
    return f"{current}.1"


_VALID_STATUSES = {"draft", "published", "archived"}


@router.patch("/{template_id}", response_model=AstTemplateResponse)
def update_template_meta(
    template_id: UUID,
    req: AstTemplateMetaUpdate,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    """015: in-place metadata edit (status / iri_pattern) — NO version bump.

    Distinct from PUT (which copy-on-writes a new schema version). Used by the
    list-page ⋮ actions (发布/归档) and IRI 模式 edits.
    """
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")

    if row.schema_json.get("schema_version") == 2 and (
        req.status == "published"
        or row.status == "published"
        and any(getattr(req, key) is not None for key in ("doc_no", "iri_pattern", "status"))
    ):
        raise ReportingError("COMPILED_PUBLICATION_REQUIRED", status=409)

    if (row.schema_json.get("schema_version") == 2 and req.iri_pattern is not None
            and req.iri_pattern != row.iri_pattern):
        raise ReportingError("VERSIONED_REVISION_ENDPOINT_REQUIRED",
                             "关联文档类型应随模板数据来源保存为新修订", status=409)

    changed: dict = {}
    if req.name is not None and req.name != row.name:
        new_name = req.name.strip()
        if not new_name:
            raise HTTPException(422, "模板名称不能为空")
        # 改名须复检 (name, version) 唯一约束（与 update_template 一致）。
        clash = (
            db.query(AstTemplate)
            .filter(AstTemplate.name == new_name, AstTemplate.version == row.version)
            .first()
        )
        if clash and clash.id != row.id:
            raise HTTPException(409, f"模板 'name={new_name}, version={row.version}' 已存在")
        row.name = new_name
        changed["name"] = new_name
    if req.doc_no is not None:
        row.doc_no = req.doc_no or None
        changed["doc_no"] = row.doc_no
    if req.owner is not None:
        row.owner = req.owner or None
        changed["owner"] = row.owner
    if req.status is not None:
        if req.status not in _VALID_STATUSES:
            raise HTTPException(422, f"Invalid status '{req.status}' (draft|published|archived)")
        row.status = req.status
        changed["status"] = req.status
    if req.iri_pattern is not None:
        row.iri_pattern = req.iri_pattern or None
        changed["iri_pattern"] = row.iri_pattern

    if changed:
        audit.append(
            db,
            "template.meta_update",
            actor=getattr(identity, "username", "system"),
            entity_iri=str(row.id),
            details={"name": row.name, "version": row.version, **changed},
            commit=False,
        )
    db.commit()
    db.refresh(row)
    return _template_response(row)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")
    if row.is_default:
        raise HTTPException(400, "Cannot delete the default template")
    from app.models.reporting import TemplateCompilation

    if (
        row.status in {"published", "archived"}
        or db.query(TemplateCompilation)
        .filter_by(
            template_id=row.id,
        )
        .first()
    ):
        raise ReportingError("TEMPLATE_HISTORY_IMMUTABLE", status=409)

    # 提交前先算引用计数（此刻 row 仍在库），提交成功后再 best-effort 删文件：先删后提交会在
    # 提交失败/回滚时留下断引用（DB 仍指向已删文件）。
    orphan_sample = None
    if row.sample_docx_path:
        siblings = (
            db.query(AstTemplate)
            .filter(
                AstTemplate.sample_docx_path == row.sample_docx_path,
                AstTemplate.id != row.id,
            )
            .count()
        )
        if siblings == 0:
            orphan_sample = row.sample_docx_path

    audit.append(
        db,
        "template.delete",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": row.name, "version": row.version},
        commit=False,
    )
    db.delete(row)
    db.commit()
    # best-effort：删除已提交成功，孤儿文件清理失败（权限/IO）不应把成功的删除变成 500。
    _best_effort_unlink(orphan_sample)


@router.post("/{template_id}/set-default", response_model=AstTemplateResponse)
def set_default_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")

    db.query(AstTemplate).filter(AstTemplate.is_default.is_(True)).update({"is_default": False})
    row.is_default = True
    audit.append(
        db,
        "template.set_default",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": row.name, "version": row.version},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _template_response(row)


# ── 015 基本信息：默认示例文档替换 / 默认源文件 / 训练数据 ─────────────


def _get_template_or_404(template_id: UUID, db: Session) -> AstTemplate:
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")
    return row


@router.post("/{template_id}/sample")
async def replace_sample(
    template_id: UUID,
    file: UploadFile = File(...),
    include_content: bool = True,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    """替换既有模板的默认示例文档（固化输出 section / 格式）。支持 .doc（后端转 .docx）
    / .docx，解析为忠于原文结构的 tiptap 并同步 sample_text，供 AI 插槽建议与忠实预览。
    include_content=False 在持久化成功后仅返回 204，供无需立即预览的创建向导使用。"""
    row = _get_template_or_404(template_id, db)
    if row.status in {"published", "archived"}:
        raise ReportingError("TEMPLATE_REVISION_REQUIRED", status=409)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".doc", ".docx"):
        raise HTTPException(422, "仅支持 .doc / .docx 文件")

    from app.services.extraction.doc_converter import DocConversionError, ensure_docx_async
    from app.services.extraction.document_annotator import parse_word_to_tiptap
    from app.services.extraction.slot_suggester import tiptap_to_text

    old_path = row.sample_docx_path
    raw = await file.read()

    # 先在临时目录内完成 转换→解析→非空校验，全部通过后才把 .docx 落到唯一持久路径——转换/解析
    # 失败时无任何持久落盘（无需回删）。唯一文件名（完整 128-bit uuid4）绝不原地覆盖：同模板重传或
    # 版本 copy-on-write 共享同一 sample_docx_path 时新旧路径不可能相撞（2^-128），既不会在校验前
    # 截断旧（好）文件，也不会串改被其他版本行引用的样例。旧文件仅在提交成功后按引用计数清理。
    _UPLOADS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"upload{ext}"
        src.write_bytes(raw)
        try:
            docx_tmp = await ensure_docx_async(str(src))
        except DocConversionError as exc:
            raise HTTPException(422, f"文档转换失败：{exc}") from exc
        content_json = await asyncio.to_thread(parse_word_to_tiptap, docx_tmp, original_path=src)
        plain_text = tiptap_to_text(content_json)
        if not plain_text.strip():
            raise HTTPException(422, "无法从文档中提取文本内容")
        saved_path = str(_UPLOADS / f"tpl_{template_id}_sample_{uuid4().hex}.docx")
        shutil.copyfile(docx_tmp, saved_path)

    row.sample_docx_path = saved_path
    row.sample_content_json = content_json
    row.sample_analysis = content_json.get("analysis")
    row.sample_text = plain_text
    try:
        audit.append(
            db,
            "template.sample_replace",
            actor=getattr(identity, "username", "system"),
            entity_iri=str(row.id),
            details={"name": row.name, "version": row.version, "filename": file.filename},
            commit=False,
        )
        db.commit()
    except Exception:
        # 提交失败：刚落盘的新文件此刻无任何 DB 引用（事务已回滚），必须删掉，否则形成孤儿。
        # rollback 与 unlink 各自吞异常：rollback 失败（如 DB 断连——commit/rollback 连环失败的
        # 典型场景）也不得跳过文件清理；unlink 失败不得掩盖原始 commit 异常。
        try:
            db.rollback()
        except Exception:  # pragma: no cover - 防御性：rollback 失败仅记录
            _log.warning("提交失败后 rollback 亦失败（已忽略）", exc_info=True)
        _best_effort_unlink(saved_path)
        raise
    db.refresh(row)
    # 提交成功后再清理旧文件（best-effort）：先删后提交会在提交回滚时留下断引用（DB 仍指向
    # 已删文件，渲染时静默回退空白文档）。删旧文件仅当无同名模板的其他版本行共享它——版本
    # copy-on-write 会令多版本共用一份 sample_docx_path（见 update_template），故引用计数。
    # 整段清理裹 try/except：替换已提交成功，清理失败（DB 连接/权限/IO）绝不能把成功操作变 500。
    if old_path and old_path != saved_path:
        try:
            shared = (
                db.query(AstTemplate)
                .filter(
                    AstTemplate.sample_docx_path == old_path,
                    AstTemplate.id != row.id,
                )
                .count()
            )
            if shared == 0:
                Path(old_path).unlink(missing_ok=True)
        except Exception:
            _log.warning("替换示例后清理旧文件失败（已忽略）：%s", old_path, exc_info=True)
    if not include_content:
        return Response(status_code=204)
    return {
        "content_json": content_json,
        "plain_text": plain_text,
        "analysis": content_json.get("analysis"),
    }


@router.post("/{template_id}/default-source", response_model=AstTemplateResponse)
async def upload_default_source(
    template_id: UUID,
    file: UploadFile = File(...),
    background: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    engine: object = Depends(get_ontology_engine),
    identity: object = Depends(_maintainer),
):
    """上传/替换默认源文件，同时创建 ExtractionJob 以驱动标注管线。"""
    from app.api.extraction import _enqueue_annotation

    row = _get_template_or_404(template_id, db)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".doc", ".docx", ".xlsx", ".xls"):
        raise HTTPException(422, "仅支持 Word(.doc/.docx) 或 Excel(.xlsx/.xls) 文件")

    # Copy-on-write：新上传落到唯一路径（不原地覆盖旧源），转换/落盘全部成功、DB commit 之后
    # 才清理旧文件与旧缓存。先删后写（原实现）会在转换失败时永久丢失旧默认源——回归缺陷，已修。

    _UPLOADS.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    # 落盘后缀：遗留 .doc 归一化为 .docx（下游标注链只认 .docx），其余原样保留。
    stored_suffix = ".docx" if suffix == ".doc" else suffix
    saved_path = str(_UPLOADS / f"tpl_{template_id}_source_{uuid4().hex}{stored_suffix}")

    # 遗留 .doc：在临时目录内转换+校验，仅把通过校验的 .docx 拷到持久唯一路径——转换失败/超时
    # 产生的空/损坏中间产物随 TemporaryDirectory 一并清理，绝不会在 data/uploads 里留下孤儿。
    if suffix == ".doc":
        from app.services.extraction.doc_converter import DocConversionError, ensure_docx_async

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / f"upload{suffix}"
            src.write_bytes(raw)
            try:
                docx_tmp = await ensure_docx_async(str(src))
            except DocConversionError as exc:
                raise HTTPException(422, f"文档转换失败：{exc}") from exc
            shutil.copyfile(docx_tmp, saved_path)
    else:
        Path(saved_path).write_bytes(raw)

    row.default_source_path = saved_path
    row.default_source_filename = file.filename

    source_type = "excel" if suffix in (".xlsx", ".xls") else "word"
    job = ExtractionJob(
        source_type=source_type,
        source_filename=file.filename,
        document_path=saved_path,
        source_config={
            "mode": "template_default",
            "template_id": str(template_id),
            "doc_class_iri": row.iri_pattern,
        },
        status="running",
    )
    db.add(job)
    db.flush()
    row.default_source_job_id = job.id

    try:
        audit.append(
            db,
            "template.default_source_upload",
            actor=getattr(identity, "username", "system"),
            entity_iri=str(row.id),
            details={"name": row.name, "filename": file.filename},
            commit=False,
        )
        db.commit()
    except Exception:
        # 提交失败：刚落盘的新文件此刻无任何 DB 引用（事务已回滚），删掉以免留孤儿；旧源保持不变。
        try:
            db.rollback()
        except Exception:  # pragma: no cover - 防御性：rollback 失败仅记录
            _log.warning("默认源提交失败后 rollback 亦失败（已忽略）", exc_info=True)
        _best_effort_unlink(saved_path)
        raise
    db.refresh(row)

    # 提交成功后再清理旧资源（best-effort）：旧源文件（若与新路径不同）与旧标注缓存。

    if source_type in ("word", "excel"):
        _enqueue_annotation(job.id, background, engine, db, mode="start",
                            actor=getattr(identity, "username", "system"))

    return _template_response(row)


@router.delete("/{template_id}/default-source", response_model=AstTemplateResponse)
def delete_default_source(
    template_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    row = _get_template_or_404(template_id, db)
    row.default_source_path = None
    row.default_source_filename = None
    row.default_source_job_id = None
    db.commit()
    db.refresh(row)
    return _template_response(row)


@router.get("/{template_id}/training-pairs", response_model=list[TrainingPairResponse])
def list_training_pairs(template_id: UUID, db: Session = Depends(get_db)):
    _get_template_or_404(template_id, db)
    # 直查而非走 row.training_pairs 惰性集合：会话可能缓存已删条目（expire_on_commit=False）。
    return (
        db.query(AstTemplateTrainingPair)
        .filter(AstTemplateTrainingPair.template_id == template_id)
        .order_by(AstTemplateTrainingPair.created_at)
        .all()
    )


@router.post(
    "/{template_id}/training-pairs",
    response_model=TrainingPairResponse,
    status_code=201,
)
async def add_training_pair(
    template_id: UUID,
    source_file: UploadFile = File(...),
    report_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    """新增一条训练样例（源文档必填，评估报告可缺省，后补）。"""
    _get_template_or_404(template_id, db)
    pair = AstTemplateTrainingPair(
        template_id=template_id,
        source_filename=source_file.filename or "source",
        source_path="",  # 落盘后回填（需 pair.id 命名，避免碰撞）
        created_by=getattr(identity, "username", "system"),
    )
    db.add(pair)
    db.flush()  # 取 pair.id 作为文件名前缀
    pair.source_path = await _save_upload(source_file, f"train_{pair.id}_src")
    if report_file is not None and report_file.filename:
        pair.report_filename = report_file.filename
        pair.report_path = await _save_upload(report_file, f"train_{pair.id}_report")
    audit.append(
        db,
        "template.training_pair_add",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(template_id),
        details={"source": pair.source_filename, "report": pair.report_filename},
        commit=False,
    )
    db.commit()
    db.refresh(pair)
    return pair


@router.delete("/{template_id}/training-pairs/{pair_id}", status_code=204)
def delete_training_pair(
    template_id: UUID,
    pair_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    pair = db.get(AstTemplateTrainingPair, pair_id)
    if not pair or pair.template_id != template_id:
        raise HTTPException(404, "训练样例不存在")
    for p in (pair.source_path, pair.report_path):
        if p:
            Path(p).unlink(missing_ok=True)
    db.delete(pair)
    db.commit()


# ── 013 DOCX structured parse for template creation ────────────────────
# 后台把样例 DOCX 解析为忠于原文结构的 tiptap（不扁平化成文本），前端据此
# 忠实预览并回传结构化内容做 AI 分析——避免「解析成文本→送前台→送回」丢结构。
# 确定性离线解析，不受 llm_suggest_slots_enabled 门控（LLM 关时也能预览结构）。


@router.post("/parse-sample")
async def parse_sample(
    file: UploadFile = File(...),
    identity: object = Depends(_maintainer),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".doc", ".docx"):
        raise HTTPException(422, "仅支持 .doc / .docx 文件")

    from app.services.extraction.doc_converter import DocConversionError, ensure_docx_async
    from app.services.extraction.document_annotator import parse_word_to_tiptap
    from app.services.extraction.slot_suggester import tiptap_to_text

    content = await file.read()
    # 临时目录内完成 .doc→.docx 转换与解析，产物与 profile 随目录一并清理（无持久落盘）。
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"upload{ext}"
        src.write_bytes(content)
        try:
            docx_path = await ensure_docx_async(str(src))
        except DocConversionError as exc:
            raise HTTPException(422, f"文档转换失败：{exc}") from exc
        content_json = await asyncio.to_thread(parse_word_to_tiptap, docx_path, original_path=src)
        plain_text = tiptap_to_text(content_json)

    if not plain_text.strip():
        raise HTTPException(422, "无法从文档中提取文本内容")

    return {
        "content_json": content_json,
        "plain_text": plain_text,
        "analysis": content_json.get("analysis"),
    }


# ── 013 Suggest Slots (AI-assisted template design) ────────────────────


@router.post("/suggest-slots")
def suggest_slots_endpoint(
    req: SuggestSlotsRequest,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
    engine: object = Depends(get_ontology_engine),
):
    from app.config import settings
    from app.services.llm.local_client import get_local_llm

    client = get_local_llm() if settings.llm_suggest_slots_enabled else None

    # Resolve document text + structured content (tiptap) as the LLM analysis input.
    # 016 US1: the document entity type grounds ontology coverage — take the explicit
    # request field first, else recover it from the job annotation cache (D10).
    content_json: dict | None = None
    doc_class_iri: str | None = req.doc_class_iri
    if req.sample_content_json is not None:
        # 首选：前端回传的结构化样例——服务端派生 LLM 文本，绝不丢结构。
        from app.services.extraction.slot_suggester import tiptap_to_text

        content_json = req.sample_content_json
        document_text = tiptap_to_text(content_json)
    elif req.job_id is not None:
        from app.models.extraction import ExtractionJob

        job = db.get(ExtractionJob, req.job_id)
        if not job:
            raise HTTPException(404, "抽取作业不存在")
        from app.api.extraction import (
            _WORD_REPOSITORY_PREVIEW_MODE,
            _require_preview_capability,
            _word_job_mode,
        )

        _require_preview_capability(job)
        from app.services.extraction.slot_suggester import build_document_text

        document_text = build_document_text(job.document_path)
        # 复用抽取时预计算的标注缓存做忠实预览锚点（缺失则降级为无 source_ref）。
        cache_path = _annotation_cache_path(req.job_id)
        if (
            _word_job_mode(job) != _WORD_REPOSITORY_PREVIEW_MODE
            and cache_path.is_file()
        ):
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                content_json = cached.get("content")
                if not doc_class_iri:
                    doc_class_iri = (cached.get("doc_class") or {}).get("doc_class_iri")
            except Exception:
                content_json = None
    else:
        document_text = req.document_text or ""

    max_suggestions = min(req.max_suggestions, settings.suggest_slots_max)

    from app.services.extraction.slot_suggester import suggest_slots

    result = suggest_slots(
        client,
        document_text=document_text,
        existing_template=req.existing_template,
        max_suggestions=max_suggestions,
        ontology_engine=engine,
        content_json=content_json,
        doc_class_iri=doc_class_iri,
        analysis=req.analysis,
    )
    return result


@router.post("/coverage-doc-classes", response_model=CoverageDocClassesResponse)
def coverage_doc_classes(
    req: CoverageDocClassesRequest,
    identity: object = Depends(_maintainer),
    engine: object = Depends(get_ontology_engine),
):
    """返回候选文档类型中「已建模可覆盖关系」的子集（016：仅启用已建模类型）。

    作者化 UI 用此结果门控「关联文档类型」下拉——只启用 AI 覆盖分析确能产出边的类型
    （当前仅 CMCReport；本体补充关系后自动扩大）。判定与 suggester 走同一
    ``_supplemented_schema_edges``（``coverage_capable``），保证「能选」⇔「能产出覆盖」。
    只读（Principle II）。前端候选清单为唯一入参，避免前后端类型清单漂移。
    """
    from app.services.extraction.slot_suggester import coverage_capable

    return CoverageDocClassesResponse(
        capable=[iri for iri in req.doc_class_iris if coverage_capable(engine, iri)]
    )


# ── 015 Section 行文 Prompt design assist ──────────────────────────────


@router.post("/generate-section-prompt", response_model=GenerateSectionPromptResponse)
def generate_section_prompt_endpoint(
    req: GenerateSectionPromptRequest,
    identity: object = Depends(_maintainer),
):
    raise ReportingError(
        "STRUCTURED_OUTPUT_PROMPT_REQUIRED",
        status=409,
        message="Configure authorized InputRefs and a versioned prompt policy.",
    )


@router.post("/preview-section-narrative", response_model=PreviewSectionNarrativeResponse)
def preview_section_narrative_endpoint(
    req: PreviewSectionNarrativeRequest,
    identity: object = Depends(_maintainer),
    db: Session = Depends(get_db),
):
    raise ReportingError(
        "STRUCTURED_OUTPUT_PREVIEW_REQUIRED",
        status=409,
        message="Use /api/report-previews with a V2 template revision.",
    )


# ── Template match (T011) ───────────────────────────────────────────────


def _annotation_cache_path(job_id) -> Path:
    return Path("data/uploads") / f"{job_id}.annotated.json"


@router.get("/match/{job_id}", response_model=TemplateMatchResponse)
def match_template_for_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(get_current_user),
):
    from app.services.reporting.legacy_facade import selected_template

    job, template = selected_template(db, job_id)
    return TemplateMatchResponse(
        template_id=template.id,
        template_name=template.name,
        template_version=template.version,
        match_source="selected" if (job.source_config or {}).get("template_id") else "default",
    )
