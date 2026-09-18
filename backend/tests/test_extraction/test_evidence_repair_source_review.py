"""Source review must not inherit the recognizer's claimed success."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/review_hrs5592_evidence_repair.py"
spec = importlib.util.spec_from_file_location("source_review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def anchor(row, column, end):
    return {"document_hash": review.SOURCE, "block_id": "table:9", "row_index": row,
            "column_index": column, "span_start": 0, "span_end": end}


def fixture_candidates():
    tables = [[] for _ in range(10)]
    tables[9] = [["", "设备名称", "设备编号"], [], [], ["", "离心机", "CT-1"],
                 ["", "离心机", "CT-2"]]
    parent = {
        "candidate_id": "parent", "revision": 1, "predicate_iri": "urn:test/usesEquipment",
        "decision_status": "supported", "reason_code": "model_supported",
        "policy_eligible": True, "polarity": "affirmed", "conditions": [], "applicability": {},
        "subject_ref": {"id": "root"}, "object_ref": {"id": "equipment"},
        "object_evidence_refs": [anchor(3, 1, 3)],
    }
    prop = {
        "candidate_id": "property", "revision": 1, "predicate_iri": "urn:test/equipmentID",
        "decision_status": "supported", "reason_code": "model_supported",
        "policy_eligible": True, "polarity": "affirmed", "conditions": [], "applicability": {},
        "subject_ref": {"id": "equipment"}, "subject_evidence_refs": [anchor(3, 1, 3)] * 2,
        "value_evidence_refs": [anchor(3, 2, 4)], "predicate_evidence_refs": [anchor(0, 2, 4)],
        "raw_value": "CT-1", "normalized_value": "CT-1",
    }
    nodes = [{"entity_id": "equipment", "label": "离心机", "class_iri": "urn:test/Centrifuge"}]
    return parent, prop, nodes, tables


@pytest.mark.parametrize("fault", ["wrong_span", "same_name_other_row", "invented_condition"])
def test_model_supported_does_not_override_wrong_original_source(fault):
    parent, prop, nodes, tables = fixture_candidates()
    assert all(r["source_review"] == "source_and_ownership_checked"
               for r in review.review_candidates([parent, prop], nodes, [], tables, {}))
    prop = deepcopy(prop)
    if fault == "wrong_span":
        prop["value_evidence_refs"][0]["span_start"] = 1
    elif fault == "same_name_other_row":
        prop["value_evidence_refs"] = [anchor(4, 2, 4)]
        prop["raw_value"] = prop["normalized_value"] = "CT-2"
    else:
        prop["applicability"] = {"equipment": "invented"}
    rows = review.review_candidates([parent, prop], nodes, [], tables, {})
    assert rows[1]["source_review"] == "source_or_ownership_review_failed"


def test_directed_failures_and_unexecuted_targets_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "source", lambda path: ([], []))
    cases = ["production_plan", "production_plan:wrong_document_date"]
    tasks = [{"case": case, "arm": "current", "task_id": str(i), "status": "failed",
              "seconds": 1, "started_seconds": i, "ended_seconds": i + 1,
              "model_calls": 1, "complete": False, "input_file": f"tasks/{i}/input.json"}
             for i, case in enumerate(cases)]
    payloads = {"summary.json": {"run_id": "run", "recognition_seconds": 2},
                "ontology-snapshot.json": {"classes": {}}, "task-results.json": tasks,
                "model-requests.json": []}
    payloads.update({f"tasks/{i}/outcome.json": {"nodes": [], "edges": [], "properties": []}
                     for i in range(2)})
    for name, data in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    result = review.review_run(tmp_path, automatic=False)
    assert result["unexecuted_expected_tasks"] == 12
    assert [r["source_review"] for r in result["tasks"]] == [
        "positive_target_not_demonstrated", "negative_control_not_demonstrated",
    ]


def test_wrong_document_is_rejected_before_source_review(tmp_path):
    path = tmp_path / "another.docx"
    path.write_bytes(b"not the frozen HRS-5592 source")
    with pytest.raises(AssertionError):
        review.source(path)
