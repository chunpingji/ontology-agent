"""Versioned, deterministic CMC demonstration; no recognition or fact publication."""

import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.entity_shadow import EntityShadow
from app.models.extraction import AstTemplate, ExtractionJob, GeneratedReport
from app.services import audit
from app.services.reporting.batch_demo_body import build_body
from app.services.reporting.batch_demo_layout import SampleLayout
from app.services.reporting.output_ast import OutputNode, plain_text
from app.services.reporting.template_v2 import TemplateV2

DATA_ROOT = Path(__file__).with_name("demo")
ARTIFACT_ROOT = Path("data/reports/batch-demo")
REPORT_TYPE = "batch_record_demo"
TEMPLATE_ID = UUID("8542466b-d6e6-4052-ae7e-05ca9c99a1c3")
JOB_KEYS = ("job_id", "jobId", "source_job_id", "extraction_job_id", "hasJob", "sourceJob")


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()


def definitions():
    return tuple(json.loads((DATA_ROOT / name).read_text())
                 for name in ("hrs5592.json", "batch-template.json"))


def select_path(graph, path):
    nodes = [{"sub_relationships": graph["relationships"]}]
    for predicate in path:
        nodes = [child for node in nodes for child in node["sub_relationships"]
                 if child["predicate_iri"] == predicate]
    return nodes


def validate(graph, template):
    checks = []
    for section in template["sections"]:
        nodes = select_path(graph, section["path"])
        errors = []
        minimum = section["min_count"]
        if len(nodes) < minimum:
            errors.append(f"至少需要 {minimum} 个实体，实际 {len(nodes)} 个")
        for node in nodes:
            properties = {p["iri"]: p["value"] for p in node["object_data_properties"]}
            missing = [iri.rsplit("/", 1)[-1] for iri in section["required_properties"]
                       if properties.get(iri) in (None, "", [])]
            if missing:
                errors.append(node["object_text"] + " 缺少 " + "、".join(missing))
            if section["mode"] == "operations":
                ops = node.get("operations", [])
                if not ops or any(not op.get("instruction") or not op.get("source_ref")
                                  for op in ops):
                    errors.append(node["object_text"] + " 缺少工艺操作或原文出处")
                outputs = [n for n in node["sub_relationships"] if n["predicate_iri"].endswith(
                    ("/producesIntermediate", "/producesFinalProduct"),
                )]
                if not outputs:
                    errors.append(node["object_text"] + " 缺少中间体/最终产品关系")
        checks.append({"id": section["id"], "label": section["title"],
                       "path": section["path"], "count": len(nodes),
                       "passed": not errors, "errors": errors})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def read_source(db, document_iri):
    """Verify original identity before sharing its fixture or binding a template."""
    graph, template = definitions()
    document = db.scalar(select(EntityShadow).where(
        EntityShadow.iri == document_iri, EntityShadow.module == "document",
    ))
    if document is None:
        raise HTTPException(404, "文档登记不存在")
    if (document.class_iri != template["root_class_iri"]
            or document.label_zh != graph["source_filename"]):
        return None
    props = document.properties_json or {}
    raw = next((props[k] for k in JOB_KEYS if props.get(k)), None)
    try:
        job = db.get(ExtractionJob, UUID(str(raw)))
    except ValueError:
        job = None
    if job is None or job.source_filename != graph["source_filename"]:
        raise HTTPException(409, "演示文档与原件作业不匹配")
    config = job.source_config or {}
    if (config.get("doc_class_iri") not in (None, document.class_iri)
            or config.get("doc_ref") not in (None, document.iri)):
        raise HTTPException(409, "演示文档类型或归属已变化")
    path = Path(job.document_path) if job.document_path else None
    if path is None or not path.is_file():
        raise HTTPException(409, "演示原件不可用，无法校验数据版本")
    with path.open("rb") as stream:
        file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if file_hash != graph["source_sha256"]:
        raise HTTPException(409, "原件已变化，静态演示图谱不适用于此版本")
    if (graph["doc_class"]["doc_class_iri"] != template["root_class_iri"]
            or graph["fixture_id"] != template["fixture_id"]):
        raise HTTPException(422, "演示图谱与模板不匹配")
    source = {"document_iri": document.iri, "source_job_id": str(job.id),
              "document_version": props.get("_version", 1), "source_sha256": file_hash}
    return graph, template, source, job


def registered_template(db, template, source, job):
    row = db.get(AstTemplate, TEMPLATE_ID)
    if row is None or not (row.schema_json or {}).get("demo_profile"):
        raise HTTPException(409, "演示模板尚未完成特化配置")
    try:
        schema = TemplateV2.model_validate(row.schema_json)
    except ValidationError as exc:
        raise HTTPException(409, "演示模板配置无效，请核对配置") from exc
    profile = schema.demo_profile
    if (profile.fixture_id != template["fixture_id"]
            or profile.contract_id != template["contract_id"]
            or profile.document_iri != source["document_iri"]
            or row.default_source_job_id != job.id
            or row.default_source_path != job.document_path
            or row.default_source_filename != job.source_filename
            or row.iri_pattern != template["root_class_iri"]
            or row.name != template["name"] or row.version.lstrip("v") != template["version"]
            or row.status != "draft" or row.is_default
            or schema.sections or schema.calculation_checks):
        raise HTTPException(409, "演示模板与共享文档配置已变化，请核对配置")
    return row


def template_context(db, template_id, actor):
    if template_id != TEMPLATE_ID:
        raise HTTPException(404, "演示模板不存在")
    row = db.get(AstTemplate, template_id)
    profile = (row.schema_json or {}).get("demo_profile") if row else None
    if not profile:
        raise HTTPException(409, "演示模板尚未完成特化配置")
    try:
        profile = TemplateV2.model_validate(row.schema_json).demo_profile
    except ValidationError as exc:
        raise HTTPException(409, "演示模板配置无效，请核对配置") from exc
    data, _ = context(db, profile.document_iri, actor)
    if not data["available"]:
        raise HTTPException(409, "演示模板绑定的文档已变化")
    return data


