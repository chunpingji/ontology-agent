"""Shared catalog and dependency-scoped execution of deterministic graph rules.

The catalog selects a version-fixed operator. Definitions, inputs and review
decisions retain their own identities; cached numerical work never caches a
user's latest decision or source relationship state.
"""

from collections import OrderedDict
from copy import deepcopy
from threading import RLock

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.performance import measure

_results = OrderedDict()
_lock = RLock()


def rule_catalog():
    from app.services.reasoning.pde_calculation import calculation_contract

    return [calculation_contract()]


def method(ref):
    return next((rule for rule in rule_catalog() if rule["contract_id"] == ref), None)


def required_checks(slots):
    return [
        {
            "check_id": "pde:" + slot["source_slot_id"],
            "source_slot": slot["source_slot_id"],
            "contract_ref": rule["contract_id"],
        }
        for slot in slots
        for rule in rule_catalog()
        if slot.get("class_iri") == rule["definition"]["root_class_iri"]
    ]


def check_plan(slots, explicit=()):
    """Retain explicit historical IDs/methods and add mandatory applicable rules."""
    classes = {s["source_slot_id"]: s.get("class_iri") for s in slots}
    checks = []
    for check in deepcopy(list(explicit)):
        rule = method(check["contract_ref"])
        if (
            rule
            and check["check_id"] == "pde:" + check["source_slot"]
            and classes.get(check["source_slot"]) != rule["definition"]["root_class_iri"]
        ):
            continue
        checks.append(check)
    for required in required_checks(slots):
        if not any(
            (c["source_slot"], c["contract_ref"])
            == (required["source_slot"], required["contract_ref"])
            for c in checks
        ):
            checks.append(required)
    return checks


def evaluate(candidates, *, contract=None, root_entity_id=None):
    from app.services.reasoning.pde_calculation import _evaluate_candidates
    from app.services.reporting.template_v2 import ReportingError

    selected = contract or rule_catalog()[0]
    registered = method(selected.get("contract_id"))
    if not registered or selected != registered:
        raise ReportingError("CALCULATION_METHOD_MISMATCH")
    operators = {"pde-check-v1": _evaluate_candidates}
    operator = operators.get(registered["definition"]["executor"])
    if operator is None:
        raise ReportingError("CALCULATION_EXECUTOR_UNAVAILABLE")
    return operator(candidates, contract=registered, root_entity_id=root_entity_id)


def entity_result(entity, properties, definition, operator):
    """Recompute only changed parameter subjects, including eligibility/provenance."""
    predicates = {p["predicate_iri"] for p in definition["parameters"].values()}
    dependencies = sorted(
        (p for p in properties if p.predicate_iri in predicates), key=lambda p: p.candidate_id
    )
    key = evidence_hash([definition, entity, dependencies])
    with _lock:
        if key in _results:
            with measure("graph_rule_cache_hit"):
                _results.move_to_end(key)
                return deepcopy(_results[key])
        with measure("graph_rule_cache_miss"):
            result = operator(entity, dependencies, definition)
        _results[key] = deepcopy(result)
        if len(_results) > 1024:
            _results.popitem(last=False)
        return result


def refresh_job_rules(db, job_id, changed=()):
    """Warm changed dependencies in the graph mutation transaction."""
    from app.services.extraction.candidate_store import CandidateStore

    rules = rule_catalog()
    predicates = {p["predicate_iri"] for r in rules for p in r["definition"]["parameters"].values()}
    predicates.update(p for r in rules for p in r["definition"]["predicate_path"])
    classes = {
        r["definition"][key] for r in rules for key in ("root_class_iri", "subject_class_iri")
    }
    if changed and not any(
        c.class_iri in classes or c.predicate_iri in predicates for c in changed
    ):
        return []
    return evaluate(CandidateStore(db).list(job_id))
