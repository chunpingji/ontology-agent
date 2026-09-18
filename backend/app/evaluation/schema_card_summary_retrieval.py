"""Offline full-report retrieval probe; summaries never become source evidence."""

from __future__ import annotations

from collections import defaultdict

from app.evaluation.schema_card_evidence import build_sources
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    MetadataSnapshot,
    RangeClass,
    SlotSpec,
    SubjectRef,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot, validate_record_universe

POLICY_VERSION = "cmc-full-record-summary-probe-v1"
MAX_RECORDS = 6
MAX_SOURCE_CHARACTERS = 12_000
SCORE_WEIGHTS = {"source_labels": 4, "heading": 2, "summary": 1}


def _metadata_dependencies(metadata):
    return {
        name: getattr(metadata, name)
        for name in (
            "analysis_id", "document_hash", "structure_hash", "summary_version",
            "summary_model_identity", "generation_source", "source_record_refs", "policy_version",
        )
    } | {"nodes": [node.model_dump(mode="json") for node in metadata.node_summaries]}


def _validate_metadata(index, metadata):
    if any(getattr(metadata, key) != getattr(index.ir, key) for key in (
        "analysis_id", "document_hash", "structure_hash",
    )):
        raise ValueError("retrieval_metadata_source_mismatch")
    expected = set(index.by_id)
    if (set(metadata.source_record_refs) != expected
            or len(metadata.source_record_refs) != len(expected)):
        raise ValueError("retrieval_metadata_record_universe_mismatch")
    seen = set()
    for node in metadata.node_summaries:
        if node.node_id not in index.nodes_by_id or node.node_id in seen:
            raise ValueError("retrieval_metadata_node_mismatch")
        seen.add(node.node_id)
        if (len(node.source_record_refs) != len(set(node.source_record_refs))
                or any(rid not in expected or index.by_id[rid].section_node_id != node.node_id
                       for rid in node.source_record_refs)):
            raise ValueError("retrieval_metadata_node_record_mismatch")
    dependencies = _metadata_dependencies(metadata)
    if (metadata.dependency_hash != evidence_hash(dependencies)
            or metadata.snapshot_id != stable_id("metadata-snapshot", dependencies)):
        raise ValueError("retrieval_metadata_identity_mismatch")


def _masked_metadata(metadata):
    masked = metadata.model_copy(deep=True, update={
        "node_summaries": [node.model_copy(update={"summary": None})
                           for node in metadata.node_summaries],
    })
    dependencies = _metadata_dependencies(masked)
    return masked.model_copy(update={
        "dependency_hash": evidence_hash(dependencies),
        "snapshot_id": stable_id("metadata-snapshot", dependencies),
    })


def _queries(menus, catalog):
    """Only class/predicate menus supply queries; old anchors and scope names do not."""
    unique = {}
    for menu in menus.values():
        class_iri = menu["class_iri"]
        if class_iri not in catalog:
            raise ValueError("retrieval_query_class_outside_catalog")
        for field in menu["fields"].values():
            if field["kind"] == "property":
                predicate = SlotSpec.model_validate(field)
            elif field["kind"] == "relationship":
                predicate = EdgeSpec.model_validate(field)
                ranges = []
                for iri in sorted(set(predicate.range_class_iris)):
                    if iri not in catalog:
                        raise ValueError("retrieval_range_class_outside_catalog")
                    card = catalog[iri]
                    ranges.append(RangeClass(
                        iri=iri, label=card["label"], description=card.get("description", ""),
                        direct_field_labels=[item["label"] for item in card.get("properties", [])],
                    ))
                predicate = predicate.model_copy(update={"range_classes": ranges})
            else:
                raise ValueError("retrieval_query_predicate_kind_invalid")
            key = (class_iri, predicate.kind, predicate.iri)
            if key in unique and unique[key] != predicate:
                raise ValueError("retrieval_query_menu_conflict")
            unique[key] = predicate
    return [(key[0], predicate) for key, predicate in sorted(unique.items())]