def context(db, document_iri, actor):
    loaded = read_source(db, document_iri)
    if loaded is None:
        return {"available": False}, None
    graph, template, source, job = loaded
    registered_template(db, template, source, job)
    layout = SampleLayout(db)
    template = {**template, "layout": layout.manifest}
    graph_hash = digest({"source": source, "graph": graph})
    template_hash = digest(template)
    latest = db.scalar(select(GeneratedReport).where(
        GeneratedReport.job_id == job.id, GeneratedReport.actor == actor,
        GeneratedReport.report_type == REPORT_TYPE, GeneratedReport.deleted_at.is_(None),
        GeneratedReport.report_status == "completed",
    ).order_by(GeneratedReport.created_at.desc()).limit(1))
    if latest and (
        latest.rules_summary.get("graph_hash") != graph_hash
        or latest.rules_summary.get("template_hash") != template_hash
    ):
        latest = None
    return {"available": True, **source, "graph": graph, "graph_hash": graph_hash,
            "template": template, "template_hash": template_hash,
            "validation": validate(graph, template),
            "preview_ast": build_body(graph, template, layout).model_dump(mode="json"),
            "latest_report": response(latest) if latest else None}, job


def response(report):
    payload = report.rules_summary or {}
    return {"id": str(report.id), "job_id": str(report.job_id),
            "report_type": report.report_type, "report_status": report.report_status,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "file_size": report.file_size, "graph_hash": payload.get("graph_hash"),
            "template_hash": payload.get("template_hash"), "stages": payload.get("stages", []),
            "body_ast": payload.get("body_ast")}


def generate(db, document_iri, actor, request):
    started = perf_counter()
    data, job = context(db, document_iri, actor)
    if not data["available"]:
        raise HTTPException(422, "该文档未配置批记录静态演示")
    if (data["graph_hash"] != request.graph_hash
            or data["template_hash"] != request.template_hash):
        raise HTTPException(409, "图谱或模板版本已变化，请重新读取与校验")
    if not data["validation"]["passed"]:
        raise HTTPException(422, "批记录输入契约未满足，请检查校验明细")
    # A DB primary key arbitrates concurrent replays without adding a mutable run domain.
    report_id = uuid5(NAMESPACE_URL, json.dumps([actor, str(job.id), request.request_key]))
    existing = db.get(GeneratedReport, report_id)
    if existing:
        if (existing.deleted_at is not None
                or existing.rules_summary.get("graph_hash") != data["graph_hash"]
                or existing.rules_summary.get("template_hash") != data["template_hash"]):
            raise HTTPException(409, "请求标识已用于其他版本或已删除结果，请重新生成")
        return response(existing)
    stages = [{"key": "prepare", "label": "读取与校验数据", "status": "completed",
               "detail": f"{len(data['validation']['checks'])} 项契约校验通过"},
              {"key": "template", "label": "匹配报告模板", "status": "completed",
               "detail": data["template"]["name"] + " v" + data["template"]["version"]}]
    layout = SampleLayout(db)
    if layout.manifest != data["template"]["layout"]:
        raise HTTPException(409, "样例版本已变化，请重新读取与校验")
    ast = OutputNode.model_validate(data["preview_ast"])
    stages.append({"key": "build", "label": "批记录生成", "status": "completed",
                   "detail": "已按模板关系路径填充，实际执行记录与签署栏留空，供打印填写"})
    content = layout.render(ast)
    stages.append({"key": "render", "label": "生成报告文档", "status": "completed",
                   "detail": f"Word 文档 · {len(content):,} 字节"})
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    file_path = ARTIFACT_ROOT / f"{report_id}-{uuid4().hex}.docx"
    stages.append({"key": "finalize", "label": "保存生成结果", "status": "completed",
                   "detail": "已保存至报告中心"})
    report = GeneratedReport(
        id=report_id, job_id=job.id, report_type=REPORT_TYPE, file_path=str(file_path),
        file_size=len(content), actor=actor, report_status="completed", rules_fired_count=0,
        rules_summary={"demo": True, "document_iri": document_iri,
                       "source": {key: data[key] for key in (
                           "document_iri", "source_job_id", "document_version", "source_sha256",
                       )},
                       "graph_hash": data["graph_hash"], "template_hash": data["template_hash"],
                       "graph": data["graph"], "template": data["template"],
                       "validation": data["validation"], "body_ast": ast.model_dump(mode="json"),
                       "stages": stages, "elapsed_ms": round((perf_counter() - started) * 1000)},
        narratives={"sections": [{"section_id": s.node_id, "title": s.text,
                                  "text": plain_text(s)} for s in ast.children]},
    )
    try:
        file_path.write_bytes(content)
        db.add(report)
        db.flush()
        audit.append(db, "report.batch_demo.generate", actor=actor, entity_iri=str(report_id),
                     details={"job_id": str(job.id), "graph_hash": data["graph_hash"],
                              "template_hash": data["template_hash"], "demo": True}, commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        file_path.unlink(missing_ok=True)
        winner = db.get(GeneratedReport, report_id)
        if winner and winner.deleted_at is None and (
            winner.rules_summary.get("graph_hash") == data["graph_hash"]
            and winner.rules_summary.get("template_hash") == data["template_hash"]
        ):
            return response(winner)
        raise HTTPException(409, "并发保存冲突，请重新读取后重试") from None
    except Exception:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise
    return response(report)
