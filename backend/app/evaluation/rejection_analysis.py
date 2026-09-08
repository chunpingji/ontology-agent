"""Read-only rejection/continuation audit, without references or accuracy scoring.

``python -m app.evaluation.rejection_analysis --run DIR`` writes JSON to stdout.
Finalized incomplete runs are allowed; an active marker is never allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "rejection-continuation-audit-v1"
SEMANTIC_CODES = {
    "unsupported_type", "reference_not_supported", "cooccurrence_only",
    "no_candidates", "model_refusal", "model_partial_refusal", "passed",
}
PROTOCOL_CODES = {
    "model_schema_error", "model_parse_error", "unknown_model_reference",
    "ambiguous_allowed_source_fragments", "citation_fact_permission_mismatch",
    "citation_fragment_source_mismatch", "non_atomic_source_citation",
    "unknown_or_disallowed_citation_source", "ambiguous_source_quote",
    "source_excerpt_mismatch", "source_quote_outside_scope", "scope_violation",
    "entity_type_decisions_mismatch",
}


def _location(row):
    return {
        "subject_id": row.get("subject_id"),
        "predicate_iri": row.get("predicate_iri") or (row.get("predicate") or {}).get("iri"),
        "record_id": row.get("record_id"),
        "retrieval_phase": row.get("retrieval_phase"),
        "relationship_path": row.get("relationship_path", row.get("path")),
    }


def _context(task_id, coverage):
    locations = []
    for row in coverage:
        if task_id and task_id in row.get("task_ids", []):
            value = _location(row)
            if value not in locations:
                locations.append(value)
    return {
        "status": "matched" if len(locations) == 1 else "ambiguous" if locations else "unmapped",
        "locations": locations,
    }


def _unique(context):
    return context["locations"][0] if context["status"] == "matched" else None


def _reason(value, field="reason"):
    reason = value.get(field)
    return {
        "reason": reason, "reason_field": field,
        "reason_status": "recorded" if isinstance(reason, str) and reason.strip() else "missing",
    }


def _response(value):
    if isinstance(value, dict):
        return value, "object"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}, "unparseable_string"
        if isinstance(parsed, dict):
            return parsed, "json_string"
    return {}, "non_object"


def _continuation(origin, progress, queue):
    location = _unique(origin["context"])
    if not location or not location.get("record_id"):
        return {"status": "unavailable_context", "continued_with_model_call": None,
                "later_record_attempted": None, "witness": None, "pending_records": []}

    def different(value):
        return bool(value and value.get("record_id")
                    and value["record_id"] != location["record_id"])

    def same_pair(value):
        return (bool(location.get("subject_id") and location.get("predicate_iri"))
                and value.get("subject_id") == location["subject_id"]
                and value.get("predicate_iri") == location["predicate_iri"])

    later = []
    for item in progress:
        target = _unique(item["context"])
        if not different(target):
            continue
        # Task order proves an attempt, not a model call. Trace order proves a
        # returned model response. Never infer chronology from queue/coverage.
        trace_later = (origin.get("trace_index") is not None
                       and item.get("trace_index") is not None
                       and item["trace_index"] > origin["trace_index"])
        attempt_later = (origin.get("task_attempt_index") is not None
                         and item.get("task_attempt_index") is not None
                         and item["task_attempt_index"] > origin["task_attempt_index"])
        comparable_traces = (origin.get("trace_index") is not None
                             and item.get("trace_index") is not None)
        is_later = trace_later if comparable_traces else attempt_later
        if is_later:
            later.append({**item, "same_subject_predicate": same_pair(target)})
    # Prefer the original slot; distinguish fallback continuation elsewhere.
    same = [item for item in later if item["same_subject_predicate"]]
    eligible = same or later
    model = next((item for item in eligible if item.get("trace_index") is not None), None)
    witness = model or next(iter(eligible), None)
    pending = [_location(row) for row in queue if different(_location(row))]
    same_pending = [row for row in pending if same_pair(row)]
    if witness:
        status = ("same_subject_predicate" if same else "other_subject_or_predicate")
        status += "_model_call_observed" if model else "_attempt_only_observed"
    else:
        status = "pending_only" if pending else "no_later_record_observed"
    return {
        "status": status,
        "continued_with_model_call": bool(model),
        "later_record_attempted": bool(witness),
        "same_subject_predicate_continued": bool(same),
        "witness": witness,
        "pending_same_subject_predicate_count": len(same_pending),
        "pending_other_subject_predicate_count": len(pending) - len(same_pending),
        "pending_records": same_pending or pending,
    }


def analyze_rejection_artifacts(run, checkpoint, traces=()):
    """Summarize recorded decisions only; inputs are not mutated.

    This in-memory helper has no activity check. Use ``analyze_rejection_run``
    for a formal file-based audit. Neither function judges rejection correctness.
    """
    coverage, queue = checkpoint.get("coverage", []), checkpoint.get("queue", [])
    tasks, traces = run.get("tasks", []), list(traces)
    attempt_indexes = defaultdict(list)
    for index, item in enumerate(tasks, 1):
        attempt_indexes[item.get("task", {}).get("task_id")].append(index)

    def metadata(task_id):
        indexes = attempt_indexes[task_id]
        return {"task_id": task_id, "context": _context(task_id, coverage),
                "task_attempt_index": indexes[0] if len(indexes) == 1 else None}

    progress = [{**metadata(item.get("task_id")), "trace_index": index,
                 "stage": item.get("stage"), "evidence_source": "trace.jsonl"}
                for index, item in enumerate(traces, 1) if item.get("stage") != "route_records"]
    progress.extend({**metadata(item.get("task", {}).get("task_id")),
                     "task_attempt_index": index, "evidence_source": "run.json.tasks",
                     "trace_index": None, "task_status": item.get("status")}
                    for index, item in enumerate(tasks, 1))
    events, identities, unreadable = [], [], []
    response_counts = Counter()
    for index, trace in enumerate(traces, 1):
        raw, parse_status = _response(trace.get("response"))
        stage = trace.get("stage")
        base = {**metadata(trace.get("task_id")), "trace_index": index, "stage": stage,
                "response_parse_status": parse_status, "evidence_source": "trace.jsonl.response",
                "reference_alias_scope": f"trace.jsonl:{index}"}
        response_counts[stage or "unknown"] += 1
        if parse_status in {"unparseable_string", "non_object"}:
            unreadable.append({**base, "raw_response": trace.get("response")})
            continue

        def emit(category, value, path, reason_field="reason", **extra):
            events.append({**base, "category": category, "response_path": path,
                           "decision": value, **_reason(value, reason_field), **extra})

        if raw.get("refusal"):
            emit("model_refusal", raw, "$", "refusal")
        elif stage == "verify_entity_types":
            for number, value in enumerate(raw.get("decisions", [])):
                if not isinstance(value, dict):
                    continue
                path = f"decisions[{number}]"
                identity_status = ("supported" if value.get("identity_supported") is True
                                   else "not_supported" if value.get("identity_supported") is False
                                   else "not_returned" if "identity_supported" not in value
                                   else "invalid_value")
                identities.append({**base, "response_path": path,
                                   "candidate_id": value.get("candidate_id"),
                                   "type_supported": value.get("supported"),
                                   "identity_status": identity_status})
                if value.get("supported") is False:
                    emit("type_rejection", value, path)
                elif value.get("supported") is True and identity_status == "not_supported":
                    emit("identity_not_supported", value, path,
                         reason_scope="type_decision_reason_not_identity_specific")
        elif stage == "verify_binding" and raw.get("supported") is False:
            # refusal_reason here explains failed binding, not overall model refusal.
            emit("binding_rejection", raw, "$", "refusal_reason")
        elif stage == "verify_reference" and raw.get("supported") is False:
            emit("reference_rejection", raw, "$")
        elif stage == "recall":
            if raw.get("refusal_reason"):
                partial = isinstance(raw.get("entities"), list) and bool(raw["entities"])
                emit("model_partial_refusal" if partial else "model_refusal",
                     raw, "$", "refusal_reason")
            else:
                for name in ("entities", "assertions"):
                    if raw.get(name) == []:
                        emit("no_candidates", raw, name)
            for number, value in enumerate(raw.get("entities", [])):
                if isinstance(value, dict) and value.get("supported") is False:
                    emit("type_rejection", value, f"entities[{number}]")

    task_failures, task_semantic = [], []
    for index, task in enumerate(tasks, 1):
        task_id = task.get("task", {}).get("task_id")
        codes = {str(code) for code in task.get("issues", [])}
        if task.get("reason"):
            codes.add(str(task["reason"]))
        codes.update(str(value["code"]) for value in task.get("outcomes", []) if value.get("code"))
        for code in sorted(codes):
            row = {**metadata(task_id), "task_attempt_index": index,
                   "evidence_source": "run.json.tasks", "code": code,
                   "task_status": task.get("status"),
                   "outcomes": [v for v in task.get("outcomes", []) if v.get("code") == code]}
            if code in SEMANTIC_CODES:
                # This projection may mirror a trace decision. Never add its
                # counts to response events; it also exposes missing traces.
                task_semantic.append(row)
                continue
            category = ("budget_failure" if "budget" in code
                        else "protocol_failure" if code in PROTOCOL_CODES
                        else "model_failure" if code.startswith("model_")
                        else "other_validation_failure")
            task_failures.append({**row, "category": category})
    for row in [*events, *task_failures, *task_semantic]:
        row["continuation"] = _continuation(row, progress, queue)
    return {
        "version": VERSION, "completion": run.get("completion"),
        "trace_count": len(traces), "task_attempt_count": len(tasks),
        "response_stage_counts": dict(sorted(response_counts.items())),
        "response_event_counts": dict(sorted(Counter(v["category"] for v in events).items())),
        "events": events, "identity_observations": identities,
        "unreadable_responses": unreadable,
        "task_failure_counts": dict(sorted(Counter(v["category"] for v in task_failures).items())),
        "task_failures": task_failures,
        "task_semantic_code_counts": dict(sorted(
            Counter(v["code"] for v in task_semantic).items(),
        )),
        "task_semantic_observations": task_semantic,
        "queued_work_count": len(queue),
        "interpretation": (
            "Response events count explicit returned decisions, not semantic accuracy. Missing "
            "reasons or identity fields are not inferred. Task-code projections may mirror "
            "response events and must not be added to them. Continuation requires a later "
            "different-record trace or recorded task attempt; queued work is only pending. "
            "Attempt-only continuation does not prove a model call or successful extraction. "
            "Identity rejection is not type rejection. c0/e0 and other response aliases are "
            "local to reference_alias_scope; resolve only with that trace row's references. "
            "No silver/reference data is used."
        ),
    }


def analyze_rejection_run(run_directory):
    """Read finalized artifacts and return their SHA-256 hashes with the audit."""
    directory = Path(run_directory).resolve()
    marker = directory / "run.in_progress.json"
    if marker.exists():
        raise ValueError("active run: rejection audit requires finalized artifacts")
    raw = {name: (directory / name).read_bytes()
           for name in ("run.json", "checkpoint.json", "trace.jsonl")}
    run = json.loads(raw["run.json"])
    if run.get("completion") not in {"complete", "incomplete"}:
        raise ValueError("run has no finalized completion status")
    checkpoint = json.loads(raw["checkpoint.json"])
    identity_checks = {}
    for key in ("input_id", "scheduler_version", "transport_version"):
        run_value, checkpoint_value = run.get(key), checkpoint.get(key)
        missing = run_value is None or checkpoint_value is None
        if not missing and run_value != checkpoint_value:
            raise ValueError(f"run and checkpoint {key} differ; cannot audit mixed artifacts")
        identity_checks[key] = {
            "status": "unavailable_missing_field" if missing else "matched",
            "run_value": run_value, "checkpoint_value": checkpoint_value,
        }
    report = analyze_rejection_artifacts(
        run, checkpoint,
        [json.loads(line) for line in raw["trace.jsonl"].splitlines() if line.strip()],
    )
    changed = any((directory / name).read_bytes() != data for name, data in raw.items())
    if marker.exists() or changed:
        raise ValueError("active or changed run: artifacts changed during rejection audit")
    report["artifact_sha256"] = {
        name: hashlib.sha256(data).hexdigest() for name, data in raw.items()
    }
    report["artifact_identity_checks"] = identity_checks
    report["artifact_identity_note"] = (
        "Present run/checkpoint identities and versions must match. Missing legacy fields "
        "are reported as unavailable, not silently treated as a verified match."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    try:
        report = analyze_rejection_run(args.run)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
