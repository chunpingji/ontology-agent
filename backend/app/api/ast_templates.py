"""AST 模板管理 API（012-ast-template-llm-pipeline, contracts/ast-templates-api）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import ROLE_SENIOR_ANALYST, get_ontology_engine, require_role
from app.models.extraction import AstTemplate, AstTemplateTrainingPair
from app.schemas.extraction import (
    AstTemplateCreate,
    AstTemplateMetaUpdate,
    AstTemplateResponse,
    AstTemplateUpdate,
    GenerateSectionPromptRequest,
    GenerateSectionPromptResponse,
    SuggestSlotsRequest,
    TemplateMatchResponse,
    TrainingPairResponse,
)
from app.services import audit
from app.services.reporting.ast_template import ReportTemplate, resolve_template

router = APIRouter()

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


def _count_slots(schema_json: dict) -> int:
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
        default_source_filename=t.default_source_filename,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# ── Template CRUD (T009) ────────────────────────────────────────────────


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
def get_template(template_id: UUID, db: Session = Depends(get_db)):
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
        "training_pairs": [
            TrainingPairResponse.model_validate(p).model_dump() for p in pairs
        ],
        "versions": versions,
    }


@router.post("", response_model=AstTemplateResponse, status_code=201)
def create_template(
    req: AstTemplateCreate,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    try:
        ReportTemplate.model_validate(req.schema_json)
    except Exception as exc:
        raise HTTPException(422, f"Template validation failed: {exc}") from exc

    existing = (
        db.query(AstTemplate)
        .filter(AstTemplate.name == req.name, AstTemplate.version == req.version)
        .first()
    )
    if existing:
        raise HTTPException(409, f"Template 'name={req.name}, version={req.version}' already exists")

    row = AstTemplate(
        name=req.name,
        version=req.version,
        doc_no=req.doc_no,
        iri_pattern=req.iri_pattern,
        schema_json=req.schema_json,
        sample_text=req.sample_text,
        sample_content_json=req.sample_content_json,
        created_by=getattr(identity, "username", "system"),
    )
    db.add(row)
    audit.append(
        db, "template.create",
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
        raise HTTPException(409, f"Version '{new_version}' already exists for template '{old.name}'")

    row = AstTemplate(
        name=old.name,
        version=new_version,
        doc_no=old.doc_no,
        iri_pattern=old.iri_pattern,
        status=old.status,
        schema_json=req.schema_json,
        sample_text=old.sample_text,
        sample_content_json=old.sample_content_json,
        owner=old.owner,
        default_source_path=old.default_source_path,
        default_source_filename=old.default_source_filename,
        is_default=old.is_default,
        created_by=getattr(identity, "username", "system"),
    )
    db.add(row)
    audit.append(
        db, "template.update",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": old.name, "from_version": old.version, "to_version": new_version},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _template_response(row)


def _auto_version(current: str) -> str:
    m = re.match(r"^v(\d+)$", current)
    if m:
        return f"v{int(m.group(1)) + 1}"
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
            db, "template.meta_update",
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

    audit.append(
        db, "template.delete",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": row.name, "version": row.version},
        commit=False,
    )
    db.delete(row)
    db.commit()


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
        db, "template.set_default",
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
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    """替换既有模板的默认示例文档（固化输出 section / 格式）。解析为忠于原文结构的
    tiptap 并同步 sample_text，供 AI 插槽建议与忠实预览。"""
    row = _get_template_or_404(template_id, db)
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(422, "仅支持 .docx 文件")

    import tempfile

    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        tmp.write(content)
        tmp.close()
        from app.services.extraction.document_annotator import parse_word_to_tiptap
        from app.services.extraction.slot_suggester import tiptap_to_text

        content_json = parse_word_to_tiptap(tmp.name)
        plain_text = tiptap_to_text(content_json)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    if not plain_text.strip():
        raise HTTPException(422, "无法从文档中提取文本内容")

    row.sample_content_json = content_json
    row.sample_text = plain_text
    audit.append(
        db, "template.sample_replace",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": row.name, "version": row.version, "filename": file.filename},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return {"content_json": content_json, "plain_text": plain_text}


@router.post("/{template_id}/default-source", response_model=AstTemplateResponse)
async def upload_default_source(
    template_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    """上传/替换默认源文件（固化输出格式的参照原件）。"""
    row = _get_template_or_404(template_id, db)
    # 替换时清理旧文件（best-effort）。
    if row.default_source_path:
        Path(row.default_source_path).unlink(missing_ok=True)
    row.default_source_path = await _save_upload(file, f"tpl_{template_id}_source")
    row.default_source_filename = file.filename
    audit.append(
        db, "template.default_source_upload",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"name": row.name, "filename": file.filename},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _template_response(row)


@router.delete("/{template_id}/default-source", response_model=AstTemplateResponse)
def delete_default_source(
    template_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    row = _get_template_or_404(template_id, db)
    if row.default_source_path:
        Path(row.default_source_path).unlink(missing_ok=True)
    row.default_source_path = None
    row.default_source_filename = None
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
        db, "template.training_pair_add",
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
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(422, "仅支持 .docx 文件")

    import tempfile

    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        tmp.write(content)
        tmp.close()
        from app.services.extraction.document_annotator import parse_word_to_tiptap
        from app.services.extraction.slot_suggester import tiptap_to_text

        content_json = parse_word_to_tiptap(tmp.name)
        plain_text = tiptap_to_text(content_json)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    if not plain_text.strip():
        raise HTTPException(422, "无法从文档中提取文本内容")

    return {"content_json": content_json, "plain_text": plain_text}


# ── 013 Suggest Slots (AI-assisted template design) ────────────────────


@router.post("/suggest-slots")
def suggest_slots_endpoint(
    req: SuggestSlotsRequest,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
    engine: object = Depends(get_ontology_engine),
):
    from app.config import settings

    if not settings.llm_suggest_slots_enabled:
        raise HTTPException(503, "插槽建议功能未启用（llm_suggest_slots_enabled=False）")

    from app.services.llm.local_client import get_local_llm

    client = get_local_llm()
    if client is None:
        raise HTTPException(503, "本地 LLM 不可用，请检查 local_llm_enabled 和端点配置")

    # Resolve document text + structured content (tiptap) for source_ref anchors.
    content_json: dict | None = None
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
        from app.services.extraction.slot_suggester import build_document_text

        document_text = build_document_text(job.document_path)
        # 复用抽取时预计算的标注缓存做忠实预览锚点（缺失则降级为无 source_ref）。
        cache_path = _annotation_cache_path(req.job_id)
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                content_json = cached.get("content")
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
    )
    return result


# ── 015 Section 行文 Prompt design assist ──────────────────────────────


@router.post("/generate-section-prompt", response_model=GenerateSectionPromptResponse)
def generate_section_prompt_endpoint(
    req: GenerateSectionPromptRequest,
    identity: object = Depends(_maintainer),
):
    """Derive a 行文 Prompt for one section from the sample + its slot labels.

    Design-time AI assist (mirrors suggest-slots gating). The returned prompt is
    saved into the section's ``prompt`` field; at report time the generation
    engine calls the LLM with it to fuse the section's slot values into prose.
    """
    from app.config import settings

    if not settings.llm_suggest_slots_enabled:
        raise HTTPException(503, "行文 Prompt 生成未启用（llm_suggest_slots_enabled=False）")

    from app.services.llm.local_client import get_local_llm

    client = get_local_llm()
    if client is None:
        raise HTTPException(503, "本地 LLM 不可用，请检查 local_llm_enabled 和端点配置")

    from app.services.reporting.narrative_generator import generate_section_prompt

    prompt = generate_section_prompt(
        client,
        section_title=req.section_title,
        slot_labels=req.slot_labels,
        sample_text=req.sample_text,
    )
    if not prompt:
        raise HTTPException(502, "行文 Prompt 生成失败，请检查本地 LLM 日志")
    return GenerateSectionPromptResponse(prompt=prompt)


# ── Template match (T011) ───────────────────────────────────────────────


def _annotation_cache_path(job_id) -> Path:
    return Path("data/uploads") / f"{job_id}.annotated.json"


@router.get("/match/{job_id}", response_model=TemplateMatchResponse)
def match_template_for_job(
    job_id: UUID,
    db: Session = Depends(get_db),
):
    cache_path = _annotation_cache_path(job_id)
    if not cache_path.exists():
        raise HTTPException(422, "文档未分类，无法匹配模板")

    result = json.loads(cache_path.read_text(encoding="utf-8"))
    doc_class = result.get("doc_class")
    doc_class_iri = doc_class.get("doc_class_iri") if doc_class else None

    tpl, match_source, db_id = resolve_template(doc_class_iri, db)

    return TemplateMatchResponse(
        template_id=db_id or UUID(int=0),
        template_name=getattr(tpl, "template_id", ""),
        template_version=getattr(tpl, "revision", ""),
        match_source=match_source,
    )
