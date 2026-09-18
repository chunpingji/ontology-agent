"""Audit HRS-5592 focus paths against DOCX XML after recognition.

This developer-sample reference is never a recognition input or an expert gold set.
Full source reviews belong in the private run area, not public/Git artifacts.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

if not __debug__:
    raise RuntimeError("Source auditing requires assertions enabled; do not use python -O.")

if not __debug__:
    raise RuntimeError("Source auditing requires assertions enabled; do not use python -O.")

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SOURCE = "2c1174bf616f30dd28c655fef4261c167762de807c8ad79e148fb8ef18e16436"
REVIEW_VERSION = "hrs5592-source-ownership-review-v2"
FOCUS = {
    "usesEquipment",
    "hasProductionPlan",
    "equipmentID",
    "modelSpecification",
    "plannedProductionDate",
    "plannedBatchCount",
}


def read(path):
    return json.loads(path.read_text())


def short(iri):
    return iri.rsplit("/", 1)[-1]


def xml_text(node):
    return "".join(t.text or "" for t in node.findall(".//w:t", NS))


def source(path):
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SOURCE
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", NS)
    paragraphs = [xml_text(p) for p in body.findall("w:p", NS)]
    tables = [
        [[xml_text(cell) for cell in row.findall("w:tc", NS)] for row in table.findall("w:tr", NS)]
        for table in body.findall("w:tbl", NS)
    ]
    assert "计划在2026年08月" in paragraphs[26] and "本次计划生产1批" in paragraphs[26]
    assert paragraphs[19] == "时间：2026年07月"
    assert tables[9][0][1:4] == ["设备名称", "设备编号", "规格型号"]
    return paragraphs, tables


def coordinate(ref):
    return ref["block_id"], ref.get("row_index"), ref.get("column_index")


def check_sources(candidate, paragraphs, tables):
    # Each exact anchor must replay against XML from the actual uploaded DOCX.
    for name, refs in candidate.items():
        if not name.endswith("evidence_refs") or not isinstance(refs, list):
            continue
        for ref in refs:
            assert ref["document_hash"] == SOURCE
            if ref["block_id"].startswith("table:"):
                text = tables[int(ref["block_id"].split(":")[1])][ref["row_index"]][
                    ref["column_index"]
                ]
            else:
                text = paragraphs[ref["paragraph_index"]]
            assert 0 <= (ref.get("span_start") or 0) <= len(text)
            assert ref.get("span_end") is None or ref["span_start"] < ref["span_end"] <= len(text)


def review_candidates(candidates, nodes, paragraphs, tables, ancestors):
    result = []
    by_id = {n["entity_id"]: n for n in nodes}
    parents = {
        c["object_ref"]["id"]: c
        for c in candidates
        if "object_ref" in c and c.get("policy_eligible") and c["polarity"] == "affirmed"
    }
    for candidate in candidates:
        predicate = short(candidate["predicate_iri"])
        eligible = candidate.get("currently_effective", candidate.get("policy_eligible", False))
        row = {
            "candidate_id": candidate["candidate_id"],
            "revision": candidate["revision"],
            "predicate": predicate,
            "effective": eligible,
            "decision_status": candidate["decision_status"],
            "reason_code": candidate["reason_code"],
            "source_review": "unscored",
            "first_effective_seconds": candidate.get("first_effective_seconds"),
        }
        if not eligible:
            row["source_review"] = "not_effective"
            result.append(row)
            continue
        if predicate not in FOCUS:
            node = by_id.get(candidate.get("object_ref", {}).get("id"), {})
            row["object_label"] = candidate.get("object_label", node.get("label"))
            row["object_class"] = short(
                candidate.get("object_class_iri") or node.get("class_iri", "")
            )
            result.append(row)
            continue
        try:
            check_sources(candidate, paragraphs, tables)
            assert (
                candidate["polarity"] == "affirmed"
                and not candidate["conditions"]
                and not candidate["applicability"]
            )
            if predicate in {"usesEquipment", "hasProductionPlan"}:
                node = by_id[candidate["object_ref"]["id"]]
                refs = candidate["object_evidence_refs"]
                assert refs
                ref = refs[0]
                endpoint = (
                    tables[int(ref["block_id"].split(":")[1])][ref["row_index"]][
                        ref["column_index"]
                    ]
                    if ref["block_id"].startswith("table:")
                    else paragraphs[ref["paragraph_index"]]
                )
                assert endpoint[ref["span_start"] : ref["span_end"]] == node["label"]
                if predicate == "usesEquipment":
                    assert ref["block_id"] in {"table:8", "table:9"} and ref["column_index"] == 1
                    table_index = int(ref["block_id"].split(":")[1])
                    expected = tables[table_index][ref["row_index"]][1]
                    assert node["label"] == expected
                    expected_type = {
                        "离心机": "Centrifuge",
                        "1000L反应釜": "Reactor",
                        "真空干燥箱": "VacuumDryer",
                    }.get(expected)
                    assert expected_type is not None, (
                        "new equipment needs manual type review",
                        expected,
                    )
                    assert short(node["class_iri"]) in {
                        expected_type,
                        *ancestors.get(expected_type, []),
                    }
                    row["object_class"] = short(node["class_iri"])
                    row["type_specificity"] = (
                        "exact"
                        if short(node["class_iri"]) == expected_type
                        else "supported_ancestor"
                    )
                    row.update(
                        object_label=expected, source_table=table_index, source_row=ref["row_index"]
                    )
                else:
                    assert ref["paragraph_index"] == 26 and node["label"] in paragraphs[26]
                    assert short(node["class_iri"]) == "ClinicalSampleProductionPlan"
                    row["source_paragraph"] = 26
                row["subject_id"] = candidate["subject_ref"]["id"]
                row["object_id"] = candidate["object_ref"]["id"]
            else:
                parent = parents[candidate["subject_ref"]["id"]]
                row["subject_id"] = candidate["subject_ref"]["id"]
                ref = candidate["value_evidence_refs"][0]
                subject_refs = candidate["subject_evidence_refs"]
                assert len(subject_refs) >= 2
                if predicate in {"equipmentID", "modelSpecification"}:
                    assert short(parent["predicate_iri"]) == "usesEquipment"
                    original = parent["object_evidence_refs"][0]
                    column = {"equipmentID": 2, "modelSpecification": 3}[predicate]
                    assert coordinate(ref) == ("table:9", original["row_index"], column)
                    assert all(coordinate(r) == coordinate(original) for r in subject_refs)
                    assert ("table:9", 0, column) in {
                        coordinate(r) for r in candidate["predicate_evidence_refs"]
                    }
                    expected = tables[9][original["row_index"]][column]
                    assert candidate["raw_value"] == candidate["normalized_value"] == expected
                    row["source_row"] = original["row_index"]
                else:
                    assert short(parent["predicate_iri"]) == "hasProductionPlan"
                    assert ref["paragraph_index"] == 26
                    assert all(r["paragraph_index"] == 26 for r in subject_refs)
                    expected, normalized = {
                        "plannedProductionDate": ("2026年08月", "2026-08"),
                        "plannedBatchCount": ("1", 1),
                    }[predicate]
                    assert (
                        candidate["raw_value"] == expected
                        and candidate["normalized_value"] == normalized
                    )
                    assert paragraphs[26][ref["span_start"] : ref["span_end"]] == expected
                    row["source_paragraph"] = 26
                endpoint = (
                    tables[int(ref["block_id"].split(":")[1])][ref["row_index"]][
                        ref["column_index"]
                    ]
                    if ref["block_id"].startswith("table:")
                    else paragraphs[ref["paragraph_index"]]
                )
                assert endpoint[ref["span_start"] : ref["span_end"]] == candidate["raw_value"]
                row.update(
                    raw_value=candidate["raw_value"], normalized_value=candidate["normalized_value"]
                )
            row["source_review"] = "source_and_ownership_checked"
        except (AssertionError, KeyError) as error:
            row.update(
                source_review="source_or_ownership_review_failed",
                error_type=type(error).__name__,
                raw_value=candidate.get("raw_value"),
                value_sources=[coordinate(r) for r in candidate.get("value_evidence_refs", [])],
                subject_sources=[coordinate(r) for r in candidate.get("subject_evidence_refs", [])],
            )
        result.append(row)
    return result


def review_run(path, automatic):
    summary = read(path / "summary.json")
    paragraphs, tables = source(path / "source.docx")
    classes = read(path / "ontology-snapshot.json")["classes"]
    ancestors = {}
    for cls in classes.values():
        found, pending = set(), list(cls["parent_iris"])
        while pending:
            iri = pending.pop()
            if iri in found:
                continue
            found.add(iri)
            pending.extend(classes.get(iri, {}).get("parent_iris", []))
        ancestors[short(cls["iri"])] = sorted(short(iri) for iri in found)
    result = {
        "directory": str(path),
        "source_sha256": SOURCE,
        "review_version": REVIEW_VERSION,
        "review_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": summary["run_id"],
        "recognition_seconds": summary["recognition_seconds"],
        "expert_quality_gate": "not_passed",
        "production_deployed": False,
    }
    if automatic:
        graph = read(path / "graph/result.json")["graph"]
        result["candidates"] = review_candidates(
            summary["effective_candidate_history"], graph["nodes"], paragraphs, tables, ancestors
        )
        result["missing_focus_targets"] = sorted(
            FOCUS
            - {
                r["predicate"]
                for r in result["candidates"]
                if r["source_review"] == "source_and_ownership_checked"
            }
        )
        result["progress"] = summary["progress"]
        result["requested_stop_reason"] = summary["requested_stop_reason"]
        result["task_batches"] = summary["task_batches"]
        result["applied_candidate_history"] = []
        for file in sorted((path / "batches").glob("*.json")):
            batch = read(file)
            for c in batch["outcome"]["edges"] + batch["outcome"]["properties"]:
                result["applied_candidate_history"].append(
                    {
                        "batch": file.name,
                        "candidate_id": c["candidate_id"],
                        "revision": c["revision"],
                        "predicate": short(c["predicate_iri"]),
                        "decision_status": c["decision_status"],
                        "policy_eligible": c["policy_eligible"],
                        "reason_code": c["reason_code"],
                    }
                )
    else:
        tasks = read(path / "task-results.json")
        result["tasks"] = []
        for arm in ["current", "joint"]:
            selected = [t for t in tasks if t["arm"] == arm]
            outputs = {
                t["task_id"]: read((path / t["input_file"]).with_name("outcome.json"))
                for t in selected
            }
            nodes = [n for output in outputs.values() for n in output["nodes"]]
            candidates = [
                c for output in outputs.values() for c in output["edges"] + output["properties"]
            ]
            reviews = {
                r["candidate_id"]: r
                for r in review_candidates(candidates, nodes, paragraphs, tables, ancestors)
            }
            for task in selected:
                output = outputs[task["task_id"]]
                actual = output["edges"] + output["properties"]
                item = {
                    k: task[k]
                    for k in [
                        "case",
                        "arm",
                        "status",
                        "seconds",
                        "started_seconds",
                        "ended_seconds",
                        "model_calls",
                        "complete",
                    ]
                }
                item["candidates"] = [reviews[c["candidate_id"]] for c in actual]
                if task["case"].endswith("wrong_document_date"):
                    rejected = task["complete"] and task["status"] == "unsupported"
                    rejected = (
                        rejected
                        and actual
                        and all(
                            c.get("raw_value") == "2026年07月" and not c["policy_eligible"]
                            for c in actual
                        )
                    )
                    item["source_review"] = (
                        "wrong_document_date_actually_rejected"
                        if rejected
                        else "negative_control_not_demonstrated"
                    )
                else:
                    supported = len(actual) == 1 and all(
                        r["source_review"] == "source_and_ownership_checked"
                        for r in item["candidates"]
                    )
                    item["source_review"] = (
                        "source_and_ownership_checked"
                        if supported
                        else "positive_target_not_demonstrated"
                    )
                result["tasks"].append(item)
        result["expected_task_count"] = 14
        result["actual_task_count"] = len(result["tasks"])
        result["unexecuted_expected_tasks"] = max(0, 14 - len(result["tasks"]))
    stages = {}
    for request in read(path / "model-requests.json"):
        m = request.get("metrics") or {}
        stage = request["stage"]
        v = stages.setdefault(stage, Counter())
        v["requests"] += 1
        for name in [
            "request_seconds",
            "queue_seconds",
            "prompt_tokens",
            "completion_tokens",
            "prompt_ms",
            "predicted_ms",
            "cache_tokens",
        ]:
            if m.get(name) is not None:
                v[name] += m[name]
            else:
                v["unknown_" + name] += 1
    result["costs"] = stages
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--group", choices=["automatic", "directed"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review_run(args.run, args.group == "automatic")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    rows = result.get("candidates", result.get("tasks", []))
    print(
        json.dumps(
            {
                "source_review_counts": dict(Counter(r["source_review"] for r in rows)),
                "expert_quality_gate": "not_passed",
            },
            ensure_ascii=False,
        )
    )
