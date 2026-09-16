"""Transient Section prose over the selected engine's existing, owned source results."""

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

from app.services.extraction.evidence_identity import canonical_json
from app.services.reporting.section_narrative import (
    CUSTOM_PROMPT_REF,
    draft_value,
    write_preview,
)
from app.services.reporting.template_v2 import ReportingError, Section, TemplateV2


def load_source(db, engine, actor, row, job):
    from app.services.document_analysis.application import (
        DocumentAnalysisApplication,
        DocumentAnalysisError,
    )
    from app.services.document_analysis.report_documents import ReportDocumentRuns
    from app.services.document_analysis.template_runs import TemplateDocumentRuns
    from app.services.reporting.demo_sources import load_finder
    from app.services.template_finder.policy import resolve
    from app.services.template_finder.service import _file_hash

    if (job.source_config or {}).get("document_role") in {
        "template_sample", "training_source", "training_report",
    }:
        raise ReportingError("SOURCE_ROLE_INVALID", "样例不能作为行文事实来源")
    if resolve(row):
        result = load_finder(db, engine, row, job, actor, {})
        if result["state"] != "ready":
            raise ReportingError(result["code"], "请先在源文档完成本体指引1.0识别", status=409)
        return result
    application = DocumentAnalysisApplication(db, ontology_engine=engine)
    try:
        run = TemplateDocumentRuns(application).latest(actor, row.id, job.id)
        if run is None and (job.source_config or {}).get("doc_ref"):
            run = ReportDocumentRuns(application).latest(actor, job.source_config["doc_ref"])
        if run is None:
            raise ReportingError(
                "GRAPH_RESULT_UNAVAILABLE", "请先在源文档完成关系图谱识别", status=409,
            )
        run = application.get_run(run.recognition_run_id, actor)
        application.assert_artifacts_readable(run)
        if (job.document_path and Path(job.document_path).is_file()
                and run.document_hash != _file_hash(Path(job.document_path))):
            raise ReportingError("GRAPH_RESULT_STALE", "原件已更新，请重新识别", status=409)
        graph = application.graph_response(run, projection="effective_affirmed")
    except DocumentAnalysisError as exc:
        raise ReportingError(exc.code, exc.message, status=exc.status_code) from exc
    if graph["availability"] not in {"ready", "partial"} or not graph["graph_snapshot"]:
        raise ReportingError("GRAPH_RESULT_UNAVAILABLE", "尚无可预览的识别结果", status=409)
    return {"kind": "ontology_guided", "state": "ready", "job_id": str(job.id),
            "execution_id": str(run.recognition_run_id), "source_filename": job.source_filename,
            "graph": graph}


def section_input_ids(section):
    from app.services.reporting.template_compiler import walk_groups

    refs = [use.input_ref for group, _ in walk_groups(section.groups)
            for unit in group.units for use in unit.inputs]
    # An explicit selection remains meaningful, but is no longer a readiness gate.
    if section.narrative and section.narrative.input_refs:
        refs = [ref.input_id for ref in section.narrative.input_refs]
    return list(dict.fromkeys(refs))


