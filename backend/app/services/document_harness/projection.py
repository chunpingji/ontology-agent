"""Read-only display of the independent harness's current business state."""

from __future__ import annotations

from collections import defaultdict

from app.models.document_analysis import DocumentAnalysisArtifact
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_harness.application import ENGINE, HarnessError, require_harness
from app.services.document_harness.coreference import project_coreferences
from app.services.document_harness.interpretations import interpretation_tasks
from app.services.document_harness.runtime import call_summaries, read_rows
from app.services.extraction.document_ir import DocumentIR

FIELDS = {
    "entities": ("id", "label", "role", "class_iri", "class_label", "state", "reason", "evidence"),
    "properties": (
        "id", "subject_id", "predicate_iri", "label", "value", "state", "reason", "evidence",
        "source_value", "source_unit", "value_component", "value_evidence", "field_id",
    ),
    "relations": (
        "id", "subject_id", "object_id", "predicate_iri", "label", "state", "reason",
        "evidence", "polarity", "conditions",
    ),
    "relation_groups": (
        "id", "subject_id", "object_ids", "predicate_iri", "label", "state", "reason",
        "evidence", "polarity", "conditions", "participation", "selection", "timing",
        "timing_state", "timing_reason",
    ),
    "observations": ("id", "label", "reason", "evidence", "field_id"),
}


def card_ref(classes, iri):
    if not iri:
        return None
    return {"iri": iri, "label": classes.get(iri, {}).get("label") or iri}


def _domain_text(expression, classes):
    kind = expression.get("kind")
    if kind == "named":
        iri = expression["iri"]
        return f"{classes.get(iri, {}).get('label') or iri} <{iri}>"
    if kind in {"union", "intersection"}:
        joiner = " 或 " if kind == "union" else " 且 "
        return "(" + joiner.join(
            _domain_text(part, classes) for part in expression.get("operands", [])
        ) + ")"
    return "未解析的定义域约束"


def predicate_ref(classes, iri, class_iri=None):
    if not iri:
        return None
    cards = [classes.get(class_iri, {})] if class_iri else classes.values()
    definition = next((predicate for card in cards
                       for key in ("properties", "relations") for predicate in card.get(key, [])
                       if predicate["iri"] == iri), {})
    separator = "#" if "#" in iri else "/" if "/" in iri else ":"
    namespace = iri.rsplit(separator, 1)[0] + separator
    return {
        "iri": iri, "label": definition.get("label") or iri,
        "namespace": namespace,
        "domain_text": " 且 ".join(
            _domain_text(part, classes) for part in definition.get("domain", [])
        ) or "未声明全局定义域或当前卡未提供定义",
    }


def observation_context(row, state, catalog, properties_by_field, owners_by_field):
    classes = catalog.get("classes", {})
    field_id = row.get("field_id")
    owners = list(dict.fromkeys([
        *row.get("candidate_subject_ids", []),
        *(owners_by_field.get(field_id, []) if field_id else []),
    ]))
    if row.get("subject_id") and row["subject_id"] not in owners:
        owners.append(row["subject_id"])
    groups = {}

    def group(subject_id, class_iri):
        return groups.setdefault((subject_id, class_iri), {
            "subject_id": subject_id, "card": card_ref(classes, class_iri),
            "property_ids": [], "attempts": [],
        })

    for prop in properties_by_field.get(field_id, []) if field_id else []:
        entry = group(prop["subject_id"], prop.get("alignment_class_iri"))
        entry["property_ids"].append(prop["id"])
        if prop["subject_id"] not in owners:
            owners.append(prop["subject_id"])
    for attempt in row.get("alignment_outcomes", {}).values():
        subject_id, class_iri = attempt["subject_id"], attempt["class_iri"]
        group(subject_id, class_iri)["attempts"].append({
            "state": attempt["state"], "reason": attempt["reason"],
            "predicates": [predicate_ref(classes, iri, class_iri)
                           for iri in attempt["predicate_iris"]],
        })
        if subject_id not in owners:
            owners.append(subject_id)
    guidance = state.get("windows", {}).get(
        row.get("discovery_window_id", row.get("window_id")), {},
    )
    discovery_cards = []
    if "guidance_class_iris" in guidance:
        root = card_ref(classes, catalog.get("root_class_iri"))
        if root:
            discovery_cards.append({**root, "role": "document_properties"})
        discovery_cards.extend(
            {**card_ref(classes, iri), "role": "reading"}
            for iri in guidance["guidance_class_iris"]
        )
    return {
        "kind": row.get("kind") or ("field" if field_id else "validation"),
        "value": state.get("fields", {}).get(field_id, {}).get("value"),
        "candidate_subject_ids": owners, "object_id": row.get("object_id"),
        "discovery_cards": discovery_cards, "alignments": list(groups.values()),
    }


def _catalog(db, run):
    ref = DocumentAnalysisRunStore(db).get_artifact(
        run.recognition_run_id, run.owner_id, "ontology_snapshot",
    )
    artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id) if ref else None
    return artifact.payload if artifact else {"classes": {}}


