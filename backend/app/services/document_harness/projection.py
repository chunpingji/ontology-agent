"""Read-only display of the independent harness's current business state."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisRun,
    DocumentRunCurrentState,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    RunNotFound,
    content_hash,
)
from app.services.document_harness.accounting import stage_costs
from app.services.document_harness.application import ENGINE, HarnessError, require_harness
from app.services.document_harness.coreference import project_coreferences
from app.services.document_harness.interpretations import _candidates, attach_interpretation_answers
from app.services.document_harness.runtime import read_rows
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


def build_graph_base(state, catalog):
    """Project business state once per public change; never read the call ledger."""
    classes = catalog.get("classes", {})
    properties_by_field, owners_by_field = defaultdict(list), defaultdict(list)
    for prop in state.get("properties", {}).values():
        properties_by_field[prop.get("field_id")].append(prop)
    for entity in state.get("entities", {}).values():
        for field_id in entity.get("field_ids", []):
            owners_by_field[field_id].append(entity["id"])
    result = {}
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
                item["verification"] = {name: (row.get("verification") or {}).get(name)
                                        for name in ("method", "rule_id", "rule_version",
                                                     "semantic_verdict")}
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
    result["task_seeds"] = _candidates(None, state)
    result["candidate_work"] = [candidate_work(row) for row in state.get("work", {}).values()
                                if row.get("kind") in {"relation_alignment", "coreference_review"}]
    project_coreferences(result, [
        {key: row[key] for key in ("id", "left_mention_id", "right_mention_id", "verdict", "basis",
                                  "reason", "evidence", "proof")}
        for row in state.get("coreferences", {}).values()
    ], classes)
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



def candidate_work(row):
    seed = row.get("input") or row
    return {
        "id": row["id"], "kind": row["kind"],
        "subject_id": seed.get("subject_id") or seed.get("left_mention_id"),
        "object_ids": seed.get("object_ids", [seed["right_mention_id"]]
                                   if seed.get("right_mention_id") else []),
        "predicate_iri": seed.get("predicate_iri"), "status": row["status"],
        "reason_code": row.get("reason_code"),
        "evidence": seed.get("clue_refs", seed.get("evidence", [])),
    }


def read_graph_bundle(db, run_id, owner_id):
    """Read the current head and three small rows from one SQL snapshot."""
    aliases = [aliased(DocumentRunCurrentState) for _ in range(3)]
    query = select(DocumentAnalysisRun, *aliases).where(
        DocumentAnalysisRun.recognition_run_id == run_id,
        DocumentAnalysisRun.owner_id == owner_id,
        DocumentAnalysisRun.deletion_state != "deleted",
    )
    for row, domain, key in zip(aliases, ("metrics", "display", "cursor"),
                                ("main", "graph", "main")):
        query = query.outerjoin(row, and_(
            row.recognition_run_id == DocumentAnalysisRun.recognition_run_id,
            row.domain == "harness:" + domain, row.business_key == content_hash(key),
        ))
    bundle = db.execute(query.execution_options(populate_existing=True)).one_or_none()
    if bundle is None:
        raise RunNotFound(run_id=str(run_id))
    run, *rows = bundle
    values = []
    for row in rows:
        if row is not None and content_hash(row.payload) != row.content_hash:
            raise ValueError("harness_current_row_hash_mismatch")
        values.append(deepcopy(row.payload["value"]) if row else None)
    metrics, display, cursor = values
    if not metrics or not display or display.get("work_version") != run.work_version:
        raise HarnessError("HARNESS_DISPLAY_NOT_READY", "展示结果尚未就绪，请稍后重试",
                           status_code=409, retryable=True)
    return run, metrics, display["base"], cursor or {}


def graph_response(db, run):
    require_harness(db, run)
    run, metrics, base, cursor = read_graph_bundle(db, run.recognition_run_id, run.owner_id)
    result = deepcopy(base)
    result["interpretation_tasks"] = attach_interpretation_answers(
        db, run, result.pop("task_seeds"),
    )
    reading = cursor.get("reading") or {
        "total_characters": 0, "processed_characters": 0, "complete_characters": 0,
        "complete": cursor.get("scope_complete", False),
    }
    if "complete_characters" not in reading:
        raise HarnessError("HARNESS_NEW_RUN_REQUIRED", "该运行使用旧阅读协议，请新建运行")
    result.update({
        "protocol": ENGINE, "run_id": str(run.recognition_run_id), "revision": run.revision,
        "status": run.execution_status, "stage": cursor.get("stage", run.stage),
        "progress": {
            **{key: metrics[key] for key in ("completed_calls", "candidate_count", "fact_count",
                                            "work_counts", "rule_verified_count",
                                            "llm_verified_count")},
            "phase": cursor.get("phase", "reading"),
            "reading_windows": cursor.get("reading_windows", {
                "total": 0, "saved": 0, "complete": 0, "incomplete": 0, "active": 0,
            }),
            "scope_complete": reading["complete"], "reading": reading,
            "candidate_scope_limited": (metrics["limited_scope_count"] > 0
                                        or metrics["work_counts"]["pruned"] > 0),
            "stage_costs": stage_costs(metrics),
        },
    })
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