def resolved_fields(db, engine, schema, section, source):
    """Use the existing compiler/resolver, independently of unrelated output gaps."""
    from app.services.extraction.extraction_tasks import semantic_schema_from_engine
    from app.services.reporting.demo_sources import builtin_contract, load_mock
    from app.services.reporting.input_resolver import ResolvedValue
    from app.services.reporting.report_run_service import ReportRunService
    from app.services.reporting.report_snapshot import resolve_snapshot
    from app.services.reporting.template_compiler import compile_template

    if not section_input_ids(section):
        return []
    service = ReportRunService(db, model_schema=lambda: semantic_schema_from_engine(engine))
    prepared = service.prepare(schema)
    ontology, contracts = service.load_contracts(prepared)
    contracts[CUSTOM_PROMPT_REF] = builtin_contract(CUSTOM_PROMPT_REF)
    documents = [slot for slot in prepared.source_slots if slot.kind == "document"]
    sources = {}
    if len(documents) == 1 and source["kind"] == "finder_demo":
        if documents[0].class_iri != source["root_class_iri"]:
            raise ReportingError("SOURCE_TYPE_MISMATCH", "文档类型与模板来源不匹配")
        sources[documents[0].source_slot_id] = source
    fields = []
    for input_id in section_input_ids(section):
        definition = prepared.definitions.inputs.get(input_id)
        label = (definition.label or definition.name or input_id) if definition else input_id
        item = {"input_id": input_id, "label": label, "value": draft_value(None)}
        local = prepared.model_copy(deep=True)
        local.sections = [Section.model_validate({
            "section_id": section.section_id, "title": section.title, "groups": [],
            "narrative": {"enabled": True, "instructions": "预览读取本节输入",
                          "policy_ref": CUSTOM_PROMPT_REF, "input_refs": [{"input_id": input_id}],
                          "required_refs": [], "claim_refs": []},
        })]
        # These are report-level requirements, not prerequisites for design-time prose.
        local.calculation_checks = []
        plan = compile_template(local, ontology, contracts)
        if not plan["valid"]:
            item["issues"] = [d["code"] for d in plan["diagnostics"] if d["severity"] == "error"]
            fields.append(item)
            continue
        try:
            records = {}
            for node in plan["node_order"]:
                kind, ref = node.split(":", 1)
                if kind != "binding":
                    continue
                binding = local.definitions.bindings[ref]
                slot = binding.scope.record_slot if binding.kind == "context" else None
                if slot in local.record_sources and slot not in records:
                    records[slot] = load_mock(
                        db, local.record_sources[slot], local.budget.max_records,
                    )
            snapshot = resolve_snapshot(plan, {
                "template": plan["template"], "schema": ontology, "contracts": contracts,
                "sources": sources, "records": records, "demonstration": True,
            })
            value = ResolvedValue.model_validate(snapshot["inputs"][input_id])
            item["value"] = draft_value(value)
            item["source"] = value.derivation.get(
                "source_kind", local.definitions.bindings[definition.binding_ref].kind,
            )
        except ReportingError as exc:
            item["issues"] = [exc.code]
        fields.append(item)
    return fields


def scoped_guided_graph(graph, template, section):
    """Limit a graph excerpt to the Section's declared fact paths, preserving edge roles."""
    refs = section_input_ids(section)
    if not refs:
        return graph
    selected, edges = set(), []
    documents = [s for s in template.source_slots if s.kind == "document"]
    for ref in refs:
        definition = template.definitions.inputs.get(ref)
        binding = template.definitions.bindings.get(definition.binding_ref) if definition else None
        if binding is None or binding.kind != "facts":
            continue
        scope = binding.scope
        if (len(documents) != 1 or scope.source_slot != documents[0].source_slot_id
                or scope.root.kind not in {"source_root", "entity_ref"}
                or scope.fact_source_refs or scope.condition_refs):
            continue
        frontier = {entity["entity_id"] for entity in graph["entities"] if (
            entity["seed_origin"] == "user_selected" if scope.root.kind == "source_root"
            else entity["entity_id"] == scope.root.entity_id
        )}
        routes = {entity_id: set() for entity_id in frontier}
        for step in scope.predicate_path:
            following = defaultdict(set)
            for edge_index, edge in enumerate(graph["relationships"]):
                if edge["predicate_iri"] != step.predicate_iri:
                    continue
                subject, target = edge["subject_ref"]["entity_id"], edge["object_ref"]["entity_id"]
                if edge["direction"] == "object_to_subject":
                    subject, target = target, subject
                if step.direction == "inverse":
                    subject, target = target, subject
                if subject in frontier:
                    following[target].update(routes[subject] | {edge_index})
            routes = following
            frontier = set(following)
        if scope.object_ids is not None:
            frontier &= set(scope.object_ids)
        if (scope.cardinality.max_count is not None
                and len(frontier) > scope.cardinality.max_count):
            continue
        selected.update(frontier)
        edges.extend(graph["relationships"][index] for entity_id in frontier
                     for index in sorted(routes[entity_id]))
    visible = selected | {e[role]["entity_id"] for e in edges
                          for role in ("subject_ref", "object_ref")}
    return {**graph, "entities": [e for e in graph["entities"] if e["entity_id"] in visible],
            "properties": [p for p in graph["properties"]
                           if p["subject_ref"]["entity_id"] in selected],
            "relationships": list({canonical_json(e): e for e in edges}.values())}


