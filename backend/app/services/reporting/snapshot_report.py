"""Freeze report inputs before scheduling; render only that immutable bundle.

Production candidates, annotation previews, external resolvers, template samples
and report-time enrichment are not fact sources for this consumer.
"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.models.evidence import EvidenceCoverage, EvidenceJobState
from app.models.extraction import AstTemplate
from app.models.ontology_meta import OntologyDecisionRule
from app.services.extraction.candidate_store import CandidateStore
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.template_extraction_plan import build_instance_coverage
from app.services.fact_commit import FactCommitService
from app.services.fact_selector import FactSelector
from app.services.reporting.ast_template import ReportTemplate, resolve_template
from app.services.reporting.coverage_validator import CoverageManifest, SlotCoverage


def build_report_inputs(db, job, *, schema, template_id=None, snapshot_id=None):
    config = job.source_config or {}
    template_id = template_id or config.get("template_id")
    row = db.get(AstTemplate, UUID(str(template_id))) if template_id else None
    if template_id and row is None:
        raise LookupError("report template not found")
    if row:
        template = ReportTemplate.model_validate(row.schema_json)
        template_id, version, name = str(row.id), row.version, row.name
        input_class = config.get("doc_class_iri") or row.iri_pattern or ""
        if row.iri_pattern and input_class != row.iri_pattern:
            raise ValueError("template input type does not match job document type")
    else:
        input_class = config.get("doc_class_iri") or ""
        template, _, resolved_id = resolve_template(input_class, db)
        row = db.get(AstTemplate, resolved_id) if resolved_id else None
        template_id = str(resolved_id) if resolved_id else template.template_id
        version, name = (
            (row.version, row.name) if row else (template.revision, template.template_id)
        )
    snapshot = FactCommitService(db, None).published_snapshot(job.id, snapshot_id)
    selector = FactSelector(snapshot, schema=schema) if snapshot else None
    state = db.get(EvidenceJobState, job.id, populate_existing=True)
    run = deepcopy(state.extraction_run or {}) if state else {}
    manifest = build_instance_coverage(
        template,
        selector,
        candidates=CandidateStore(db).list(job.id),
        template_version=version,
        template_db_id=template_id,
        extraction_run=run,
        discovery_decisions=run.get("discovery_decisions", {}),
    )
    sample = row.sample_docx_path if row else None
    sample_hash = (
        sha256(Path(sample).read_bytes()).hexdigest() if sample and Path(sample).is_file() else None
    )
    rules = [
        {
            "id": str(rule.id),
            "version": getattr(rule, "version", None),
            "antecedent": deepcopy(rule.antecedent),
            "consequent": deepcopy(rule.consequent),
        }
        for rule in db.scalars(
            select(OntologyDecisionRule)
            .where(
                OntologyDecisionRule.rule_group == "risk_assessment",
                OntologyDecisionRule.is_disabled.is_(False),
            )
            .order_by(OntologyDecisionRule.priority)
        )
    ]
    frozen = {
        **manifest,
        "template_name": name,
        "template_status": row.status if row else "published",
        "template_schema": template.model_dump(mode="json"),
        "input_class_iri": input_class,
        "source_filename": job.source_filename or "",
        "sample_docx_path": sample if sample_hash else None,
        "sample_sha256": sample_hash,
        "semantic_schema": deepcopy(schema),
        "fact_snapshot": snapshot,
        "rules": rules,
        "gap_history": run.get("gap_history", []),
    }
    frozen["manifest_id"] = stable_id(
        "report-input", {k: v for k, v in frozen.items() if k != "manifest_id"}
    )
    return frozen


def freeze_report_inputs(db, job_id, inputs):
    if not inputs.get("snapshot_id"):
        raise ValueError("report requires a published fact snapshot; review and commit first")
    if inputs.get("template_status") != "published":
        raise ValueError("formal report requires a published template version")
    selector = FactSelector(inputs["fact_snapshot"], schema=inputs["semantic_schema"])
    if not selector.instances(inputs["input_class_iri"]):
        raise ValueError("document root has not been committed")
    row = db.get(EvidenceCoverage, inputs["manifest_id"])
    if row is None:
        row = EvidenceCoverage(
            id=inputs["manifest_id"],
            job_id=job_id,
            snapshot_id=inputs["snapshot_id"],
            template_id=inputs["template_id"],
            payload=deepcopy(inputs),
        )
        db.add(row)
        db.flush()
    elif str(row.job_id) != str(job_id) or evidence_hash(row.payload) != evidence_hash(inputs):
        raise ValueError("immutable report manifest identity conflict")
    return row


def presentation_manifest(inputs):
    slots = []
    for index, task in enumerate(inputs["tasks"]):
        source = ",".join(task.get("assertion_ids", []) + task.get("negative_assertion_ids", []))
        values = "; ".join(obj["text"] for obj in task.get("objects", []))
        slots.append(
            SlotCoverage(
                slot_id=task.get("coverage_task_id", f"target-{index}"),
                label=task["label"],
                status="filled"
                if task["status"] == "filled"
                else "missing_required"
                if task["required"]
                else "blank_optional",
                source_kind="published_snapshot",
                value=values or None,
                source_ref=source or None,
                note=(
                    f"{task['status']}: {task['reason']} · "
                    f"{task.get('subject_instance_iri') or '主体待确认'}"
                ),
            )
        )
    return CoverageManifest(
        template_id=inputs["template_id"],
        slots=slots,
        snapshot_id=inputs["snapshot_id"],
        manifest_id=inputs["manifest_id"],
        discovery_revision=inputs["discovery_revision"],
        selector_version=inputs["selector_version"],
        instance_manifest={
            k: deepcopy(v)
            for k, v in inputs.items()
            if k not in {"fact_snapshot", "semantic_schema", "template_schema", "rules"}
        },
    )


def _section_evidence(section, inputs):
    """Deterministic source projection; also the only fact input to optional prose."""
    tasks = [t for t in inputs["tasks"] if t["section_id"] == section.section_id]
    lines = []
    labels = defaultdict(list)
    for task in tasks:
        lines.append(f"{task['label']}：{task['status']}（{task['reason']}）")
        for obj in task.get("objects", []):
            lines.append(f"- {obj['text']} [{obj['instance_iri']}]")
            definition = inputs["semantic_schema"].get(obj["class_iri"], {})
            for predicate, values in obj["properties"].items():
                prop = next(
                    (p for p in definition.get("properties", []) if p["iri"] == predicate), {}
                )
                label = prop.get("label") or predicate
                for value in values:
                    text = value["literal"]["raw_value"]
                    lines.append(f"  {label}：{text} [assertion:{value['assertion_id']}]")
                    for key in [label, *prop.get("aliases", [])]:
                        labels[key].append((obj["instance_iri"], text))
        if task.get("negative_assertion_ids"):
            lines.append(
                "否定证据（仅限所列对象/时间范围）：" + ", ".join(task["negative_assertion_ids"])
            )
    return "\n".join(lines) or "本节未声明事实选择路径，不能用预览或模板示例填充。", labels


def snapshot_slot_value(source, section_id, inputs):
    """Project only objects already selected by this section's coverage manifest."""
    path = [step.model_dump() for step in source.predicate_path]
    rows, ids = {}, set()
    for task in inputs["tasks"]:
        if (
            task["section_id"] != section_id
            or task.get("root_class_iri") != source.root_class_iri
            or task.get("predicate_path") != path
            or task.get("range_class_iri") != source.range_class_iri
            or task["status"] in {"pending_review", "conflict"}
        ):
            continue
        for obj in task.get("objects", []):
            values = []
            for predicate in source.data_property_iris:
                candidates = obj["properties"].get(predicate, [])
                values.append(
                    "、".join(dict.fromkeys(v["literal"]["raw_value"] for v in candidates))
                    or "待补充"
                )
                ids.update(v["assertion_id"] for v in candidates)
            if source.text:
                values.insert(0, obj["text"])
            rows[(task["subject_instance_iri"], obj["instance_iri"])] = " / ".join(values)
            ids.update(obj["assertion_ids"])
    text = "；".join(
        f"{value} [{target}]" if len(rows) > 1 else value
        for (_, target), value in sorted(rows.items())
    )
    return text or "待补充／待审核（无匹配路径的已提交值）", sorted(ids)