def graph_response(db, run):
    require_harness(db, run)
    state = read_rows(db, run, domains={*FIELDS, "cursor", "fields", "windows", "coreferences"})
    catalog = _catalog(db, run)
    classes = catalog.get("classes", {})
    properties_by_field, owners_by_field = defaultdict(list), defaultdict(list)
    for prop in state.get("properties", {}).values():
        properties_by_field[prop.get("field_id")].append(prop)
    for entity in state.get("entities", {}).values():
        for field_id in entity.get("field_ids", []):
            owners_by_field[field_id].append(entity["id"])
    calls = call_summaries(db, run)
    cursor = state.get("cursor", {}).get("main", {})
    costs = {}
    for call in calls.values():
        stage = call["stage"]
        entry = costs.setdefault(stage, {
            "stage": stage, "calls": 0, "seconds": 0, "input_tokens": 0, "output_tokens": 0,
        })
        entry["calls"] += call.get("attempts") or 1
        entry["seconds"] += call.get("seconds") or 0
        usage = call.get("usage") or {}
        for name, alternate in (("input_tokens", "prompt_tokens"),
                                ("output_tokens", "completion_tokens")):
            measured = usage.get(name, usage.get(alternate))
            if (call.get("unmeasured_attempts") or type(measured) is not int
                    or measured < 0 or entry[name] is None):
                entry[name] = None
            else:
                entry[name] += measured
    result = {
        "protocol": ENGINE, "run_id": str(run.recognition_run_id), "revision": run.revision,
        "status": run.execution_status, "stage": cursor.get("stage", run.stage),
        "progress": {
            "completed_calls": sum(call.get("status") == "completed" for call in calls.values()),
            "candidate_count": sum(
                row.get("role") != "document_root"
                for kind in ("entities", "properties", "relations", "relation_groups")
                for row in state.get(kind, {}).values()
            ),
            "fact_count": sum(
                row.get("state") == "accepted" and row.get("role") != "document_root"
                for kind in ("entities", "properties", "relations", "relation_groups")
                for row in state.get(kind, {}).values()
            ),
            "windows_total": cursor.get("windows_total", 0),
            "windows_discovered": cursor.get("windows_discovered", 0),
            "windows_reviewed": cursor.get("windows_reviewed", 0),
            "scope_complete": cursor.get("scope_complete", False),
            "stage_costs": list(costs.values()),
        },
    }
    for kind, fields in FIELDS.items():
        result[kind] = []
        for row in state.get(kind, {}).values():
            item = {name: row.get(name) for name in fields}
            evidence = []
            for ref in [*(row.get("evidence") or []), *(row.get("review_evidence") or [])]:
                if ref not in evidence:
                    evidence.append(ref)
            item["evidence"] = evidence
            if kind in {"properties", "relations", "relation_groups"}:
                item["card"] = card_ref(classes, row.get("alignment_class_iri"))
                item["predicate"] = predicate_ref(
                    classes, row.get("predicate_iri"), row.get("alignment_class_iri"),
                )
            if kind == "properties":
                # Rows created before exact value spans were introduced do not
                # have this field. Absence means no saved value-specific quote;
                # it must not become JSON null or inherit broader claim evidence.
                item["value_evidence"] = row.get("value_evidence") or []
            if kind == "observations":
                item.update(observation_context(
                    row, state, catalog, properties_by_field, owners_by_field,
                ))
            result[kind].append(item)
    for key, call in calls.items():
        if call.get("status") == "failed":
            result["observations"].append({
                "id": f"call-failure:{key}", "label": f"{call['stage']} 调用失败",
                "reason": call.get("error") or "model_request_failed", "evidence": [],
                "field_id": None, "value": None, "candidate_subject_ids": [],
                "kind": "failure", "object_id": None, "discovery_cards": [], "alignments": [],
            })
    result["interpretation_tasks"] = interpretation_tasks(db, run, state)
    project_coreferences(result, list(state.get("coreferences", {}).values()), classes)
    targets = []
    for entity in result["entities"]:
        card = classes.get(entity.get("class_iri"))
        if card is None or entity["state"] == "rejected":
            continue
        for kind, key in (("property", "properties"), ("relation", "relations")):
            for predicate in card.get(key, []):
                if predicate.get("constraint_status") != "resolved":
                    continue
                claims = [item for item in result[key] if item["subject_id"] == entity["id"]
                          and item["predicate_iri"] == predicate["iri"]]
                if kind == "relation":
                    claims.extend(item for item in result["relation_groups"]
                                  if item["subject_id"] == entity["id"]
                                  and item["predicate_iri"] == predicate["iri"])
                targets.append({
                    "id": content_hash([entity["id"], kind, predicate["iri"]]),
                    "subject_id": entity["id"], "predicate_iri": predicate["iri"],
                    "label": predicate["label"], "kind": kind,
                    "range_labels": [classes.get(iri, {}).get("label", iri)
                                     for iri in predicate.get("range_class_iris", [])],
                    "state": ("accepted" if any(x["state"] == "accepted" for x in claims)
                              else "candidate" if any(x["state"] in {"candidate", "unresolved"}
                                                      for x in claims) else "pending"),
                })
    result["targets"] = targets
    return result


def source_response(db, run, source_id):
    require_harness(db, run)
    stored = read_rows(db, run, domains={"input"}).get("input", {}).get("document")
    if stored is None:
        raise HarnessError("SOURCE_NOT_READY", "原文尚未解析", status_code=409, retryable=True)
    ir = DocumentIR.model_validate(stored)
    try:
        unit = ir.unit(source_id)
    except ValueError as exc:
        raise HarnessError("SOURCE_NOT_FOUND", "来源不属于当前运行", status_code=404) from exc
    return {
        "source_id": unit.evidence_id, "text": unit.text, "page": unit.physical_page_number,
        "section_id": unit.section_node_id, "block_id": unit.block_id,
        "row": unit.row_index, "column": unit.column_index,
    }
