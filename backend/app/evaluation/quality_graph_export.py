"""Read-only, reference-free graph export: ``python -m app.evaluation.quality_graph_export RUN``.

Saved validation is not a semantic accuracy score. The full graph retains every
saved ``passed`` candidate, including negative/conditional assertions. Positive
projection additionally checks exact current references and actual directed
reachability; it never merges names, upgrades revisions, or invents edges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from copy import deepcopy
from pathlib import Path
from typing import Any

EXPORT_VERSION = "evaluation-quality-graph-export-v1"
KINDS = {"entity": "nodes", "property": "properties", "relationship": "relationships"}


def _ref_key(value):
    if (not isinstance(value, dict) or not isinstance(value.get("candidate_id"), str)
            or not value["candidate_id"] or type(value.get("revision")) is not int
            or value["revision"] < 1):
        return None
    return value["candidate_id"], value["revision"]


def _references(candidate):
    required = {"subject"} if candidate["kind"] != "entity" else set()
    if candidate["kind"] == "relationship":
        required.add("object")
    for role in ("subject", "object", "path_root"):
        if role in required or candidate.get(role) is not None:
            yield role, candidate.get(role), "entity"
    for index, reference in enumerate(candidate.get("dependency_refs", [])):
        yield f"dependency_refs[{index}]", reference, None
    if candidate.get("scope") is not None:
        yield "scope.subject", candidate["scope"].get("subject"), "entity"


def _document_hash(run, candidates):
    hashes = sorted({anchor["document_hash"] for candidate in candidates
                     for source in candidate.get("provenance", [])
                     if source.get("kind") == "document"
                     for anchor in source.get("anchors", []) if anchor.get("document_hash")})
    supplied = run.get("document_hash")
    if supplied:
        return supplied if not hashes or hashes == [supplied] else None, hashes
    return hashes[0] if len(hashes) == 1 else None, hashes


def export_quality_graph(run: dict[str, Any]) -> dict[str, Any]:
    """Return detached JSON data. Only the supplied run is read; no scoring occurs.

    Duplicate IDs, including multiple revisions, are preserved in the full
    graph but quarantined from positive projection. Reference problems remove
    their owners and dependents to a fixed point, without changing saved status.
    """
    if not isinstance(run, dict) or not isinstance(run.get("candidates"), list):
        raise ValueError("run must be an object containing a candidates array")
    candidates = deepcopy(run["candidates"])
    for candidate in candidates:
        if not _ref_key(candidate) or candidate.get("kind") not in KINDS:
            raise ValueError("each candidate requires a nonempty ID, positive revision and kind")
    by_id = defaultdict(list)
    for index, candidate in enumerate(candidates):
        by_id[candidate["candidate_id"]].append(index)
    document_hash, source_hashes = _document_hash(run, candidates)
    reasons: dict[int, list[str]] = defaultdict(list)
    checks: dict[int, list[dict]] = defaultdict(list)
    targets: dict[int, set[int]] = defaultdict(set)

    def root_candidate(candidate):
        return (candidate["kind"] == "entity" and document_hash is not None
                and candidate.get("identity", {}).get("document_root") == document_hash)

    for index, candidate in enumerate(candidates):
        if len(by_id[candidate["candidate_id"]]) != 1:
            reasons[index].append("duplicate_candidate_id")
        if candidate.get("validation_status") != "passed":
            reasons[index].append("validation_not_passed")
        if candidate.get("assertion_status", "affirmed") != "affirmed":
            reasons[index].append("assertion_not_affirmed")
        if candidate.get("condition_anchors") or candidate.get("condition_provenance_indexes"):
            reasons[index].append("conditioned_assertion")
        if candidate.get("review_status") == "rejected":
            reasons[index].append("review_rejected")
        if candidate.get("identity", {}).get("document_root") and not root_candidate(candidate):
            reasons[index].append("document_root_hash_unresolved_or_mismatched")
        if candidate["kind"] != "entity":
            if not candidate.get("bindings"):
                reasons[index].append("binding_evidence_missing")
            for binding in candidate.get("bindings", []):
                if (binding.get("subject_candidate_id")
                        != (candidate.get("subject") or {}).get("candidate_id")
                        or binding.get("object_candidate_id")
                        != (candidate.get("object") or {}).get("candidate_id")
                        or binding.get("predicate_iri") != candidate.get("predicate_iri")
                        or not (binding.get("anchors") or binding.get("provenance_indexes"))):
                    reasons[index].append("binding_evidence_inconsistent")
            scope = candidate.get("scope")
            if scope and _ref_key(scope.get("subject")) != _ref_key(candidate.get("subject")):
                reasons[index].append("scope_subject_mismatch")
        for role, reference, expected_kind in _references(candidate):
            record = {"role": role, "reference": deepcopy(reference)}
            key = _ref_key(reference)
            matches = by_id.get(key[0], []) if key else []
            problem = None
            if key is None:
                problem = "invalid_or_missing_reference"
            elif not matches:
                problem = "missing_candidate"
            elif len(matches) != 1:
                problem = "ambiguous_candidate_id"
            else:
                target_index = matches[0]
                target = candidates[target_index]
                if target["revision"] != key[1]:
                    problem = "stale_revision"
                elif expected_kind and target["kind"] != expected_kind:
                    problem = "endpoint_not_entity"
                elif (reference.get("class_iri") is not None
                      and reference["class_iri"] != target.get("class_iri")):
                    problem = "reference_class_mismatch"
                elif (reference.get("instance_iri") is not None
                      and reference["instance_iri"]
                      != target.get("identity", {}).get("instance_iri")):
                    problem = "reference_identity_mismatch"
                elif role == "path_root" and not root_candidate(target):
                    problem = "path_root_not_current_document_root"
                else:
                    targets[index].add(target_index)
                    record["target_source_index"] = target_index
            record["status"] = problem or "resolved"
            checks[index].append(record)
            if problem:
                reasons[index].append(f"{role}:{problem}")

    positive = {index for index in range(len(candidates)) if not reasons[index]}
    while True:
        withdrawn = {index for index in positive if not targets[index] <= positive}
        if not withdrawn:
            break
        for index in withdrawn:
            reasons[index].append("reference_target_not_positive")
        positive -= withdrawn

    roots = {index for index in positive if root_candidate(candidates[index])}

    def endpoint(candidate, role):
        return by_id[candidate[role]["candidate_id"]][0]

    def reachable_subset(available, root_index):
        outgoing = defaultdict(list)
        for index in available:
            candidate = candidates[index]
            if candidate["kind"] == "relationship":
                outgoing[endpoint(candidate, "subject")].append(endpoint(candidate, "object"))
        reached = {root_index} & available
        queue = deque(sorted(reached))
        while queue:
            for target in outgoing[queue.popleft()]:
                if target in available and target not in reached:
                    reached.add(target)
                    queue.append(target)
        selected = set(reached)
        for index in available:
            candidate = candidates[index]
            if (candidate["kind"] != "entity" and endpoint(candidate, "subject") in reached
                    and (candidate["kind"] != "relationship"
                         or endpoint(candidate, "object") in reached)):
                selected.add(index)
        return selected

    root_positive = set()
    for root_index in sorted(roots):
        # A stored path root constrains the traversal that may use this candidate.
        # Multiple metadata roots must not silently lend each other provenance.
        available = {index for index in positive if not candidates[index].get("path_root")
                     or endpoint(candidates[index], "path_root") == root_index}
        selected = reachable_subset(available, root_index)
        # Keep every exported graph reference self-contained. Disconnected
        # support records do not become reachable just by being dependencies.
        while True:
            narrowed = reachable_subset({index for index in selected
                                         if targets[index] <= selected}, root_index)
            if narrowed == selected:
                break
            selected = narrowed
        root_positive |= selected

    for index, candidate in enumerate(candidates):
        candidate["graph_export"] = {
            "source_index": index,
            "is_current_document_root": root_candidate(candidate),
            "positive_eligible": index in positive,
            "root_reachable_positive": index in root_positive,
            "positive_exclusion_reasons": sorted(set(reasons[index])),
            "root_projection_exclusion_reason": (
                None if index in root_positive else
                "not_positive_eligible" if index not in positive else
                "not_root_reachable_or_support_outside_root_subgraph"
            ),
            "reference_checks": checks[index],
        }

    def graph(selected):
        output = {name: [] for name in KINDS.values()}
        for index in sorted(selected):
            candidate = candidates[index]
            output[KINDS[candidate["kind"]]].append(deepcopy(candidate))
        output["counts"] = {name: len(output[name]) for name in KINDS.values()}
        return output

    validated = {index for index, candidate in enumerate(candidates)
                 if candidate.get("validation_status") == "passed"}
    return {
        "schema_version": 1,
        "export_version": EXPORT_VERSION,
        "source": {**{name: run.get(name) for name in (
            "input_id", "completion", "degraded", "extractor_version",
            "scheduler_version", "transport_version",
        )}, "document_hash": document_hash, "observed_document_hashes": source_hashes},
        "interpretation": {
            "reference_used": False,
            "semantic_accuracy_assessed": False,
            "validation_is_accuracy": False,
            "unscored_is_correct": False,
            "full_graph": "All saved validation_status=passed candidates, including negative, "
                          "conditional and review-rejected assertions; not an accuracy claim.",
            "root_positive_graph": "Affirmed, unconditional, non-rejected candidates with exact "
                                   "current references, reachable through actual directed "
                                   "relationships from document-matching metadata roots. "
                                   "All retained graph references resolve inside this subgraph.",
            "identity_policy": "Exact candidate ID and revision only. No name merging, "
                               "revision upgrades, reference answers or source re-verification.",
            "metadata_roots": "Document metadata roots are retained as graph scaffolding, "
                              "not counted as extracted entity discoveries.",
        },
        "summary": {
            "input_candidates": len(candidates),
            "saved_validation_status_counts": dict(Counter(
                candidate.get("validation_status", "missing") for candidate in candidates)),
            "validated_candidates": len(validated),
            "positive_candidates": len(positive),
            "root_reachable_positive_candidates": len(root_positive),
            "root_reachable_metadata_roots": len(roots & root_positive),
            "validated_assertion_status_counts": dict(Counter(
                candidates[index].get("assertion_status", "affirmed")
                for index in sorted(validated))),
            "duplicate_candidate_ids": sorted(cid for cid, values in by_id.items()
                                              if len(values) != 1),
        },
        "full_validated_candidate_graph": graph(validated),
        "root_reachable_positive_graph": {
            "roots": [{"candidate_id": candidates[index]["candidate_id"],
                       "revision": candidates[index]["revision"]}
                      for index in sorted(roots & root_positive)],
            **graph(root_positive),
        },
        "positive_projection_exclusions": [
            {"candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
             "validation_status": candidate.get("validation_status"),
             **deepcopy(candidate["graph_export"])}
            for index, candidate in enumerate(candidates) if index not in positive
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Saved run.json; no sibling files are read")
    args = parser.parse_args(argv)
    try:
        source = args.run.read_bytes()
        exported = export_quality_graph(json.loads(source))
        exported["source"]["run_sha256"] = hashlib.sha256(source).hexdigest()
        rendered = json.dumps(exported, ensure_ascii=False, indent=2, allow_nan=False)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    sys.stdout.write(rendered + "\n")


if __name__ == "__main__":
    main()
