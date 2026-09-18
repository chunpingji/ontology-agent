"""Held-out template acceptance; reference values never enter executor/model input."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from app.db import SessionLocal
from app.models.document_analysis import (
    DocumentAnalysisRun,
)
from app.schemas.evidence import EvidenceAnchor
from app.services.document_analysis.application import DocumentAnalysisApplication
from app.services.extraction.document_ir import DocumentIR

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-id", required=True, type=UUID)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
run_id = args.run_id
args.output.parent.mkdir(parents=True, exist_ok=True)


def short(iri):
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


with SessionLocal() as db:
    run = db.get(DocumentAnalysisRun, run_id)
    application = DocumentAnalysisApplication(db, ontology_engine=object())
    graph = application.graph_response(run, projection="effective_affirmed")
    payload = application._artifact_payload(run, "metadata")[0]
    ir = DocumentIR.model_validate(payload["analysis"])
    stored = application._artifact_payload(run, "graph")
    errors = []
    replayed = {}

    def replay(value):
        if isinstance(value, dict):
            if "evidence_id" in value and "block_id" in value:
                try:
                    anchor = EvidenceAnchor.model_validate(value)
                    text = ir.resolve(anchor)
                    replayed[(anchor.evidence_id, anchor.span_start, anchor.span_end)] = text
                except ValueError as error:
                    errors.append(str(error))
            else:
                for item in value.values():
                    replay(item)
        elif isinstance(value, list):
            for item in value:
                replay(item)

    if stored:
        replay(stored[0])
    root = graph["graph_snapshot"]["root_ref"]["entity_id"] if graph["graph_snapshot"] else None
    nodes = {node["entity_id"]: node for node in graph["entities"]}
    branches = []
    for edge in graph["relationships"]:
        if edge["subject_ref"]["entity_id"] != root:
            continue
        node = nodes[edge["object_ref"]["entity_id"]]
        properties = [
            {
                "predicate": short(p["predicate_iri"]),
                "value": p["raw_value"],
                "candidate_id": p["candidate_id"],
                "source_selection_refs": p["source_selection_refs"],
            }
            for p in graph["properties"]
            if p["subject_ref"]["entity_id"] == node["entity_id"]
        ]
        branches.append(
            {
                "predicate": short(edge["predicate_iri"]),
                "class": short(node["class_iri"]),
                "label": node["label"],
                "properties": properties,
                "entity_id": node["entity_id"],
                "relation_id": edge["candidate_id"],
                "source_selection_refs": edge["source_selection_refs"],
            }
        )
    checks = {
        "source_hash": ir.document_hash
        == "2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436",
        "anchors_replayed": bool(replayed) and not errors,
    }
    product = next(
        (
            b
            for b in branches
            if b["predicate"] == "describes"
            and b["class"] == "DrugProduct"
            and b["label"] in {"HRS-5592", "HRS-5592项目"}
        ),
        None,
    )
    checks["DrugProduct"] = product is not None
    values = {p["predicate"]: p["value"] for p in product["properties"]} if product else {}
    for predicate, value in {
        "projectName": "HRS-5592",
        "registrationCategory": "化1类新药",
        "molecularFormula": "C26H25F2N7O2S",
        "molecularWeight": "537.18",
    }.items():
        checks[predicate] = values.get(predicate) == value
    checks["no_wrong_product_weight"] = bool(product) and all(
        p["value"] == "537.18" for p in product["properties"] if p["predicate"] == "molecularWeight"
    )
    route = next(
        (
            b
            for b in branches
            if b["predicate"] == "hasSynthesisRoute"
            and b["class"] == "SynthesisRoute"
            and b["label"] == "3.1.1合成路线图"
        ),
        None,
    )
    checks["SynthesisRoute"] = route is not None
    checks["processBasis"] = bool(route) and any(
        p["predicate"] == "processBasis" and p["value"] == "3.1.1合成路线图"
        for p in route["properties"]
    )
    checks["no_title_as_description"] = bool(route) and all(
        p["value"] != "3.1.1合成路线图"
        for p in route["properties"]
        if p["predicate"] == "processDescription"
    )
    cleaning = next(
        (
            b
            for b in branches
            if b["predicate"] == "hasCleaningMethod"
            and b["class"] == "CleaningProcess"
            and "水洗" in b["label"]
        ),
        None,
    )
    checks["CleaningProcess"] = cleaning is not None
    checks["cleaning_properties_not_applicable"] = (
        bool(cleaning)
        and nodes[cleaning["entity_id"]].get("predicate_menu") is not None
        and not any(m["kind"] == "property" for m in nodes[cleaning["entity_id"]]["predicate_menu"])
    )
    checks["no_placeholder_values"] = all(
        p["raw_value"].strip().lower() not in {"n/a", "na", "未知", "不适用"}
        for p in graph["properties"]
    )
    result = {
        "run_id": str(run_id),
        "status": run.execution_status,
        "passed": all(checks.values()),
        "checks": checks,
        "replayed_anchor_count": len(replayed),
        "replay_errors": errors,
        "branches": branches,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))

raise SystemExit(0 if result["passed"] else 1)
