"""Read saved experiment artifacts only; never imports application or calls models.
Usage: python summarize-metric-baseline.py RUN_ROOT ARM
"""
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from urllib.parse import quote
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ARM = sys.argv[2]
NUMERIC = {"http://www.w3.org/2001/XMLSchema#" + name for name in (
    "decimal", "double", "float", "integer", "int", "nonNegativeInteger", "positiveInteger",
)}
STAGES = {"candidates", "verification"}

def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def strict_shacl_pass(metric, candidate_id=None):
    shacl = metric.get("shacl", {})
    coverage = shacl.get("coverage", {})
    expected = set(coverage.get("expected_focus_nodes", []))
    actual = set(coverage.get("actual_focus_nodes", []))
    cid = candidate_id or metric.get("candidate_id")
    return (isinstance(cid, str) and bool(cid)
            and metric.get("candidate_id") == cid
            and metric.get("tool") == "validate_metric"
            and expected == {"urn:ontology-agent:claim:" + quote(cid, safe="")}
            and shacl.get("execution_status") == "completed"
            and shacl.get("evaluated") is True
            and shacl.get("conforms") is True
            and shacl.get("validation_status") == "passed"
            and coverage.get("complete") is True
            and bool(expected) and actual == expected
            and bool(coverage.get("executed_shapes"))
            and not coverage.get("missing_focus_nodes")
            and not coverage.get("unexpected_focus_nodes"))