def render_snapshot_report(inputs):
    from app.services.reasoning.fact_bridge import snapshot_to_facts
    from app.services.reasoning.interpreter import FALSE, TRUE, evaluate
    from app.services.reporting.docx_renderer import render_risk_report
    from app.services.reporting.risk_report_generator import (
        PENDING_LEVEL,
        RISK_LEVEL_MAP,
        RiskReport,
        RiskRow,
    )

    if not inputs.get("snapshot_id"):
        raise ValueError("report requires published facts")
    template = ReportTemplate.model_validate(inputs["template_schema"])
    manifest = presentation_manifest(inputs)
    report = RiskReport(
        doc_no=template.doc_no,
        revision=inputs["template_version"],
        source_document_name=inputs["source_filename"],
        source_document_type=inputs["input_class_iri"],
        evidence_snapshot_id=inputs["snapshot_id"],
        coverage_manifest_id=inputs["manifest_id"],
        template_version=inputs["template_version"],
        source_discovery_hash=inputs["discovery_revision"],
        selector_version=inputs["selector_version"],
        conclusion="本报告仅反映已提交证据；未完成事项、风险结论与会签须由授权人员复核。",
    )
    selector = FactSelector(inputs["fact_snapshot"], schema=inputs["semantic_schema"])
    fact_ids = {
        identity
        for task in inputs["tasks"]
        for identity in task.get("assertion_ids", [])
        if task["status"] not in {"conflict", "pending_review"}
    }
    for subject in selector.instances(inputs["input_class_iri"]):
        facts = snapshot_to_facts(selector, subject, fact_ids)
        for rule in inputs["rules"]:
            result = evaluate(rule["antecedent"], facts)
            consequent = rule["consequent"] or {}
            pre = (
                RISK_LEVEL_MAP.get(consequent.get("risk_level"), PENDING_LEVEL)
                if result is TRUE
                else "低"
                if result is FALSE
                else PENDING_LEVEL
            )
            # Planned postconditions describe controls to implement, not evidence
            # that they were implemented. No automatic post-control '低'.
            post = PENDING_LEVEL if consequent.get("postconditions") else pre
            report.assessment_rows.append(
                RiskRow(
                    hazid=consequent.get("category") or rule["id"],
                    contributing_factors=consequent.get("description") or "待评估",
                    pre_control_level=pre,
                    post_control_level=post,
                    control_measures=consequent.get("control_measure") or "待制定",
                    traceability=(
                        f"规则 {rule['id']} v{rule['version']}；"
                        f"快照 {inputs['snapshot_id']}；主体 {subject}"
                    ),
                    status="待评估"
                    if post == PENDING_LEVEL
                    else "可接受"
                    if post == "低"
                    else "不可接受",
                )
            )
    for section in template.sections:
        text, labels = _section_evidence(section, inputs)
        report.section_narratives.append(
            {
                "section_id": section.section_id,
                "title": section.title,
                "text": text,
                "source": "published_snapshot",
            }
        )
        for group in section.groups:
            for slot in group.slots:
                assertion_ids = []
                if slot.source.kind == "constant":
                    value = slot.source.value
                elif slot.source.kind == "snapshot":
                    value, assertion_ids = snapshot_slot_value(
                        slot.source, section.section_id, inputs
                    )
                elif slot.source.kind == "report_metadata":
                    value = inputs.get(slot.source.field) or "未提供元数据"
                elif slot.source.kind == "rule":
                    value = (
                        "；".join(
                            str(getattr(row, slot.source.field)) for row in report.assessment_rows
                        )
                        or "待评估"
                    )
                elif slot.source.kind == "manual":
                    value = "待授权人员填写／签署"
                else:
                    values = set(labels.get(slot.label, []))
                    value = (
                        next(iter(values))[1]
                        if len(values) == 1
                        else "待补充／待审核（无可唯一绑定的已提交值）"
                    )
                report.semantic_slots.append(
                    {
                        "slot_id": slot.slot_id,
                        "section_id": section.section_id,
                        "label": slot.label,
                        "text": value,
                        "source": "report_metadata"
                        if slot.source.kind == "report_metadata"
                        else "manual_pending"
                        if slot.source.kind == "manual"
                        else "published_snapshot",
                        "assertion_ids": assertion_ids,
                    }
                )
    if report.section_narratives and report.assessment_rows:
        matrix = [
            "\n风险评估矩阵（冻结规则；控制措施未验证时保持待评估）",
            "| 风险类型 | 风险因素 | 控制前 | 控制后 | 控制措施 | 可追溯性 | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in report.assessment_rows:
            matrix.append(
                "| "
                + " | ".join(
                    str(value).replace("|", "／").replace("\n", " ")
                    for value in (
                        row.hazid,
                        row.contributing_factors,
                        row.pre_control_level,
                        row.post_control_level,
                        row.control_measures,
                        row.traceability,
                        row.status,
                    )
                )
                + " |"
            )
        report.section_narratives[0]["text"] += "\n" + "\n".join(matrix)
    sample = inputs.get("sample_docx_path")
    if sample and (
        not Path(sample).is_file()
        or sha256(Path(sample).read_bytes()).hexdigest() != inputs["sample_sha256"]
    ):
        raise ValueError("frozen template sample changed or disappeared")
    data = render_risk_report(report, manifest, template=template, sample_docx_path=sample)
    return report, manifest, data