def _record_roles(index):
    headings = defaultdict(list)
    for unit in index.ir.evidence_units:
        if unit.kind == "heading" and unit.text:
            headings[unit.section_node_id].append(index.ir.anchor(
                unit.evidence_id, 0, len(unit.text),
            ))
    roles = {}
    for record in index.records:
        view = index.record_views_by_id[record.record_id]
        field_group_ids = sorted({rid for group in index.field_groups_by_record[record.record_id]
                                  for rid in group.record_ids if rid != record.record_id},
                                 key=index.source_positions.__getitem__)
        refs = {
            "source": view.source_refs, "header": view.header_refs, "note": view.note_refs,
            "parent": view.parent_context_refs,
            "field_group": [anchor for rid in field_group_ids
                            for anchor in index.record_views_by_id[rid].source_refs],
            "ancestor_heading": [],
        }
        node_id, visited = record.section_node_id, set()
        while node_id is not None:
            if node_id in visited or node_id not in index.nodes_by_id:
                raise ValueError("retrieval_ancestor_structure_invalid")
            visited.add(node_id)
            refs["ancestor_heading"].extend(headings[node_id])
            node_id = index.nodes_by_id[node_id].get("parent_id")
        unit_ids = set()
        for anchors in refs.values():
            for anchor in anchors:
                if index.ir.resolve(anchor) != index.ir.unit(anchor.evidence_id).text:
                    raise ValueError("retrieval_record_requires_full_source_unit")
                unit_ids.add(anchor.evidence_id)
        roles[record.record_id] = {
            "refs": {role: [anchor.model_dump(mode="json") for anchor in anchors]
                     for role, anchors in refs.items()},
            "field_group_record_ids": field_group_ids,
            "unit_ids": sorted(unit_ids, key=index.positions.__getitem__),
            "logical_row_id": view.logical_row_id,
            "source_cell_ids": view.source_cell_ids,
        }
    return roles


def _rank(index, metadata, queries, roles, ontology_hash):
    scores = {record.record_id: {
        "record_id": record.record_id, "record_kind": record.kind,
        "section_node_id": record.section_node_id, "source_position": index.source_positions[
            record.record_id], "rank_score": 0.0,
        "score_components": dict.fromkeys(SCORE_WEIGHTS, 0.0), "query_scores": [],
    } for record in index.records}
    for class_iri, predicate in queries:
        subject = SubjectRef(
            entity_id=stable_id("retrieval-class-hypothesis", class_iri), revision=1,
            class_iri=class_iri,
        )
        plan = plan_slot(subject, predicate, index, metadata, ontology_hash=ontology_hash)
        validate_record_universe(plan, index)
        query_id = stable_id("retrieval-menu-query", [class_iri, predicate.kind, predicate.iri])
        for item in plan.records:
            row = scores[item.record_id]
            row["rank_score"] += item.rank_score
            for name in SCORE_WEIGHTS:
                row["score_components"][name] += item.score_components[name]
            row["query_scores"].append({
                "query_id": query_id, "plan_id": plan.plan_id, "rank_score": item.rank_score,
                "score_components": item.score_components, "phase": item.phase,
            })
    ranked = sorted(scores.values(), key=lambda row: (-row["rank_score"], row["source_position"]))
    selected, selected_units, deferred = [], set(), []
    characters = 0
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        if row["rank_score"] <= 0:
            row["selection_status"] = "not_selected_nonpositive"
            continue
        new_units = set(roles[row["record_id"]]["unit_ids"]) - selected_units
        added = sum(len(index.ir.unit(eid).text) for eid in new_units)
        reason = ("record_count_budget" if len(selected) >= MAX_RECORDS else
                  "source_character_budget" if characters + added > MAX_SOURCE_CHARACTERS
                  else None)
        row["additional_source_characters"] = added
        if reason:
            row.update(selection_status="deferred", reason=reason)
            deferred.append({"record_id": row["record_id"], "reason": reason,
                             "additional_source_characters": added})
        else:
            row["selection_status"] = "selected"
            selected.append(row["record_id"])
            selected_units.update(new_units)
            characters += added
    return {
        "metadata_snapshot_id": metadata.snapshot_id, "records": ranked,
        "selected_record_ids": selected, "deferred_records": deferred,
        "selected_unit_ids": sorted(selected_units, key=index.positions.__getitem__),
        "source_characters": characters, "query_count": len(queries),
    }


