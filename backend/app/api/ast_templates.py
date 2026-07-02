"""AST 模板管理 API（012-ast-template-llm-pipeline, contracts/ast-templates-api）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import ROLE_SENIOR_ANALYST, get_ontology_engine, require_role
from app.models.extraction import AstTemplate, DocumentTypeMapping
from app.schemas.extraction import (
    AstTemplateCreate,
    AstTemplateResponse,
    AstTemplateUpdate,
    DocumentTypeMappingCreate,
    DocumentTypeMappingResponse,
    SuggestSlotsRequest,
    TemplateMatchResponse,
)
from app.services import audit
from app.services.reporting.ast_template import ReportTemplate, resolve_template

router = APIRouter()

_maintainer = require_role(ROLE_SENIOR_ANALYST)


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
        slot_count=_count_slots(t.schema_json),
        is_default=t.is_default,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# ── Template CRUD (T009) ────────────────────────────────────────────────


@router.get("", response_model=list[AstTemplateResponse])
def list_templates(db: Session = Depends(get_db)):
    rows = db.query(AstTemplate).order_by(AstTemplate.created_at.desc()).all()
    return [_template_response(r) for r in rows]


@router.get("/{template_id}")
def get_template(template_id: UUID, db: Session = Depends(get_db)):
    row = db.get(AstTemplate, template_id)
    if not row:
        raise HTTPException(404, "模板不存在")
    resp = _template_response(row)
    return {
        **resp.model_dump(),
        "schema_json": row.schema_json,
        "sample_text": row.sample_text,
        "sample_content_json": row.sample_content_json,
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
        schema_json=req.schema_json,
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


# ── Document type mappings (T010) ───────────────────────────────────────


mapping_router = APIRouter()


@mapping_router.get("", response_model=list[DocumentTypeMappingResponse])
def list_mappings(db: Session = Depends(get_db)):
    rows = (
        db.query(DocumentTypeMapping)
        .join(AstTemplate)
        .order_by(DocumentTypeMapping.priority.desc())
        .all()
    )
    return [
        DocumentTypeMappingResponse(
            id=m.id,
            doc_class_iri_pattern=m.doc_class_iri_pattern,
            template_id=m.template_id,
            template_name=m.template.name,
            template_version=m.template.version,
            priority=m.priority,
            created_at=m.created_at,
        )
        for m in rows
    ]


@mapping_router.post("", response_model=DocumentTypeMappingResponse, status_code=201)
def create_mapping(
    req: DocumentTypeMappingCreate,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    tpl = db.get(AstTemplate, req.template_id)
    if not tpl:
        raise HTTPException(404, "模板不存在")

    row = DocumentTypeMapping(
        doc_class_iri_pattern=req.doc_class_iri_pattern,
        template_id=req.template_id,
        priority=req.priority,
    )
    db.add(row)
    audit.append(
        db, "mapping.create",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"pattern": req.doc_class_iri_pattern, "template": tpl.name},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return DocumentTypeMappingResponse(
        id=row.id,
        doc_class_iri_pattern=row.doc_class_iri_pattern,
        template_id=row.template_id,
        template_name=tpl.name,
        template_version=tpl.version,
        priority=row.priority,
        created_at=row.created_at,
    )


@mapping_router.delete("/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: UUID,
    db: Session = Depends(get_db),
    identity: object = Depends(_maintainer),
):
    row = db.get(DocumentTypeMapping, mapping_id)
    if not row:
        raise HTTPException(404, "映射不存在")

    audit.append(
        db, "mapping.delete",
        actor=getattr(identity, "username", "system"),
        entity_iri=str(row.id),
        details={"pattern": row.doc_class_iri_pattern},
        commit=False,
    )
    db.delete(row)
    db.commit()