def finite_decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def call_summary(calls):
    reported = [c for c in calls if isinstance(c.get("usage"), dict) and c["usage"]]
    token_totals, token_coverage = {}, {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [c.get("usage", {}).get(key) for c in calls if isinstance(c.get("usage"), dict)]
        valid = [n for n in values if type(n) is int and n >= 0]
        token_totals["reported_"+key] = sum(valid)
        token_coverage[key] = {"reported_calls": len(valid), "missing_or_invalid_calls": len(calls)-len(valid)}
    seconds = [finite_decimal(c.get("seconds")) for c in calls]
    valid_seconds = [n for n in seconds if n is not None and n >= 0]
    return {
        "http_calls": len(calls),
        "usage_reported_calls": len(reported), "usage_missing_calls": len(calls)-len(reported),
        **token_totals, "token_field_coverage": token_coverage,
        "http_seconds": str(sum(valid_seconds, Decimal(0))),
        "http_seconds_reported_calls": len(valid_seconds),
        "http_seconds_missing_or_invalid_calls": len(calls)-len(valid_seconds),
        "finish_reasons": dict(Counter(c.get("finish_reason") or "unreported" for c in calls)),
    }


def conversion_audit(entry):
    metric = entry["metric"]
    record = metric.get("conversion_record")
    if not isinstance(record, dict) or not record:
        return "no_unit_mapping_record"
    kind = record.get("mapping_kind")
    if kind in {"identity", "alias"}:
        return kind
    if kind != "conversion":
        return "mapping_kind_missing_or_unknown"
    if (not strict_shacl_pass(metric, entry["candidate_id"])
            or metric.get("validation_status") != "passed"
            or metric.get("semantic_status") != "supported"):
        return "conversion_not_validated"
    quantity = metric.get("quantity") or {}
    if quantity.get("kind") != "number":
        return "conversion_not_scalar"
    if not record.get("from") or not record.get("to") or record["from"] == record["to"]:
        return "conversion_unit_pair_invalid"
    source, normalized, factor, offset = [finite_decimal(v) for v in (
        quantity.get("source_value"), metric.get("normalized_value"), record.get("factor"), record.get("offset"))]
    if any(v is None for v in (source, normalized, factor, offset)):
        return "conversion_operand_missing_or_invalid"
    if factor == 1 and offset == 0:
        return "unit_mapping_without_scale_or_offset"
    if Fraction(source)*Fraction(factor)+Fraction(offset) != Fraction(normalized):
        return "conversion_equation_mismatch"
    return "verified_scale_or_offset"


def metric_summary(entries):
    metrics = [e["metric"] for e in entries]
    numeric = [e for e in entries if e["numeric_slot"] is True]
    numeric_pass = [e for e in numeric if strict_shacl_pass(e["metric"], e["candidate_id"])]
    covered = [e for e in entries if strict_shacl_pass(e["metric"], e["candidate_id"])]
    conversions = [e for e in numeric_pass if e["metric"].get("validation_status") == "passed"
                   and e["metric"].get("normalized_value") is not None
                   and isinstance(e["metric"].get("conversion_record"), dict)
                   and e["metric"]["conversion_record"]]
    return {
        "saved_metric_invocation_records": len(metrics),
        "metric_execution_status": dict(Counter(m.get("execution_status", "missing") for m in metrics)),
        "metric_validation_status": dict(Counter(m.get("validation_status", "missing") for m in metrics)),
        "numeric_slot_candidates": len(numeric),
        "slot_datatype_missing_candidates": sum(e["numeric_slot"] is None for e in entries),
        "quantity_parsed_candidates": sum(m.get("quantity") is not None for m in metrics),
        "numeric_quantity_parse_missing": sum(e["metric"].get("quantity") is None for e in numeric),
        "shacl_attempt_records": sum(m.get("shacl", {}).get("execution_status") in {"completed", "failed"} for m in metrics),
        "shacl_evaluated_records": sum(m.get("shacl", {}).get("evaluated") is True for m in metrics),
        "strict_shacl_covered_conformant_claims": len(covered),
        "strict_shacl_actual_focus_visits": sum(len(e["metric"]["shacl"]["coverage"]["actual_focus_nodes"]) for e in covered),
        "numeric_shacl_attempts": sum(e["metric"].get("shacl", {}).get("execution_status") in {"completed", "failed"} for e in numeric),
        "numeric_strict_shacl_covered_conformant_claims": len(numeric_pass),
        "numeric_normalized_claims": sum(e["metric"].get("validation_status") == "passed" and e["metric"].get("normalized_value") is not None for e in numeric_pass),
        "numeric_strict_shacl_denominator": len(numeric),
        "numeric_strict_shacl_pass_fraction": len(numeric_pass)/len(numeric) if numeric else None,
        "numeric_accepted_by_program": sum(e["accepted_by_program"] for e in numeric),
        "numeric_scale_or_offset_conversions": sum(conversion_audit(e) == "verified_scale_or_offset" for e in conversions),
        "numeric_conversion_audit": dict(Counter(conversion_audit(e) for e in numeric)),
        "numeric_unit_mappings": dict(Counter(e["metric"]["conversion_record"].get("mapping_kind", "unclassified") for e in conversions)),
        "unit_checks_status": dict(Counter(m.get("unit_checks", {}).get("status", "missing") for m in metrics)),
        "blocking_issues": dict(Counter(i for m in metrics if not strict_shacl_pass(m) for i in m.get("issues", []))),
    }

rows, all_calls, prechecks, final_checks, frozen_properties, unknown_calls = [], [], [], [], [], []
cases = read(ROOT/"cases.json", [])
declared_scopes = {case["scope_id"] for case in cases}
observed_scopes = {p.parent.name for p in (ROOT/ARM).glob("*/result.json")}
manifest = read(ROOT/"result.json", {})
for scope in sorted(declared_scopes | observed_scopes):
    path = ROOT/ARM/scope/"result.json"
    result = read(path, {"status": "not_recorded", "calls": []})
    frozen = read(path.parent/"frozen-candidates.json", {})
    menu = read(path.parent/"subject-cards.json", {})
    claims = {c["id"]: c for c in frozen.get("claims", [])}
    accepted_ids = {fact.get("candidate_id") for fact in result.get("accepted", [])}
    scope_prechecks, scope_final, scope_properties = [], [], []
    def entry(cid, metric, stage):
        claim = claims.get(cid, {})
        slot = menu.get(claim.get("subject_id"), {}).get("fields", {}).get(claim.get("field"), {})
        datatypes = slot.get("datatype_iris")
        return {"arm": ARM, "scope_id": scope, "candidate_id": cid, "metric_stage": stage,
                "subject_id": claim.get("subject_id"), "field": claim.get("field"),
                "datatype_iris": datatypes, "canonical_unit": slot.get("canonical_unit"),
                "numeric_slot": bool(set(datatypes)&NUMERIC) if datatypes else None,
                "accepted_by_program": cid in accepted_ids, "metric": metric}
    for cid, claim in claims.items():
        if claim.get("kind") == "property":
            scope_properties.append(entry(cid, {}, "frozen_candidate_not_invocation"))
        if isinstance(claim.get("metric_precheck"), dict):
            scope_prechecks.append(entry(cid, claim["metric_precheck"], "precheck"))
    for check in result.get("metric_checks", []):
        scope_final.append(entry(check["candidate_id"], check["metric"], "final"))
    calls = [c for c in result.get("calls", []) if c.get("stage") in STAGES]
    physical_stage_calls = [c for stage in sorted(STAGES) for c in read(path.parent/stage/"calls.json", [])]
    other_calls = [c for c in result.get("calls", []) if c.get("stage") not in STAGES | {"plan"}]
    unknown_calls.extend({"scope_id": scope, **c} for c in other_calls)
    all_calls.extend(calls)
    frozen_properties.extend(scope_properties)
    prechecks.extend(scope_prechecks)
    final_checks.extend(scope_final)
    rows.append({"scope_id": scope, "status": result["status"], "error_code": result.get("error_code"),
                 "error_type": result.get("error_type"), "failure_stage": result.get("failure_stage"),
                 "result_artifact_present": path.exists(),
                 "excluded_plan_calls": sum(c.get("stage") == "plan" for c in result.get("calls", [])),
                 "unknown_stage_calls": other_calls,
                 "calls": call_summary(calls),
                 "call_copies_match": sorted(calls,key=lambda c:c['stage']) == sorted(physical_stage_calls,key=lambda c:c['stage']),
                 "precheck": metric_summary(scope_prechecks), "final": metric_summary(scope_final),
                 "frozen_property_candidates": len(scope_properties),
                 "frozen_numeric_candidates": sum(e["numeric_slot"] is True for e in scope_properties),
                 "frozen_artifact_present": (path.parent/"frozen-candidates.json").exists(),
                 "final_metric_artifact_present": "metric_checks" in result,
                 "accepted_property_candidates": sum(f.get("kind")=="property" for f in result.get("accepted",[]))})

numeric_details = [{**{k:v for k,v in e.items() if k!='metric'},
                    **{k:e['metric'].get(k) for k in ('raw_value','source_unit','quantity','normalized_value','conversion_record','execution_status','validation_status','semantic_status','issues')},
                    'strict_shacl_pass': strict_shacl_pass(e['metric'], e['candidate_id']),
                    'conversion_audit': conversion_audit(e)} for e in final_checks if e['numeric_slot'] is True]
def key(entry):
    return entry["scope_id"], entry["candidate_id"]

frozen_numeric = {key(e) for e in frozen_properties if e["numeric_slot"] is True}
final_numeric = {key(e): e for e in final_checks if e["numeric_slot"] is True}
covered_numeric = {k for k, e in final_numeric.items() if strict_shacl_pass(e["metric"], e["candidate_id"])}
missing_final_numeric = sorted(frozen_numeric - final_numeric.keys())
out = {
    "run_root": str(ROOT), "arm": ARM,
    "manifest_status": manifest.get("status"),
    "declared_scope_count": len(declared_scopes),
    "scope_statuses": dict(Counter(r["status"] for r in rows)),
    "undeclared_observed_scopes": sorted(observed_scopes-declared_scopes) if declared_scopes else [],
    "scope_count":len(rows),"complete_scope_count":sum(r['status']=='complete' for r in rows),
    "cost_scope":"candidates and verification only; historical plan calls excluded; missing usage is unknown, not zero",
    "http":call_summary(all_calls),
    "unknown_stage_calls_not_included_in_cost": unknown_calls,
    "excluded_plan_calls": sum(r["excluded_plan_calls"] for r in rows),
    "http_by_stage":{stage:call_summary([c for c in all_calls if c['stage']==stage]) for stage in sorted(STAGES)},
    "metric_count_scope":"Saved precheck and final invocation results in this selected artifact snapshot. Not cumulative historical rerun invocations; an exception before writing may leave unrecorded calls.",
    "precheck":metric_summary(prechecks), "final":metric_summary(final_checks),
    "combined_saved_metric_invocation_records":len(prechecks)+len(final_checks),
    "unique_frozen_property_candidates":sum(r['frozen_property_candidates'] for r in rows),
    "numeric_coverage_over_all_frozen_candidates": {
        "frozen_numeric_candidates": len(frozen_numeric),
        "final_numeric_records": len(final_numeric),
        "missing_final_candidate_ids": [{"scope_id": s, "candidate_id": c} for s,c in missing_final_numeric],
        "strict_shacl_covered_conformant_candidates": len(covered_numeric & frozen_numeric),
        "strict_shacl_pass_fraction": len(covered_numeric & frozen_numeric)/len(frozen_numeric) if frozen_numeric else None,
        "note": "Includes frozen numeric candidates without final results; not full-document recall or numeric calibration accuracy.",
    },
    "scopes":rows,"numeric_final_details":numeric_details,
    "caveats":[
        "Zero numeric SHACL coverage is unvalidated, not numerical calibration success.",
        "quantity != null counts parsed quantities, not all numeric slot attempts; use card datatype denominator.",
        "Empty conversion_record for a text/date and identity/alias mappings are not numerical scale conversions.",
        "unit_checks=passed alone does not prove source/owner/semantic validation or SHACL execution.",
        "fact_eligible is deliberately false even on validate_metric success; use final accepted candidate ID separately.",
        "claim URNs repeat across scopes; count scope-qualified candidates or focus visits, not global bare-URN uniqueness.",
        "SHACL profile validates one literal representation, not clinical truth, unit semantics or full business graph.",
        "Summed HTTP seconds are stage request durations; not total elapsed experiment wall time.",
        "Declared scopes without result files remain not_recorded; candidates never generated by a failed scope cannot enter a candidate denominator.",
        "Per-stage numeric fractions describe that stage only; use numeric_coverage_over_all_frozen_candidates for overall coverage.",
        "Real scale/offset conversion requires an explicit complete record and exact source*factor+offset=normalized check; identity/alias do not count.",
    ],
}
print(json.dumps(out,ensure_ascii=False,indent=2))
