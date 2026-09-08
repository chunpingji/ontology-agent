"""Stable stage/category counters alongside legacy string diagnostics."""

from collections import Counter, defaultdict

MODEL_FAILURES = frozenset(
    {
        "model_unavailable",
        "model_timeout",
        "model_total_timeout",
        "model_cancelled",
        "model_output_truncated",
        "model_parse_error",
        "model_request_failed",
        "model_empty_response",
        "model_refusal",
        "model_schema_error",
    }
)


def diagnostic(stage, code):
    if code in MODEL_FAILURES:
        category = "model_interrupted"
    elif "budget" in code or code == "task_budget_or_pause":
        category = "budget_interrupted"
    elif code in {
        "ambiguous_source_quote",
        "source_excerpt_mismatch",
        "source_quote_outside_scope",
        "scope_violation",
    }:
        category = "quote_failure"
    elif code in {"cooccurrence_only", "unsupported_type", "reference_not_supported",
                  "model_partial_refusal"}:
        category = "semantic_not_supported"
    elif code == "no_candidates":
        category = "not_found"
    elif code == "passed":
        category = "passed"
    else:
        category = "validation_rejected"
    return {"stage": stage, "code": code, "category": category}


def stage_statistics(tasks):
    counts = defaultdict(Counter)
    for event in tasks:
        for outcome in event.get("outcomes", []):
            counts[outcome["stage"]][outcome["category"]] += 1
    return {stage: dict(counts[stage]) for stage in sorted(counts)}
