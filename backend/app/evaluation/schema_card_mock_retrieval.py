"""Single-predicate full-record retrieval for the offline Mock ablation.

Mock terms change retrieval priority only. They never establish an entity
identity, attribute, or relationship, and are not added to original sources.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy

from app.evaluation.schema_card_evidence import build_sources
from app.evaluation.schema_card_summary_retrieval import _queries, _validate_metadata
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import MetadataSnapshot, SubjectRef
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval import plan_slot, validate_record_universe

CMC = "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport"
USES_EQUIPMENT = "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment"
PROCESS_EQUIPMENT = "https://ontology.pharma-gmp.cn/slpra/equipment/ProcessEquipment"
POLICY_VERSION = "cmc-mock-single-predicate-record-retrieval-v1"
MAX_RECORDS = 3
MAX_SOURCE_CHARACTERS = 3_000
SCORE_WEIGHTS = {"source_labels": 4, "heading": 2, "summary": 1,
                 "distinct_exact_ids": 4, "distinct_exact_names": 1}


def _query(catalog):
    card = catalog.get(CMC)
    if not card:
        raise ValueError("mock_retrieval_cmc_card_missing")
    predicates = [item for item in card.get("allowed_relations", [])
                  if item.get("iri") == USES_EQUIPMENT]
    if len(predicates) != 1 or PROCESS_EQUIPMENT not in predicates[0].get("range_class_iris", []):
        raise ValueError("mock_retrieval_equipment_relation_invalid")
    menus = {"document": {"class_iri": CMC, "fields": {"usesEquipment": predicates[0]}}}
    return _queries(menus, catalog)[0][1]


def _roles(index):
    """Retain structural context, but do not expand neighboring field groups."""
    headings = defaultdict(list)
    for unit in index.ir.evidence_units:
        if unit.kind == "heading" and unit.text:
            headings[unit.section_node_id].append(index.ir.anchor(unit.evidence_id, 0,
                                                                  len(unit.text)))
    result = {}
    for record in index.records:
        view = index.record_views_by_id[record.record_id]
        refs = {"source": view.source_refs, "header": view.header_refs,
                "note": view.note_refs, "parent": view.parent_context_refs,
                "ancestor_heading": []}
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
        result[record.record_id] = {
            "refs": {role: [anchor.model_dump(mode="json") for anchor in anchors]
                     for role, anchors in refs.items()},
            "unit_ids": sorted(unit_ids, key=index.positions.__getitem__),
            "logical_row_id": view.logical_row_id, "source_cell_ids": view.source_cell_ids,
        }
    return result


def _mock_patterns(mock_records):
    ids, names = set(), set()
    for record in mock_records:
        if record.get("equipment_id") in ids:
            raise ValueError("mock_retrieval_duplicate_equipment_id")
        for field, terms in (("equipment_id", ids), ("name", names)):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError("mock_retrieval_term_invalid")
            terms.add(value)
    return {
        "exact_ids": [(term, re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(term)
                                       + r"(?![A-Za-z0-9_-])")) for term in sorted(ids)],
        "exact_names": [(term, re.compile(re.escape(term))) for term in sorted(names)],
    }


def _mock_hits(index, record, patterns):
    """Scan primary source units only; a shared header cannot boost every row."""
    matched = {key: [] for key in patterns}
    for kind, terms in patterns.items():
        for term, pattern in terms:
            anchors = [index.ir.anchor(unit.evidence_id, match.start(), match.end()).model_dump(
                mode="json") for unit in record.source_units for match in pattern.finditer(
                    unit.text)]
            if anchors:
                matched[kind].append({"term": term, "anchors": anchors})
    matched["score"] = 4 * len(matched["exact_ids"]) + len(matched["exact_names"])
    return matched


def _case(index, record_id, roles):
    units = [index.ir.unit(eid).model_dump(mode="json") for eid in roles["unit_ids"]]
    case = {
        "record_id": record_id, "scope_id": stable_id("mock-retrieval-case", record_id),
        "document_ref": "", "document_title": index.ir.title, "root_class_iri": CMC,
        "content": {"text": "\n".join(unit["text"] for unit in units), "section_path": "",
                    "previous_context": "", "next_context": ""},
        "source_units": units, "source_characters": sum(len(unit["text"]) for unit in units),
    }
    sources = build_sources(case, index.ir.model_dump(mode="json"))
    primary = {anchor["evidence_id"] for anchor in roles["refs"]["source"]}
    case["sources"] = sources
    case["roles"] = deepcopy(roles) | {
        "primary_refs": [ref for ref, source in sources.items()
                         if source["evidence_id"] in primary],
        "context_refs": [ref for ref, source in sources.items()
                         if source["evidence_id"] not in primary],
    }
    return case


def _select(index, base_records, roles, matches, *, with_mock):
    rows = []
    for item in base_records:
        hits = matches[item.record_id]
        row = {
            "record_id": item.record_id, "record_kind": item.record_kind,
            "section_node_id": item.section_node_id,
            "source_position": index.source_positions[item.record_id], "phase": item.phase,
            "baseline_score": item.rank_score,
            "mock_score": hits["score"] if with_mock else 0,
            "rank_score": item.rank_score + (hits["score"] if with_mock else 0),
            "score_components": dict(item.score_components) | {
                "distinct_exact_ids": len(hits["exact_ids"]) if with_mock else 0,
                "distinct_exact_names": len(hits["exact_names"]) if with_mock else 0,
            },
            "mock_hits": deepcopy(hits) if with_mock else None,
            "source_characters": sum(len(index.ir.unit(eid).text)
                                     for eid in roles[item.record_id]["unit_ids"]),
        }
        rows.append(row)
    rows.sort(key=lambda row: (-row["rank_score"], row["source_position"]))
    selected, deferred, cases = [], [], []
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
        if row["rank_score"] <= 0:
            row["selection_status"] = "not_selected_nonpositive"
            continue
        reason = ("source_character_budget" if row["source_characters"] > MAX_SOURCE_CHARACTERS
                  else "record_count_budget" if len(selected) >= MAX_RECORDS else None)
        if reason:
            row.update(selection_status="deferred", reason=reason)
            deferred.append({"record_id": row["record_id"], "reason": reason,
                             "source_characters": row["source_characters"]})
        else:
            row["selection_status"] = "selected"
            selected.append(row["record_id"])
            cases.append(_case(index, row["record_id"], roles[row["record_id"]]))
    return {"cases": cases, "ranking": {
        "records": rows, "selected_record_ids": selected, "deferred_records": deferred,
        "record_count": len(rows), "query_count": 1,
        "budgets": {"max_records": MAX_RECORDS,
                    "max_source_characters_per_case": MAX_SOURCE_CHARACTERS,
                    "character_count": "Full text once per evidence_id within each case."},
    }}


def prepare_retrieval(
    ir: DocumentIR, metadata: MetadataSnapshot, catalog: dict, mock_records: list[dict],
) -> dict:
    """Return E0/E1 shared sources and E2 sources, without invoking any model."""
    index = RecordIndex(ir)
    _validate_metadata(index, metadata)
    predicate = _query(catalog)
    subject = SubjectRef(entity_id=stable_id("retrieval-class-hypothesis", CMC), revision=1,
                         class_iri=CMC)
    plan = plan_slot(subject, predicate, index, metadata, ontology_hash=evidence_hash(catalog))
    validate_record_universe(plan, index)
    roles, patterns = _roles(index), _mock_patterns(mock_records)
    matches = {record.record_id: _mock_hits(index, record, patterns) for record in index.records}
    result = {
        "policy": POLICY_VERSION,
        "baseline": _select(index, plan.records, roles, matches, with_mock=False),
        "mock_retrieval": _select(index, plan.records, roles, matches, with_mock=True),
        "source_identity": {key: getattr(index.ir, key) for key in
                            ("analysis_id", "document_hash", "structure_hash")},
        "metadata_snapshot_id": metadata.snapshot_id,
        "catalog_hash": evidence_hash(catalog), "mock_records_hash": evidence_hash(mock_records),
        "query": {"subject_class_iri": CMC, "predicate": predicate.model_dump(mode="json"),
                  "plan_id": plan.plan_id},
        "score_weights": SCORE_WEIGHTS.copy(),
        "mock_match_policy": "Case-sensitive primary-unit exact terms; ASCII ID boundaries; "
                             "distinct terms per record, never number of matching Mock rows.",
        "context_policy": "Complete source/header/note/parent/ancestor_heading; no field groups.",
        "summary_authority": "retrieval_only_never_source_or_citation",
        "mock_authority": "retrieval_hint_only_not_identity_or_relationship_proof",
        "record_roles": roles,
    }
    baseline = result["baseline"]["ranking"]["selected_record_ids"]
    enabled = result["mock_retrieval"]["ranking"]["selected_record_ids"]
    result["ablation"] = {
        "selected_order_changed": baseline != enabled,
        "added_record_ids": [rid for rid in enabled if rid not in baseline],
        "removed_record_ids": [rid for rid in baseline if rid not in enabled],
        "new_model_calls": 0,
    }
    return result