def graph_context(source, template=None, section=None):
    """Bounded graph excerpts keep occurrences and conditions, never sample text."""
    if source["kind"] == "ontology_guided":
        graph = source["graph"]
        if template is not None:
            graph = scoped_guided_graph(graph, template, section)
        return {key: graph[key] for key in ("entities", "properties", "relationships", "coverage")}
    remaining = 120

    def visit(edges, depth=0):
        nonlocal remaining
        result = []
        for edge in edges:
            if remaining <= 0 or depth > 4:
                result.append({"note": "更多内容未列出（部分数据）"})
                break
            remaining -= 1
            item = {key: value for key, value in edge.items() if key in {
                "predicate_iri", "predicate_label", "subject_text", "subject_class_iri",
                "object_text", "object_class_iri", "object_class_label", "source",
                "polarity", "conditions", "direction",
            }}
            properties = defaultdict(list)
            for prop in edge.get("object_data_properties", []):
                properties[prop.get("iri")].append(prop)
            item["properties"] = []
            for rows in properties.values():
                values = {canonical_json(p.get("value")) for p in rows}
                item["properties"].append({
                    "iri": rows[0].get("iri"), "label": rows[0].get("label"),
                    "value": rows[0].get("value") if len(values) == 1 else "（待补充：冲突）",
                })
            item["relationships"] = visit(edge.get("sub_relationships", []), depth + 1)
            result.append(item)
        return result

    return {"relationships": visit(source["relationships"]), "coverage": "已有部分识别结果"}


def variables_from_fields(fields):
    grouped = defaultdict(list)
    for field in fields:
        value = field["value"]
        grouped[field["label"]].append(value.get("value", canonical_json(value)))
    # Even equal values on different subjects must not make an ambiguous label unique.
    return {label: values[0] if len(values) == 1 else "（待补充：字段名不唯一）"
            for label, values in grouped.items()}


def variables_from_graph(graph):
    """Legacy label fallback only when a property has one unambiguous occurrence."""
    fields = []

    def visit(value):
        if isinstance(value, dict):
            label = value.get("predicate_label") or value.get("label")
            if label and ("raw_value" in value or "value" in value):
                raw = value.get("raw_value", value.get("value"))
                # Keep conditional properties contextual, never substitute them as unconditional.
                if not value.get("conditions"):
                    fields.append({"label": label, "value": {"value": raw}})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(graph)
    return variables_from_fields(fields)


def preview_section(db, engine, actor, request):
    from app.services.template_finder.service import source as registered_source

    if not request.prompt.strip():
        raise ReportingError("PROMPT_REQUIRED", "请先填写本节行文 Prompt", status=400)
    row, job = registered_source(db, request.template_id, request.job_id)
    schema = deepcopy(request.draft_schema if request.draft_schema is not None else row.schema_json)
    if (row.schema_json or {}).get("demo_profile") or schema.get("demo_profile"):
        raise ReportingError("STATIC_DEMO_TEMPLATE", "请使用静态演示模板的预览入口", status=409)
    section = next((s for s in schema.get("sections", [])
                    if s.get("section_id") == request.section_id), None)
    if section is None:
        raise ReportingError("SECTION_NOT_FOUND", "章节不存在，请刷新模板", status=404)
    selected = load_source(db, engine, actor, row, job)
    fields, warnings, template = [], [], None
    if schema.get("schema_version") == 2:
        try:
            template = TemplateV2.model_validate(schema)
        except ValidationError as exc:
            raise ReportingError("TEMPLATE_INVALID", "模板结构无效，请检查当前编辑内容") from exc
        section = next(s for s in template.sections if s.section_id == request.section_id)
        fields = resolved_fields(db, engine, template, section, selected)
        title = section.title
    else:
        title = section.get("title", "")
    context = {"section_title": "文档正文" if title == "upload" else title,
               "source_filename": job.source_filename, "fields": fields}
    # Bound Finder fields are authoritative for this Section. Raw graph fallback
    # must not bypass a conflicting field, an explicit selector, or a Mock filter.
    if not fields or selected["kind"] == "ontology_guided":
        context["graph"] = graph_context(selected, template, section)
    if any("待补充" in canonical_json(f) or '"coverage":"open"' in canonical_json(f)
           or '"coverage":"unknown"' in canonical_json(f) for f in fields):
        warnings.append("本节含缺失、冲突或部分集合；已用可用内容生成，缺口保留待补充。")
    if selected["kind"] == "finder_demo":
        warnings.append("本体指引1.0识别结果用于演示草稿，请核对原文；Mock 名册不代表签署。")
    variables = variables_from_graph(context.get("graph", {}))
    variables.update(variables_from_fields(fields))
    narrative = write_preview(request.prompt, context, variables)
    return {"narrative": narrative, "warnings": warnings,
            "source": {key: selected[key] for key in (
                "kind", "job_id", "execution_id", "source_filename",
            )}}
