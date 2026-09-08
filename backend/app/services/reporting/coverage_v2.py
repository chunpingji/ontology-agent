"""Evidence preparation consumes the same compiled V2 requirements as reports."""

from copy import deepcopy

from app.models.evidence import EvidenceJobState
from app.models.reporting import ReportInputSnapshot
from app.services.extraction.candidate_store import CandidateStore
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.performance import measure, tracking
from app.services.extraction.template_extraction_plan import discovery_revision
from app.services.reporting.input_resolver import PRIORITY
from app.services.reporting.legacy_facade import require_single_source, selected_template
from app.services.reporting.report_run_service import ReportRunService, schema_hash, verify_frozen
from app.services.reporting.template_compiler import Compiler, require_valid
from app.services.reporting.template_v2 import ReportingError, Step

_CONFIGURATION_ERRORS = {
    "TEMPLATE_NOT_FOUND": "未找到报告模板，请选择并配置模板后重试覆盖检查。",
    "TEMPLATE_MIGRATION_REQUIRED": "报告模板尚未迁移到 V2，请先迁移并完成契约配置。",
    "CONTRACT_NOT_FOUND": "模板引用的报告契约不存在，请在模板设置中补齐契约引用。",
    "CONTRACT_NOT_PUBLISHED": "模板引用的报告契约尚未发布，请完成契约审核与发布。",
    "TEMPLATE_COMPILATION_FAILED": "报告模板校验未通过，请在模板设置中修复校验问题。",
    "EXPLICIT_SOURCE_BINDINGS_REQUIRED": "该模板需要多个来源，请在报告预览中逐项选择来源。",
}


def build_evidence_coverage(db, job, *, schema, actor, template_id=None, snapshot_id=None):
    """Unavailable template configuration does not block independent evidence review."""
    template = None
    try:
        _, template = selected_template(db, job.id, template_id)
        return build_report_inputs(
            db, job, schema=schema, actor=actor, template_id=template.id, snapshot_id=snapshot_id
        )
    except ReportingError as exc:
        if exc.code not in _CONFIGURATION_ERRORS:
            raise
        selected_ref = (
            template.id
            if template
            else exc.detail.get("template_id")
            or template_id
            or (job.source_config or {}).get("template_id")
        )
        return {
            "availability": "unavailable",
            "template_id": str(selected_ref) if selected_ref else None,
            "template_version": template.version if template else None,
            "manifest_id": None,
            "required_gaps": None,
            "error": {**exc.detail, "message": _CONFIGURATION_ERRORS[exc.code]},
        }


def build_report_inputs(db, job, *, schema, actor, template_id=None, snapshot_id=None):
    with tracking() as performance:
        with measure("coverage_prepare"):
            result = _build_report_inputs(db, job, schema=schema, actor=actor,
                                          template_id=template_id, snapshot_id=snapshot_id)
        result["performance"] = performance.snapshot()
        return result


def _build_report_inputs(db, job, *, schema, actor, template_id=None, snapshot_id=None):
    from app.services.reasoning.calculation_review import decision_history

    _, template = selected_template(db, job.id, template_id)
    slots = template.schema_json.get("source_slots", [])
    require_single_source(slots)
    state = db.get(EvidenceJobState, job.id, populate_existing=True)
    candidates = CandidateStore(db).list(job.id)
    extraction = deepcopy(state.extraction_run or {}) if state else {}
    revision = discovery_revision(candidates, extraction)
    service = ReportRunService(db, model_schema=schema)
    prepared = service.compile(template.schema_json, actor, template.id)
    plan, _, _ = prepared
    require_valid(plan)
    key = evidence_hash(
        [
            str(job.id),
            str(template.id),
            schema_hash(template.schema_json),
            snapshot_id or (state.snapshot_id if state else None),
            revision,
            plan["compilation_id"],
            actor,
            extraction.get("discovery_decisions", {}),
            evidence_hash(decision_history(db, job.id)),
        ]
    )
    slots = template.schema_json.get("source_slots", [])
    run, created = service.start(
        {
            "template_id": str(template.id),
            "idempotency_key": key,
            "mode": "data",
            "source_bindings": {
                slots[0]["source_slot_id"]: {
                    "job_id": str(job.id),
                    "snapshot_id": snapshot_id,
                }
            }
            if slots
            else {},
            "purpose": "draft",
        },
        actor,
        preview=True,
        _prepared=prepared,
        _source_inputs={str(job.id): {"revision": state.revision if state else 0,
                                     "run": extraction, "candidates": candidates}},
    )
    db.commit()
    reused = not created and bool(run.input_snapshot_id)
    if created or not run.input_snapshot_id:
        with measure("coverage_execute"):
            service.execute(run.id)
    with measure("coverage_read"):
        inputs = verify_frozen(db.get(ReportInputSnapshot, run.input_snapshot_id))
    bundle = inputs["source_bundle"]
    source = next((v for v in bundle["sources"].values() if v.get("job_id") == str(job.id)), {})
    snapshot = source.get("snapshot")
    tasks = coverage_tasks(inputs, job.id, plan["compilation_id"])
    calculation_issues = [
        problem
        for check in inputs.get("calculation_checks", {}).values()
        for problem in check["issues"]
    ]
    tasks.extend(
        {
            "target_id": p["issue_id"],
            "label": "PDE 计算校验：" + p["message"],
            "status": p["state"],
            "reason": p["code"],
            "subject_instance_iri": None,
            "required": True,
            "assertion_ids": [],
        }
        for p in calculation_issues
    )
    return {
        **inputs,
        "reused": reused,
        "availability": "available",
        "manifest_id": run.input_snapshot_id,
        "run_id": run.id,
        "template_hash": schema_hash(template.schema_json),
        "template_id": str(template.id),
        "template_version": template.version,
        "template_schema": bundle["template"],
        "fact_snapshot": snapshot,
        "semantic_schema": bundle["schema"],
        "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
        "discovery_revision": revision,
        "input_class_iri": slots[0]["class_iri"] if slots else "",
        "tasks": tasks,
        "required_gaps": sum(not requirement["satisfied"] for requirement in inputs["coverage"])
        + len(calculation_issues),
        "completion": "complete" if inputs["material_status"] == "ready" else "incomplete",
        "diagnostics": list(dict.fromkeys(issue["code"] for issue in inputs["blocking_issues"])),
        "rules": [],
    }


