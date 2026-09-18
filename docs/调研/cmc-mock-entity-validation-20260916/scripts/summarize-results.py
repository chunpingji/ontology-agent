"""Summarize frozen artifacts and matched independent reviews; no model calls."""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root, report = map(Path, sys.argv[1:])
manifest = read(root / "result.json")
audit = read(report / "artifact-audit.json")
reference = read(report / "reference-scores.json")
reviews = read(report / "reviews-matched.json")
assert manifest["run_id"] == audit["run_id"]
assert audit["summary"]["integrity_passed"]
assert reviews["exact_candidate_matching_passed"] and not reviews["unreviewed"]
arms = {}
for arm in ("E0", "E1", "E2"):
    results = [r for r in manifest["results"] if r["arm"] == arm]
    counts = {}
    for destination in ("accepted", "unresolved", "rejected"):
        rows = [t for r in results for t in r[destination]]
        counts[destination] = {
            "count": len(rows),
            "by_kind": dict(Counter(t["kind"] for t in rows)),
        }
    arms[arm] = {
        "complete_cases": sum(r["status"] == "complete" for r in results),
        "program_destinations": counts,
        "source_only_review": reviews["by_arm"][arm],
        "retrieval_and_identifier_coverage": reference["arms"][arm],
        "technical": audit["arms"][arm],
    }
unique_ner = {}
for row in manifest["ner_results"]:
    key = row["source_input_hash"]
    if key in unique_ner:
        assert unique_ner[key] == row["span_count"]
    unique_ner[key] = row["span_count"]
summary = {
    "run_id": manifest["run_id"],
    "report_id": "upload-23c872fb-3ab1-41de-a705-dd4b162dfa09",
    "outcome": "execution_complete_quality_not_fully_passed",
    "quality_gain_established": False,
    "deployed": False,
    "source_sha256": manifest["source_sha256"],
    "model": manifest["model"],
    "declared_model_revision": manifest["declared_model_revision"],
    "gliner_model": {k: manifest["gliner2_model"][k] for k in ("repo", "revision")},
    "input_hashes": manifest["input_hashes"],
    "technical": audit["summary"],
    "integrity_checks": len(audit["checks"]),
    "ner": {
        "unique_inputs": len(unique_ner),
        "spans_over_unique_inputs": sum(unique_ner.values()),
        "spans_across_cases": sum(r["span_count"] for r in manifest["ner_results"]),
        "omitted_model_view_spans": audit["summary"]["model_view_omitted_spans_across_cases"],
        "wall_seconds": manifest["ner_wall_seconds"],
    },
    "qwen_wall_seconds": manifest["qwen_wall_seconds"],
    "new_summary_calls": manifest["new_summary_calls"],
    "arms": arms,
    "accepted_without_independent_support": [
        r for r in reviews["records"]
        if r["program_destination"] == "accepted" and r["overall_verdict"] != "supported"
    ],
    "evidence_hashes": {name: sha(report / name) for name in (
        "artifact-audit.json", "reference.json", "reference-scores.json",
        "reviews-matched.json", "review-e0-e1.json", "review-e2.json",
        "engineering-checks.json", "scope-cards.json",
    )},
    "limits": [
        "Only static mock_equipment/equipment_archive equipment records were tested.",
        "Independent assistant reference and source review are not expert gold.",
        "Identifier coverage is not semantic accuracy or full graph recall.",
        "Accepted observations are counted separately from entity/property/relation claims.",
        "E2 changes retrieval; only E0/E1 share all selected records and NER.",
        "String SHACL coverage does not evaluate numeric quantities or unit conversion.",
        "Reference scorer does not read semantic reviews; matched reviews complete that separate stage.",
    ],
}
(report / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

samples = []
for arm, scope, purpose in (
    ("E1", "record-c54c3238c67b", "known_identifier_external_link"),
    ("E1", "record-0c9a7e82c53b", "unknown_identifier_and_bad_condition_citation"),
    ("E2", "record-be0b530ec6ae", "anchor_conflicts_blocked_but_relation_undetermined"),
    ("E2", "record-d29114c4e0c5", "merged_alternatives_blocked"),
):
    directory = root / arm / scope
    result = read(directory / "result.json")
    samples.append({
        "arm": arm, "scope_id": scope, "purpose": purpose,
        "artifact_directory": str(directory),
        "candidate_sha256": sha(directory / "candidates/proposal.json"),
        "candidate": read(directory / "candidates/proposal.json"),
        "final_destinations": {
            dest: [{k: row[k] for k in ("id", "kind", "issues") if k in row}
                   for row in result[dest]]
            for dest in ("accepted", "unresolved", "rejected")
        },
        "external_records": [r for r in audit["external_records"]
                             if r["arm"] == arm and r["case"] == scope],
        "source_only_review": [r for r in reviews["records"]
                               if r["arm"] == arm and r["scope_id"] == scope],
    })
(report / "return-samples.json").write_text(json.dumps({"samples": samples}, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"run_id": summary["run_id"], "arms": {
    arm: value["program_destinations"] for arm, value in arms.items()
}}, ensure_ascii=False))
