"""Match independent source reviews to frozen C candidates, without model calls.

Usage: python match-reviews.py RUN_ROOT REVIEW_INPUTS OUTPUT REVIEW_JSON...
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_coordinates(value):
    if isinstance(value, dict):
        return {
            k: without_coordinates(v)
            for k, v in value.items()
            if k not in {"span_id", "start", "end", "replay_issue"}
        }
    if isinstance(value, list):
        return [without_coordinates(v) for v in value]
    return value


root, packs, output = map(Path, sys.argv[1:4])
review_files = list(map(Path, sys.argv[4:]))
cases = [case for path in review_files for case in read(path)["cases"]]
assert len({c["scope_id"] for c in cases}) == len(cases)
rows = []
coverage = []
for case in cases:
    scope = case["scope_id"]
    pack_path = packs / (scope + ".json")
    pack = read(pack_path)
    assert case["review_input_sha256"] == digest(pack_path), scope
    scope_root = root / "C" / scope
    for name, expected in pack["source_artifacts"].items():
        assert digest(scope_root / name) == expected, (scope, name)
    assert case["source_artifacts"] == pack["source_artifacts"]
    result = read(scope_root / "result.json")
    frozen = read(scope_root / "frozen-candidates.json")
    destinations = {}
    for key in ("accepted", "observations", "unresolved", "rejected"):
        for item in result.get(key, []):
            cid = item.get("candidate_id")
            if cid:
                assert cid not in destinations, (scope, cid, "duplicate destination")
                destinations[cid] = (key, item)
    # Kept observations are saved as proposals, without candidate IDs.
    # Match full normalized content and consume equal entries in source order.
    unmatched_observations = [
        item for item in frozen["observations"] if item["id"] not in destinations
    ]
    for item in result.get("observations", []):
        if item.get("candidate_id"):
            continue
        possible = [
            candidate for candidate in unmatched_observations
            if without_coordinates(candidate["proposal"]) == without_coordinates(item)
        ]
        assert possible, (scope, "unmatched kept observation")
        candidate = possible[0]
        unmatched_observations.remove(candidate)
        destinations[candidate["id"]] = ("observations", {"proposal": item})
    for group, originals in (
        ("claims", pack["claim_index"]),
        ("observations", pack["observations"]),
    ):
        original_by_id = {item["id"]: item for item in originals}
        review_by_id = {item["id"]: item for item in case[group]}
        frozen_by_id = {item["id"]: item for item in frozen[group]}
        assert set(original_by_id) == set(review_by_id) == set(frozen_by_id), (scope, group)
        assert len(review_by_id) == len(case[group]), (scope, group)
        for cid, review in review_by_id.items():
            original = original_by_id[cid]
            final_candidate = frozen_by_id[cid]
            assert review["proposal"] == original["proposal"], (scope, cid, "original proposal")
            assert without_coordinates(original["proposal"]) == without_coordinates(
                final_candidate["proposal"]
            ), (scope, cid, "frozen proposal")
            if group == "claims":
                for key in ("path", "subject_id", "field", "kind"):
                    assert review[key] == original[key], (scope, cid, key)
                for key in ("subject_id", "field", "kind"):
                    assert original[key] == final_candidate[key], (scope, cid, key)
            destination, item = destinations.get(cid, ("unaccounted", {}))
            assert destination != "unaccounted", (scope, cid)
            if destination in {"accepted", "observations"}:
                if group == "claims":
                    assert item["subject_id"] == original["subject_id"], (scope, cid)
                    slot = pack["cards"][original["subject_id"]]
                    iri = slot["fields"][original["field"]]["iri"]
                    assert item["predicate_iri"] == iri, (scope, cid)
                else:
                    assert without_coordinates(item["proposal"]) == without_coordinates(
                        original["proposal"]
                    ), (scope, cid)
            overall = review.get("overall_verdict", review["status"])
            content = review.get("content_verdict", review["status"])
            assert overall in {"supported", "unsupported", "undetermined"}
            rows.append({
                "scope_id": scope,
                "candidate_id": cid,
                "kind": original.get("kind", "observation"),
                "path": review.get("path"),
                "subject_id": original.get("subject_id"),
                "field": original.get("field"),
                "program_destination": destination,
                "overall_verdict": overall,
                "content_verdict": content,
                "reason": review.get("overall_reason", review["reason"]),
                "program_issues": item.get("issues", []),
                "matching": "scope + original id/path/subject/field/kind + exact original proposal + frozen proposal excluding coordinates/span_id/replay_issue metadata",
            })
    coverage.append({
        "scope_id": scope,
        "subjects_reviewed": len(case["subjects"]),
        "claims_reviewed": len(case["claims"]),
        "observations_reviewed": len(case["observations"]),
        "review_input_sha256": digest(pack_path),
    })


def counts(items):
    return {
        "count": len(items),
        "overall_verdicts": dict(Counter(x["overall_verdict"] for x in items)),
        "content_verdicts": dict(Counter(x["content_verdict"] for x in items)),
    }


data = {
    "review_type": "Independent assistant source review; not expert gold or full-document precision/recall",
    "matching_passed": True,
    "unreviewed_scope_status": [
        {
            "scope_id": path.parent.name,
            "status": read(path).get("status"),
            "error_code": read(path).get("error_code"),
            "candidate_proposal_available": (path.parent / "candidates/proposal.json").exists(),
            "reason": "No independent review provided; never treated as quality pass",
        }
        for path in sorted((root / "C").glob("*/result.json"))
        if path.parent.name not in {c["scope_id"] for c in cases}
    ],
    "coverage": coverage,
    "all_candidates": counts(rows),
    "accepted_claims": counts([x for x in rows if x["program_destination"] == "accepted"]),
    "accepted_observations": counts([x for x in rows if x["program_destination"] == "observations"]),
    "by_scope": {
        scope: {
            "all_candidates": counts([x for x in rows if x["scope_id"] == scope]),
            "accepted_claims": counts([x for x in rows if x["scope_id"] == scope and x["program_destination"] == "accepted"]),
            "accepted_observations": counts([x for x in rows if x["scope_id"] == scope and x["program_destination"] == "observations"]),
        }
        for scope in sorted({x["scope_id"] for x in rows})
    },
    "by_destination": {
        destination: counts([x for x in rows if x["program_destination"] == destination])
        for destination in sorted({x["program_destination"] for x in rows})
    },
    "records": rows,
}
output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in data.items() if k not in {"records", "by_scope", "coverage"}}, ensure_ascii=False, indent=2))
