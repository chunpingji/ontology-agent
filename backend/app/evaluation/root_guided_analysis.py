"""Offline audit of saved graph runs; emits JSON to stdout and never calls a model.

Reference annotations enter only this analysis process, after extraction. Root
paths use the existing exact, source-scoped graph scorer, including its metadata
root exclusion. A first-path time requires recorded full candidate snapshots;
model responses and model-call completion times are not publication evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path

from app.evaluation.compare import compare_runs
from app.evaluation.graph_metrics import evaluate_graph


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _lines(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("trace request/context must be a JSON object")
    return value


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize_calls(traces, calls):
    """Count the saved wire menu and input-token measurements, without estimates."""
    numbered = {}
    for call in calls:
        number = call.get("call")
        if type(number) is not int or number in numbered:
            raise ValueError("calls require unique integer call numbers")
        numbered[number] = call
    ordinal_safe = len(traces) == len(calls) and not any(c.get("error") for c in calls)
    rows = []
    used_calls = set()
    for index, trace in enumerate(traces, 1):
        request = _object(trace.get("user", {}))
        context = _object(request.get("context", {}))
        task = context.get("task", {})
        definition = task.get("predicate_definition", {})
        classes = definition.get("classes", {})
        fragments = context.get("fragments", [])
        targets = [f for f in fragments if f.get("purpose") == "target"]
        hints = definition.get("extraction_hints", {})
        predicates = {item["iri"] for value in classes.values()
                      for key in ("properties", "relationships")
                      for item in value.get(key, []) if item.get("iri")}
        if task.get("predicate_iri"):
            predicates.add(task["predicate_iri"])
        response = trace.get("response")
        response_map = response if isinstance(response, dict) else {}
        number = trace.get("model_call_number")
        method = "explicit_model_call_number" if number is not None else None
        if number is None and ordinal_safe:
            number, method = calls[index - 1]["call"], "ordinal_complete_error_free_log"
        call = numbered.get(number) if number is not None else None
        if number is not None and (call is None or number in used_calls):
            raise ValueError("trace model call number is missing or repeated")
        if call:
            used_calls.add(number)
        decisions = response_map.get("decisions", [])
        row = {
            "trace_line": index,
            "task_id": trace.get("task_id"),
            "stage": trace.get("stage"),
            "task_kind": task.get("task_kind"),
            "predicate_iri": task.get("predicate_iri"),
            "input_tokens": trace.get("input_tokens"),
            "class_menu_count": len(classes),
            "distinct_menu_predicate_count": len(predicates),
            "object_candidate_count": len(task.get("object_candidates", [])),
            "target_fragment_count": len(targets),
            "target_evidence_count": len({f["anchor"]["evidence_id"] for f in targets}),
            "target_characters": sum(len(f.get("text", "")) for f in targets),
            "gliner_span_suggestion_count": len(hints.get("gliner_span_suggestions", [])),
            "response_characters": len(json.dumps(response, ensure_ascii=False,
                                                   separators=(",", ":"))),
            "entity_proposals": len(response_map.get("entities", [])),
            "assertion_proposals": len(response_map.get("assertions", [])),
            "entity_type_decisions": len(decisions),
            "entity_type_supported": sum(d.get("supported") is True for d in decisions),
            "entity_type_unsupported": sum(d.get("supported") is False for d in decisions),
            "model_call_number": number,
            "call_alignment": method,
            "call_wall_seconds": call.get("wall_seconds") if call else None,
        }
        rows.append(row)
    by_stage = defaultdict(list)
    for row in rows:
        by_stage[f"{row['task_kind'] or 'unknown'}.{row['stage'] or 'unknown'}"].append(row)
    stages = {}
    for stage, values in sorted(by_stage.items(), key=lambda item: str(item[0])):
        tokens = [r["input_tokens"] for r in values if _number(r["input_tokens"])]
        stages[stage] = {
            "traced_calls": len(values),
            "input_tokens_sum": sum(tokens),
            "input_tokens_max": max(tokens, default=None),
            "input_tokens_mean": sum(tokens) / len(tokens) if tokens else None,
            "class_menu_count_max": max(r["class_menu_count"] for r in values),
            "class_menu_count_mean": sum(r["class_menu_count"] for r in values) / len(values),
            **{key: sum(r[key] for r in values) for key in
               ("response_characters", "entity_proposals", "assertion_proposals",
                "entity_type_decisions", "entity_type_supported", "entity_type_unsupported")},
            "aligned_call_wall_seconds": sum(r["call_wall_seconds"] for r in values
                                             if _number(r["call_wall_seconds"])),
        }
    return {
        "logged_model_calls": len(calls),
        "traced_model_calls": len(traces),
        "error_calls": [c for c in calls if c.get("error")],
        "calls_without_aligned_trace": sorted(set(numbered) - used_calls),
        "input_tokens_traced_total": sum(r["input_tokens"] for r in rows
                                         if _number(r["input_tokens"])),
        "input_token_measurements_cover_all_logged_calls": (
            bool(calls) and len(used_calls) == len(calls)
            and all(_number(r["input_tokens"]) for r in rows)
        ),
        "output_token_count": None,
        "output_token_note": "Response characters are serialized size, not output tokens.",
        "by_stage": stages,
        "calls": rows,
    }


def _reachable_paths(root_ids, entity_ids, relationships):
    """One deterministic shortest directed path per reachable non-root entity."""
    outgoing = defaultdict(list)
    for item in relationships:
        if item["subject"] in entity_ids and item["object"] in entity_ids:
            outgoing[item["subject"]].append(item)
    found = {}
    queue = deque((root, [root], []) for root in sorted(root_ids & entity_ids))
    visited = set(root_ids & entity_ids)
    while queue:
        root, entities, edges = queue.popleft()
        for edge in sorted(outgoing[entities[-1]], key=lambda item: item["id"]):
            target = edge["object"]
            if target in visited:
                continue
            visited.add(target)
            next_entities, next_edges = [*entities, target], [*edges, edge["id"]]
            found[target] = {
                "root_reference_id": root,
                "target_reference_id": target,
                "entity_reference_ids": next_entities,
                "relationship_reference_ids": next_edges,
            }
            queue.append((root, next_entities, next_edges))
    return found


def root_paths(metrics, reference, candidates):
    """Only independently matched, passed entities and relationships form paths."""
    roots = {e["id"] for e in reference["entities"] if e.get("is_document_root")}
    expected = _reachable_paths(roots, {e["id"] for e in reference["entities"]},
                                reference.get("relationships", []))
    entity_matches = {m["reference_id"]: m["candidate_id"]
                      for m in metrics["validated"]["entity"]["matched"]}
    relationship_matches = {m["reference_id"]: m["candidate_id"]
                            for m in metrics["validated"]["relationship"]["matched"]}
    candidate_map = {c["candidate_id"]: c for c in candidates}
    connected_edges, excluded_edges = [], []
    for edge in reference.get("relationships", []):
        if edge["id"] not in relationship_matches:
            continue
        candidate = candidate_map[relationship_matches[edge["id"]]]
        connected = True
        for role in ("subject", "object"):
            expected_id = entity_matches.get(edge[role])
            endpoint = candidate.get(role) or {}
            expected_candidate = candidate_map.get(expected_id, {})
            if (endpoint.get("candidate_id") != expected_id or expected_id is None
                    or endpoint.get("revision", 1) != expected_candidate.get("revision", 1)):
                connected = False
        (connected_edges if connected else excluded_edges).append(edge)
    matched = _reachable_paths(
        roots, set(entity_matches), connected_edges,
    )
    for path in matched.values():
        path["entity_candidate_ids"] = [entity_matches[e] for e in path["entity_reference_ids"]]
        path["relationship_candidate_ids"] = [relationship_matches[r]
                                               for r in path["relationship_reference_ids"]]
    return {
        "definition": "One shortest directed root path per reachable reference entity; "
                      "all path entities and edges must match the validated graph scorer "
                      "and connect the actual selected candidate IDs and revisions. "
                      "Equivalent duplicate endpoint candidates are not silently merged.",
        "reference_reachable_entity_count": len(expected),
        "matched_reachable_entity_count": len(matched),
        "reachable_entity_recall": len(matched) / len(expected) if expected else None,
        "missed_reachable_reference_ids": sorted(set(expected) - set(matched)),
        "matched_edges_excluded_for_endpoint_candidate_mismatch": [e["id"] for e in excluded_edges],
        "paths": [matched[key] for key in sorted(matched)],
    }


def score_snapshot_history(snapshots, reference, ir):
    timeline, first = [], None
    previous = -1
    for line, snapshot in enumerate(snapshots, 1):
        elapsed = snapshot.get("elapsed_seconds")
        if not _number(elapsed) or elapsed < 0 or elapsed < previous:
            raise ValueError("snapshot elapsed_seconds must be finite and nondecreasing")
        if not isinstance(snapshot.get("candidates"), list):
            raise ValueError("snapshot must contain the full candidates list")
        previous = elapsed
        metrics = evaluate_graph(snapshot, reference, ir=ir)
        paths = root_paths(metrics, reference, snapshot["candidates"])
        current = {
            "snapshot_line": line, "elapsed_seconds": elapsed,
            "validated_extracted_only_micro": metrics["validated"]["extracted_only"]["micro"],
            "matched_root_path_count": paths["matched_reachable_entity_count"],
        }
        timeline.append(current)
        if first is None and paths["paths"]:
            first = {**current, "paths": paths["paths"]}
    return {
        "status": "evaluated" if snapshots else "unavailable",
        "snapshot_count": len(snapshots),
        "first_observed_reference_correct_root_path_seconds": (
            first["elapsed_seconds"] if first else None
        ),
        "first_observation": first,
        "timeline": timeline,
        "interpretation": "First recorded full candidate snapshot with a reference-matched "
                          "root path, not model self-verification or a call-end estimate. "
                          "No snapshot history means no first-path timing claim.",
    }


def analyze_runs(prepared, run_directories, *, reference_path):
    """Verify comparable artifacts and add call, path and snapshot diagnostics."""
    prepared = Path(prepared).resolve()
    # Existing comparison checks identities, frozen settings, source IR hash,
    # candidate counts, completion and exact independent metric recomputation.
    comparison = compare_runs(prepared, run_directories, reference_path=reference_path)
    reference, ir = _read(reference_path), _read(prepared / "ir.json")
    manifest = _read(prepared / "manifest.json")
    schema_path = prepared / "schema.json"
    if manifest.get("schema_hash") and _digest(schema_path) != manifest["schema_hash"]:
        raise ValueError("prepared schema hash differs from manifest")
    rows = []
    limits = []
    for compared in comparison["rows"]:
        directory = Path(compared["run_directory"])
        run, result, metrics = (_read(directory / f"{name}.json")
                                for name in ("run", "result", "metrics"))
        snapshots = _lines(directory / "snapshots.jsonl")
        history = score_snapshot_history(snapshots, reference, ir)
        history["time_basis"] = "elapsed_seconds_this_segment"
        history["resumed_run"] = compared["resumed"]
        history["last_snapshot_candidates_equal_final_run"] = (
            snapshots[-1]["candidates"] == run["candidates"] if snapshots else None
        )
        execution_limits = result.get("execution_limits")
        limits.append(execution_limits)
        rows.append({
            "run_name": directory.name,
            "mode": result["mode"],
            "completion": result["completion"],
            "execution_limits": execution_limits,
            "attempted_tasks_total": compared["attempted_tasks_total"],
            "task_event_count": compared["task_event_count"],
            "elapsed_seconds_total": result["elapsed_seconds_total"],
            "cold_metadata_accounted_seconds": result.get("cold_metadata_accounted_seconds"),
            "summary_generation_seconds_separate": result.get(
                "summary_generation_seconds_separate"
            ),
            "summary_cache": result.get("summary_cache"),
            "raw_extracted_only": metrics["raw"]["extracted_only"],
            "validated_extracted_only": metrics["validated"]["extracted_only"],
            "provenance_replay": metrics["provenance_replay"],
            "root_paths": root_paths(metrics, reference, run["candidates"]),
            "snapshot_history": history,
            "model_output_statistics": summarize_calls(_lines(directory / "trace.jsonl"),
                                                        _lines(directory / "calls.jsonl")),
            "stage_statistics": run.get("stage_statistics", {}),
            "variant_statistics": result.get("variant_statistics", {}),
            "input_hashes": {name: _digest(directory / name) for name in
                             ("run.json", "result.json", "metrics.json", "trace.jsonl",
                              "calls.jsonl", "snapshots.jsonl") if (directory / name).is_file()},
        })
    known_limits = all(isinstance(value, dict) and value for value in limits)
    return {
        "analysis_version": "root-guided-readonly-v1",
        "input_comparability_verified": comparison["input_comparability_verified"],
        "metrics_recomputed_and_verified": comparison["metrics_recomputed_and_verified"],
        "execution_limits_recorded_for_all_runs": bool(known_limits),
        "same_recorded_execution_limits": all(value == limits[0] for value in limits)
        if known_limits else None,
        "prepared_input_hashes": {name: _digest(prepared / name) for name in
                                  ("manifest.json", "ir.json", "schema.json", "summaries.json")
                                  if (prepared / name).is_file()},
        "reference_sha256": _digest(reference_path),
        "annotation_level": reference["annotation_level"],
        "rows": rows,
        "limitations": [*comparison["limitations"],
                        "Same soft deadline/task cap does not imply equal realized work; "
                        "task-boundary checks may overshoot the deadline.",
                        "Input tokens are saved tokenizer measurements; output size is "
                        "characters because output-token usage was not recorded.",
                        "Snapshot times are observed segment-local publications. Resumed "
                        "runs do not establish a first-ever path time."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    print(json.dumps(analyze_runs(args.prepared, args.runs, reference_path=args.reference),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
