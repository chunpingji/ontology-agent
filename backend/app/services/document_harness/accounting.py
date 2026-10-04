"""Small, additive accounting for current harness work and paid model attempts."""

from copy import deepcopy
from datetime import UTC

CALL_FIELDS = (
    "stage", "status", "attempts", "started_at", "finished_at", "duration_us",
    "input_tokens", "output_tokens", "unknown_input", "unknown_output",
    "unmeasured_attempts", "error",
)
TOTAL_FIELDS = (
    "attempts", "completed_calls", "duration_us", "input_tokens", "output_tokens",
    "unknown_input", "unknown_output", "unmeasured_attempts",
)
CANDIDATE_DOMAINS = {"entities", "properties", "relations", "relation_groups"}
PROOF_DOMAINS = {"properties", "relations", "relation_groups"}
WORK_STATUSES = ("ready", "waiting", "pruned", "done", "failed")


def empty_metrics():
    return {
        "completed_calls": 0, "candidate_count": 0, "fact_count": 0,
        "limited_scope_count": 0, "stages": {},
        "work_counts": dict.fromkeys(WORK_STATUSES, 0),
        "rule_verified_count": 0, "llm_verified_count": 0,
        "phase_work_counts": {phase: dict.fromkeys(WORK_STATUSES, 0)
                              for phase in ("discovery", "skeleton", "semantic", "deterministic")},
        "calibration_counts": {key: dict.fromkeys(
            ("not_run", "passed", "invalid", "incomplete", "not_applicable", "error"), 0)
            for key in ("identifier", "datatype", "unit", "shacl")},
    }


def call_accounting(row):
    """Normalize persisted accounting values for content hashing across SQL dialects."""
    result = {}
    for name in CALL_FIELDS:
        value = getattr(row, "call_" + name)
        if name.endswith("_at") and value is not None:
            value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            value = value.isoformat()
        result[name] = value
    result["phase"] = row.payload["value"].get("execution_phase", "discovery")
    return result


def contribution(call):
    if not call:
        return dict.fromkeys(TOTAL_FIELDS, 0)
    return {name: (int(call["status"] == "completed") if name == "completed_calls"
                   else call[name]) for name in TOTAL_FIELDS}


def update_call_metrics(metrics, before, after):
    result = deepcopy(metrics)
    for call, sign in ((before, -1), (after, 1)):
        if not call:
            continue
        values = contribution(call)
        phase = call.get("phase", "discovery")
        totals = result["stages"].setdefault(phase + ":" + call["stage"],
                                             {**dict.fromkeys(TOTAL_FIELDS, 0),
                                              "phase": phase, "stage": call["stage"]})
        for name, value in values.items():
            totals[name] += sign * value
        result["completed_calls"] += sign * values["completed_calls"]
    return result


def business_contribution(domain, row):
    if row is None:
        return {}
    result = {}
    if domain in CANDIDATE_DOMAINS and row.get("role") != "document_root":
        result["candidate_count"] = 1
        result["fact_count"] = int(row.get("state") == "accepted")
        method = (row.get("verification") or {}).get("method")
        if domain in PROOF_DOMAINS and row.get("state") == "accepted" and method in {"rule", "llm"}:
            result[method + "_verified_count"] = 1
    if domain in CANDIDATE_DOMAINS:
        checks = (row.get("calibration") or {}).get("checks", {})
        for key in ("identifier", "datatype", "unit", "shacl"):
            result["check:" + key + ":" + checks.get(key, {}).get("status", "not_run")] = 1
    if domain == "work" and row.get("status") in WORK_STATUSES:
        result["work:" + row["status"]] = 1
        result["phase_work:" + row["phase"] + ":" + row["status"]] = 1
    if domain in {"observations", "reference_cues"}:
        result["limited_scope_count"] = int(bool(
            row.get("truncated") or row.get("candidate_scope_limited")
            or row.get("weak_pool_truncated") or row.get("reference_targets_truncated")
        ))
    return result


def update_business_metrics(metrics, domain, before, after):
    result = deepcopy(metrics)
    for row, sign in ((before, -1), (after, 1)):
        for name, value in business_contribution(domain, row).items():
            if name.startswith("work:"):
                result["work_counts"][name[5:]] += sign * value
            elif name.startswith("phase_work:"):
                _, phase, status = name.split(":")
                result["phase_work_counts"][phase][status] += sign * value
            elif name.startswith("check:"):
                _, key, status = name.split(":")
                result["calibration_counts"][key][status] += sign * value
            else:
                result[name] += sign * value
    return result


def stage_costs(metrics):
    return [{
        "phase": value["phase"], "stage": value["stage"], "calls": value["attempts"],
        "seconds": value["duration_us"] / 1_000_000,
        "unmeasured_attempts": value["unmeasured_attempts"],
        "input_tokens": None if value["unknown_input"] else value["input_tokens"],
        "output_tokens": None if value["unknown_output"] else value["output_tokens"],
    } for stage, value in sorted(metrics["stages"].items()) if value["attempts"] > 0]


def measured_tokens(usage, primary, alternate):
    value = (usage or {}).get(primary, (usage or {}).get(alternate))
    return value if type(value) is int and value >= 0 else None