def coverage_tasks(inputs, job_id, compilation_id):
    """Map frozen selection traces to preparation tasks; never resolve facts again."""
    bundle = inputs["source_bundle"]
    definitions = bundle["template"]["definitions"]
    compiler = Compiler(bundle["template"], bundle["schema"], bundle["contracts"])
    tasks = []
    for scope_id, scope in inputs["scopes"].items():
        for input_id, value in scope["inputs"].items():
            definition = definitions["inputs"][input_id]
            binding = definitions["bindings"][definition["binding_ref"]]
            if binding["kind"] != "facts":
                continue
            binding_scope = binding["scope"]
            source = bundle["sources"].get(binding_scope["source_slot"], {})
            if source.get("job_id") != str(job_id):
                continue
            entities = {
                record["subject_iri"]: record["candidate"]
                for record in (source.get("snapshot") or {}).get("assertions", [])
                if record["candidate"]["kind"] == "entity"
            }
            applicable_at = binding_scope.get("applicable_at") or bundle.get("applicable_at")

            def append_task(
                trace, classes, problems, *, required, field_path=(), property_iri=None
            ):
                states = {problem["state"] for problem in problems}
                status = next((state for state in PRIORITY if state in states), "filled")
                discovery = trace.get("discovery") or {}
                if status == "filled" and discovery.get("status") != "complete":
                    status = "incomplete"
                roots = trace.get("roots", [])
                subjects = trace.get("subjects", [])
                properties = [property_iri] if property_iri else []
                objects = [
                    {
                        "instance_iri": subject,
                        "class_iri": entities.get(subject, {}).get("class_iri"),
                        "text": entities.get(subject, {}).get("text", ""),
                        "missing_properties": properties if status != "filled" else [],
                        "assertion_ids": trace.get("support", {}).get(subject, []),
                    }
                    for subject in subjects
                ]
                for root in roots or [None]:
                    for result_class in classes:
                        task = {
                            "label": " / ".join([definition["label"] or input_id, *field_path]),
                            "execution_scope_id": scope_id,
                            "input_id": input_id,
                            "source_slot": binding_scope["source_slot"],
                            "cross_source": bool(binding_scope.get("fact_source_refs")),
                            "subject_instance_iri": root,
                            "root_class_iri": entities.get(root, {}).get("class_iri")
                            or binding["contract_ref"]["root_class_iri"],
                            "range_class_iri": result_class,
                            "predicate_path": trace.get("predicate_path", []),
                            "field_path": list(field_path),
                            "required_properties": properties,
                            "required": required,
                            "status": status,
                            "objects": objects,
                            "object_universe_status": discovery.get("status", "unknown"),
                            "reason": "object_universe_open"
                            if discovery.get("status") == "open"
                            else "input_requirement_unmet",
                            "snapshot_id": (source.get("snapshot") or {}).get("snapshot_id"),
                            "issue_refs": [problem["issue_id"] for problem in problems],
                            "report_selector_key": evidence_hash(
                                {
                                    "root": root,
                                    "path": trace.get("predicate_path", []),
                                    "applicable_at": applicable_at,
                                }
                            ),
                        }
                        task["coverage_task_id"] = evidence_hash([compilation_id, task])
                        tasks.append(task)

            selected = scope.get("binding_scopes", {}).get(definition["binding_ref"], {})
            append_task(
                {**selected, "predicate_path": binding_scope["predicate_path"]},
                [binding["contract_ref"]["result_class_iri"]],
                selected.get("issues", []),
                required=definition["required"],
            )

            def project_tasks(projection, node, base_classes, required, field_path=()):
                classes = compiler.path_types(
                    base_classes,
                    [Step.model_validate(step) for step in projection.get("predicate_path", [])],
                    "coverage",
                )
                if projection.get("class_iri"):
                    classes = [projection["class_iri"]]
                trace = node.get("derivation", {}).get("selection")
                if trace and (projection.get("predicate_path") or projection["kind"] == "property"):
                    append_task(
                        trace,
                        classes,
                        node.get("issues", []),
                        required=required,
                        field_path=field_path,
                        property_iri=projection.get("property_iri"),
                    )
                rows = (
                    node.get("items", [])
                    if projection["kind"] in {"records", "entities"}
                    else [node]
                )
                for row in rows:
                    for name, field in projection.get("fields", {}).items():
                        child = row.get("fields", {}).get(name)
                        if child:
                            project_tasks(
                                field["value"],
                                child,
                                classes,
                                field.get("required", False),
                                (*field_path, name),
                            )

            project_tasks(
                definition["projection"],
                value,
                [binding["contract_ref"]["result_class_iri"]],
                definition["required"],
            )
    return tasks