def _coverage(index, roles):
    primary = {a["evidence_id"] for row in roles.values() for a in row["refs"]["source"]}
    context = {eid for row in roles.values() for eid in row["unit_ids"]} - primary
    empty = {unit.evidence_id for unit in index.ir.evidence_units if not unit.text.strip()}
    all_units = {unit.evidence_id for unit in index.ir.evidence_units}
    groups = {"primary": primary - empty, "context": context - empty,
              "unrepresented": all_units - primary - context - empty, "empty": empty}
    return {
        "ir_unit_count": len(all_units), "record_count": len(index.records),
        "record_ids": list(index.by_id),
        "unit_categories": {
            name: {"count": len(ids), "evidence_ids": sorted(ids, key=index.positions.__getitem__)}
            for name, ids in groups.items()
        },
        "meaning": "Index representation only; retrieval selection is not examination or recall.",
    }


def retrieve_scope(
    ir: DocumentIR, metadata: MetadataSnapshot, menus: dict, catalog: dict, scope_id: str,
) -> dict:
    """Select complete records across the same full IR, plus a retrieval-only ablation."""
    index = RecordIndex(ir)
    _validate_metadata(index, metadata)
    masked = _masked_metadata(metadata)
    _validate_metadata(index, masked)
    queries = _queries(menus, catalog)
    roles = _record_roles(index)
    ontology_hash = evidence_hash(catalog)
    enabled = _rank(index, metadata, queries, roles, ontology_hash)
    disabled = _rank(index, masked, queries, roles, ontology_hash)
    units = [index.ir.unit(eid).model_dump(mode="json") for eid in enabled["selected_unit_ids"]]
    header_checks = build_sources({"source_units": [
        unit.model_dump(mode="json") for unit in index.ir.evidence_units
    ]}, index.ir.model_dump(mode="json"))
    selected, baseline = set(enabled["selected_record_ids"]), set(disabled["selected_record_ids"])
    scores_without = {row["record_id"]: row["rank_score"] for row in disabled["records"]}
    return {
        "case": {
            # The caller supplies the actual upload reference from its frozen manifest.
            "scope_id": scope_id, "document_ref": "",
            "document_title": index.ir.title, "root_class_iri": menus["document"]["class_iri"],
            "content": {"text": "\n".join(unit["text"] for unit in units),
                        "section_path": "", "previous_context": "", "next_context": ""},
            "source_units": units,
        },
        "retrieval": {
            "policy": POLICY_VERSION,
            "budgets": {"max_records": MAX_RECORDS, "max_source_characters": MAX_SOURCE_CHARACTERS,
                        "character_count": "Full unit text once per evidence_id; no truncation."},
            "score_weights": SCORE_WEIGHTS.copy(), "query_aggregation": "equal_weight_sum",
            "query_subject_authority": "plan_slot_hypothesis_not_an_entity_assertion",
            "queries": [{"query_id": stable_id("retrieval-menu-query", [cls, pred.kind, pred.iri]),
                         "subject_class_iri": cls, "predicate": pred.model_dump(mode="json")}
                        for cls, pred in queries],
            "source_identity": {key: getattr(index.ir, key) for key in
                                ("analysis_id", "document_hash", "structure_hash")},
            "metadata_source": {key: getattr(metadata, key) for key in
                                ("snapshot_id", "generation_source", "summary_model_identity",
                                 "summary_version", "dependency_hash")},
            "summary_enabled": enabled, "summary_masked": disabled,
            "ablation": {
                "selected_order_changed": enabled["selected_record_ids"] != disabled[
                    "selected_record_ids"],
                "source_unit_set_changed": set(enabled["selected_unit_ids"]) != set(
                    disabled["selected_unit_ids"]),
                "source_input_changed": enabled["selected_unit_ids"] != disabled[
                    "selected_unit_ids"],
                "added_record_ids": sorted(
                    selected - baseline, key=index.source_positions.__getitem__),
                "removed_record_ids": sorted(
                    baseline - selected, key=index.source_positions.__getitem__),
                "score_deltas": {
                    row["record_id"]: row["rank_score"] - scores_without[row["record_id"]]
                    for row in enabled["records"]
                },
                "new_model_calls": 0,
            },
            "record_roles": roles,
            "unit_header_status": {unit["evidence_id"]: {
                key: unit[key] for key in ("is_header", "parser_header_candidate", "header_status")
            } for unit in header_checks.values()},
            "summary_authority": "retrieval_only_never_source_or_citation",
        },
        "coverage": _coverage(index, roles),
    }
