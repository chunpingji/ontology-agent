"""Read-only final quality audit; independent references never enter extraction.

Run with ``python -m app.evaluation.quality_analysis --prepared DIR --run DIR
--reference FILE``. JSON goes to stdout. Active runs are rejected. Existing
scorers, candidates, frozen references and runtime artifacts are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.evaluation.graph_metrics import evaluate_graph
from app.evaluation.root_guided_analysis import root_paths, score_snapshot_history, summarize_calls

VERSION = "quality-independent-audit-v1"
KINDS = ("entity", "property", "relationship")
CITATION_ERRORS = {
    "non_atomic_source_citation", "unknown_or_disallowed_citation_source",
    "citation_fragment_source_mismatch", "citation_fact_permission_mismatch",
    "ambiguous_source_quote", "source_excerpt_mismatch", "source_quote_outside_scope",
    "scope_violation",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _positive(candidate):
    return (
        candidate.get("validation_status") == "passed"
        and candidate.get("review_status") != "rejected"
        and candidate.get("assertion_status", "affirmed") == "affirmed"
        and not candidate.get("condition_anchors")
        and not candidate.get("condition_provenance_indexes")
        and (candidate.get("kind") == "entity" or bool(candidate.get("bindings")))
    )


def _refs(candidate):
    for name in ("subject", "object", "path_root"):
        if candidate.get(name):
            yield name, candidate[name]
    for index, ref in enumerate(candidate.get("dependency_refs", [])):
        yield f"dependency_refs[{index}]", ref
    if (candidate.get("scope") or {}).get("subject"):
        yield "scope.subject", candidate["scope"]["subject"]


def audit_references(candidates):
    """Distinguish broken graph references from unmatched silver endpoints."""
    by_id = {c["candidate_id"]: c for c in candidates}
    counts = Counter(c["candidate_id"] for c in candidates)
    failures = [{"candidate_id": cid, "field": "candidate_id",
                 "reason": "duplicate_current_candidate_id", "count": count}
                for cid, count in sorted(counts.items()) if count > 1]
    for candidate in candidates:
        for index, binding in enumerate(candidate.get("bindings", [])):
            expected = {
                "subject_candidate_id": (candidate.get("subject") or {}).get("candidate_id"),
                "object_candidate_id": (candidate.get("object") or {}).get("candidate_id"),
                "predicate_iri": candidate.get("predicate_iri"),
            }
            for name, value in expected.items():
                if binding.get(name) != value:
                    failures.append({
                        "candidate_id": candidate["candidate_id"],
                        "field": f"bindings[{index}].{name}", "expected": value,
                        "observed": binding.get(name), "reason": "binding_metadata_mismatch",
                    })
            for value in binding.get("provenance_indexes", []):
                if type(value) is not int or not 0 <= value < len(candidate.get("provenance", [])):
                    failures.append({"candidate_id": candidate["candidate_id"],
                                     "field": f"bindings[{index}].provenance_indexes",
                                     "reason": "binding_provenance_index_invalid", "index": value})
        for field, ref in _refs(candidate):
            target = by_id.get(ref.get("candidate_id"))
            reason = None
            if target is None:
                reason = "missing_candidate"
            elif counts[ref["candidate_id"]] != 1:
                reason = "duplicate_current_candidate_id"
            elif ref.get("revision", 1) != target.get("revision", 1):
                reason = "stale_candidate_revision"
            elif ref.get("class_iri") is not None and ref["class_iri"] != target.get("class_iri"):
                reason = "reference_class_mismatch"
            elif (ref.get("instance_iri") is not None
                  and ref["instance_iri"] != target.get("identity", {}).get("instance_iri")):
                reason = "reference_instance_mismatch"
            elif _positive(candidate) and field != "scope.subject" and not _positive(target):
                reason = "positive_candidate_depends_on_nonpositive_target"
            if reason:
                failures.append({"candidate_id": candidate["candidate_id"], "field": field,
                                 "reference": ref, "reason": reason,
                                 "owner_validation_status": candidate.get("validation_status")})
    return {
        "failure_count": len(failures),
        "by_reason": dict(Counter(f["reason"] for f in failures)),
        "failures": failures,
        "interpretation": "Graph ID/revision and binding metadata integrity; not full semantic "
                          "validation. Reference matching failures are separate.",
    }


def audit_task_failures(tasks, candidates=(), diagnostics=()):
    """Count a code once per recorded task attempt, not once per mirrored field."""
    attempts = []
    for index, event in enumerate(tasks, 1):
        codes = {str(code) for code in event.get("issues", [])}
        if event.get("reason"):
            codes.add(str(event["reason"]))
        codes.update(str(value["code"]) for value in event.get("outcomes", [])
                     if value.get("code") and value.get("code") not in {"passed", "no_candidates"})
        if codes:
            attempts.append({"attempt_index": index,
                             "task_id": event.get("task", {}).get("task_id"),
                             "status": event.get("status"), "codes": sorted(codes)})
    code_counts = Counter(code for row in attempts for code in row["codes"])
    candidate_issues = Counter(issue["code"] for c in candidates
                               for issue in c.get("validation_issues", []) if issue.get("code"))
    return {
        "task_attempt_count": len(tasks), "attempts_with_issues": len(attempts),
        "task_attempt_code_counts": dict(sorted(code_counts.items())),
        "citation_error_attempt_count": sum(bool(set(row["codes"]) & CITATION_ERRORS)
                                            for row in attempts),
        "citation_error_code_counts": {code: code_counts[code]
                                       for code in sorted(CITATION_ERRORS) if code_counts[code]},
        "candidate_validation_issue_counts": dict(sorted(candidate_issues.items())),
        "run_diagnostic_codes": sorted(set(diagnostics)), "attempts": attempts,
        "interpretation": "Issues and outcomes are deduplicated within one attempt. Candidate "
                          "issues and run diagnostics are separate projections, not added totals.",
    }


def schema_reachability(schema, root_classes):
    ancestors = {}
    for cls in schema:
        seen, pending = set(), [cls]
        while pending:
            value = pending.pop()
            if value in seen:
                continue
            seen.add(value)
            pending.extend(p for p in schema.get(value, {}).get("parents", [])
                           if isinstance(p, str))
        ancestors[cls] = seen
    seen, pending, edges, missing = set(), list(root_classes), set(), set()
    while pending:
        cls = pending.pop()
        if cls in seen or cls not in schema:
            continue
        seen.add(cls)
        for relation in schema[cls].get("relationships", []):
            for target in relation.get("range", []):
                subclasses = {value for value in schema if target in ancestors[value]}
                if not subclasses:
                    missing.add(target)
                for value in subclasses:
                    edges.add((cls, relation["iri"], value))
                    pending.append(value)
    return {
        "root_classes": sorted(set(root_classes)), "reachable_class_count": len(seen),
        "schema_class_count": len(schema), "reachable_class_iris": sorted(seen),
        "legal_class_relationship_triples": len(edges),
        "distinct_relationship_predicates": len({p for _, p, _ in edges}),
        "class_property_slot_count": sum(len(schema[cls].get("properties", [])) for cls in seen),
        "distinct_property_predicates": len({p["iri"] for cls in seen
                                              for p in schema[cls].get("properties", [])}),
        "missing_range_class_iris": sorted(missing),
        "interpretation": "Static legal closure, not a count of document facts or false negatives.",
    }


def audit_coverage(checkpoint, plan, candidates, schema=None):
    coverage = checkpoint.get("coverage", plan.get("coverage", []))
    routes = checkpoint.get("route_cache", plan.get("routes", {}))
    route_entries = [entry for cache in routes.values() for entry in cache.get("ledger", [])]
    negatives = [entry for entry in route_entries
                 if entry.get("status") == "route_negative_not_extracted"]
    statuses = Counter(row.get("status", "unknown") for row in coverage)
    successful = {"examined", "searched", "no_evidence"}
    outstanding = [row for row in coverage if row.get("status") not in successful]
    queue = checkpoint.get("queue", [])
    conflicts = [c["candidate_id"] for c in candidates if c.get("validation_status") == "conflict"]
    records = {entry["record_id"] for entry in route_entries}
    expected = {
        (candidate["candidate_id"], predicate["iri"], record)
        for candidate in candidates if candidate.get("kind") == "entity" and _positive(candidate)
        for key in ("properties", "relationships")
        for predicate in (schema or {}).get(candidate.get("class_iri"), {}).get(key, [])
        for record in records
    }
    examined = {(row.get("subject_id"), row.get("predicate_iri"), row.get("record_id"))
                for row in coverage if row.get("status") in successful}
    return {
        "coverage_event_count": len(coverage), "status_counts": dict(statuses),
        "unique_subject_predicate_records": len({
            (row.get("subject_id"), row.get("predicate_iri"), row.get("record_id"))
            for row in coverage if row.get("record_id") and row.get("predicate_iri")
        }),
        "routing_record_decisions": len(route_entries),
        "route_negative_decisions_not_extracted": len(negatives),
        "queued_work_count": len(queue), "outstanding_coverage": outstanding,
        "conflict_candidate_ids": conflicts,
        "recorded_source_universe_size": len(records),
        "expected_instance_predicate_record_count": len(expected),
        "unexamined_instance_predicate_record_count": len(expected - examined),
        "all_recorded_instance_slots_examined": (
            bool(expected) and not (expected - examined) and not queue
            and not outstanding and not conflicts
        ),
        "full_document_exhaustive_completion_demonstrated": False,
        "interpretation": "Route-negative decisions are unsearched. An examined record may contain "
                          "no supported fact; optional schema slots are not automatically FNs. "
                          "The recorded source universe is not proof that every source region is "
                          "represented. A drained queue does not establish document completeness.",
    }


def audit_merges(checkpoint, plan):
    events = checkpoint.get("merge_log", plan.get("instance_merges", []))
    aliases = checkpoint.get("aliases", {})
    merge_events = [event for event in events if event.get("alias_map")]
    conflicts, mentions = {}, {}
    for event in events:
        for issue in event.get("conflicts", []):
            conflicts[_json(issue)] = issue
        for rows in event.get("mentions", {}).values():
            for mention in rows:
                mentions[(mention["candidate_id"], mention.get("revision", 1))] = mention
    reference_maps = [mapping for event in events
                      for audit in [event, event.get("reconciliation_references", {})]
                      for mapping in audit.get("reference_map", [])]
    return {
        "audit_event_count": len(events), "merge_event_count": len(merge_events),
        "merged_source_id_count": len(aliases), "alias_map": aliases,
        "original_mention_revision_count": len(mentions),
        "unique_identity_conflicts": list(conflicts.values()),
        "identity_conflict_code_counts": dict(Counter(c["code"] for c in conflicts.values())),
        "reference_mapping_count": len(reference_maps),
        "changed_reference_mapping_count": sum(
            any(mapping["source"].get(key) != mapping["canonical"].get(key)
                for key in ("candidate_id", "revision"))
            for mapping in reference_maps
        ),
        "reconciliation_event_count": sum("reconciliation_references" in e for e in events),
        "invalidations": checkpoint.get("invalidations", plan.get("invalidations", [])),
    }


def property_review_queue(candidates):
    groups = defaultdict(list)
    for candidate in candidates:
        if candidate.get("kind") == "property":
            key = (candidate.get("subject", {}).get("candidate_id"), candidate.get("predicate_iri"))
            groups[key].append(candidate)
    rows = []
    for (subject, predicate), group in sorted(groups.items()):
        signatures = {_json({key: c.get(key) for key in (
            "literal", "assertion_status", "applicable_at", "condition_anchors",
        )}) for c in group}
        if len(signatures) > 1 or any(
            c.get("assertion_status", "affirmed") != "affirmed"
            or c.get("literal", {}).get("kind") in {"number", "range", "comparison"}
            for c in group
        ):
            rows.append({
                "subject_candidate_id": subject, "predicate_iri": predicate,
                "distinct_value_context_count": len(signatures),
                "candidates": [{key: c.get(key) for key in (
                    "candidate_id", "revision", "literal", "assertion_status", "applicable_at",
                    "validation_status", "condition_anchors", "condition_provenance_indexes",
                    "provenance", "bindings",
                )} for c in group],
            })
    return {
        "groups": rows,
        "interpretation": "Source review queue, not additional TP/FP labels. Preserve ranges, "
                          "approximate/comparison operators, units, conversions, polarity, "
                          "conditions and temporal context. Values may have distinct valid "
                          "contexts; competing assertions must not be averaged or erased.",
    }


def summarize_quality_calls(traces, calls):
    """Keep source-record routing distinct from extraction fact-target fragments."""
    result = summarize_calls(traces, calls)
    routing_rows = []
    for row, trace in zip(result["calls"], traces, strict=True):
        if trace.get("stage") != "route_records":
            continue
        payload = trace.get("user", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        records = payload.get("records", {})
        row["task_kind"] = "retrieval"
        row["distinct_menu_predicate_count"] = len(payload.get("local_menu", {}))
        row["source_record_count"] = len(records)
        row["source_fragment_count"] = sum(len(r.get("source", [])) for r in records.values())
        row["source_characters"] = sum(len(text) for record in records.values()
                                       for text in record.get("source", []))
        for key in ("target_fragment_count", "target_evidence_count", "target_characters"):
            row.pop(key)
        routing_rows.append(row)
    if routing_rows:
        summary = result["by_stage"].pop("unknown.route_records", {})
        summary.update(
            source_record_count_total=sum(r["source_record_count"] for r in routing_rows),
            route_menu_predicate_count_max=max(r["distinct_menu_predicate_count"]
                                              for r in routing_rows),
            source_character_count_total=sum(r["source_characters"] for r in routing_rows),
        )
        result["by_stage"]["retrieval.route_records"] = summary
    return result


def analyze_quality_artifacts(run, result, checkpoint, plan, schema, ir, reference, *,
                              traces=(), calls=(), snapshots=()):
    candidates = run.get("candidates", [])
    if result.get("completion") != run.get("completion"):
        raise ValueError("run/result completion mismatch")
    if checkpoint.get("candidates", candidates) != candidates:
        raise ValueError("run/checkpoint candidates mismatch")
    metrics = evaluate_graph(run, reference, ir=ir)
    roots = {c["class_iri"] for c in candidates
             if c.get("kind") == "entity" and c.get("identity", {}).get("document_root")}
    graph_refs = audit_references(candidates)
    resumed = (result.get("elapsed_seconds_total", 0)
               > result.get("elapsed_seconds_this_segment", float("inf")) + 0.000001)
    history = ({"status": "unavailable", "snapshot_count": len(snapshots),
                "first_observed_reference_correct_root_path_seconds": None,
                "reason": "Resumed run has segment-relative snapshots; no invented total timing."}
               if resumed else score_snapshot_history(snapshots, reference, ir))
    history["last_snapshot_equals_final_run"] = (snapshots[-1]["candidates"] == candidates
                                                 if snapshots else None)
    return {
        "analysis_version": VERSION, "completion": run.get("completion"),
        "annotation_level": reference["annotation_level"],
        "metrics_recomputed": True, "primary_metric": metrics["primary_metric"],
        "raw_extracted_only": metrics["raw"]["extracted_only"],
        "validated_extracted_only": metrics["validated"]["extracted_only"],
        "semantic_interpretation": metrics["interpretation"],
        "root_paths": root_paths(metrics, reference, candidates),
        "snapshot_history": history, "graph_reference_integrity": graph_refs,
        "provenance_replay": metrics["provenance_replay"],
        "task_failures": audit_task_failures(run.get("tasks", []), candidates,
                                              run.get("diagnostics", [])),
        "instance_registry": audit_merges(checkpoint, plan),
        "schema_reachability": schema_reachability(schema, roots),
        "coverage": audit_coverage(checkpoint, plan, candidates, schema),
        "property_review": property_review_queue(candidates),
        "calls": summarize_quality_calls(traces, calls),
        "elapsed_seconds_total": result.get("elapsed_seconds_total"),
        "elapsed_interpretation": "Observed cost only. Speed is not the experimental objective.",
        "model_server_slots_unchanged": (result["slots_before"] == result["slots_after"]
                                         if "slots_before" in result and "slots_after" in result
                                         else None),
        "model_server_slot_note": "Inference-server /slots snapshots describe model context/cache "
                                  "state, not production facts or evidence of database writes.",
    }


def analyze_quality_run(prepared, run_directory, *, reference_path):
    prepared, directory = Path(prepared).resolve(), Path(run_directory).resolve()
    if (directory / "run.in_progress.json").exists():
        raise ValueError("active run: final quality scoring requires finalized artifacts")

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def lines(name):
        path = directory / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()] if path.is_file() else []

    manifest, ir = read(prepared / "manifest.json"), read(prepared / "ir.json")
    for name in ("ir", "schema"):
        expected = manifest.get(f"{name}_hash")
        if expected and digest(prepared / f"{name}.json") != expected:
            raise ValueError(f"prepared {name} hash differs from manifest")
    run, result, checkpoint, plan = [read(directory / f"{name}.json")
                                     for name in ("run", "result", "checkpoint", "plan")]
    for key in ("document_hash", "runtime_hash", "ontology_hash"):
        if not result.get(key) or result[key] != manifest.get(key):
            raise ValueError(f"run {key} differs from manifest")
    if result.get("model_settings") != manifest.get("settings"):
        raise ValueError("run model settings differ from manifest")
    for name in ("ir", "schema"):
        if result.get("input_hashes", {}).get(name) != manifest.get(f"{name}_hash"):
            raise ValueError(f"run {name} input hash differs from manifest")
    summary_hash = result.get("input_hashes", {}).get("summaries")
    if result.get("mode") == "quality_guided_summary" and summary_hash is None:
        raise ValueError("quality run has no recorded summary hash")
    if summary_hash is not None:
        if (digest(prepared / "summaries.json") != manifest.get("summary_hash")
                or summary_hash != manifest.get("summary_hash")):
            raise ValueError("summary hash differs between prepared source, manifest and run")
    if ir.get("document_hash") != manifest["document_hash"]:
        raise ValueError("IR document identity differs from manifest")
    reference = read(reference_path)
    report = analyze_quality_artifacts(
        run, result, checkpoint, plan, read(prepared / "schema.json"), ir, reference,
        traces=lines("trace.jsonl"), calls=lines("calls.jsonl"), snapshots=lines("snapshots.jsonl"),
    )
    metrics_file = directory / "metrics.json"
    if metrics_file.is_file() and read(metrics_file) != evaluate_graph(run, reference, ir=ir):
        raise ValueError("saved metrics differ from independently recomputed metrics")
    report["saved_metrics_verified"] = metrics_file.is_file()
    report["artifact_sha256"] = {name: digest(directory / name) for name in (
        "run.json", "result.json", "checkpoint.json", "plan.json", "metrics.json",
        "trace.jsonl", "calls.jsonl", "snapshots.jsonl",
    ) if (directory / name).is_file()}
    report["reference_sha256"] = digest(reference_path)
    report["prepared_sha256"] = {name: digest(prepared / name)
                                  for name in ("manifest.json", "ir.json", "schema.json",
                                               "summaries.json") if (prepared / name).is_file()}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    print(json.dumps(analyze_quality_run(args.prepared, args.run, reference_path=args.reference),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
