from copy import deepcopy

from app.services.reasoning import pde_calculation, rule_service
from app.services.reasoning.pde_calculation import apply_decisions
from app.services.reporting.calculation_execution import execute_checks
from app.services.reporting.template_v2 import TemplateV2
from tests.test_reasoning.test_pde_calculation_v1 import candidates
from tests.test_reporting.test_calculation_pipeline import decision, source_case


def test_dependency_cache_recalculates_only_affected_entities_and_retains_history(monkeypatch):
    rule_service._results.clear()
    calls = []
    original = pde_calculation._evaluate_entity

    def count(entity, props, definition):
        calls.append(entity.candidate_id)
        return original(entity, props, definition)

    monkeypatch.setattr(pde_calculation, "_evaluate_entity", count)
    rows = candidates() + candidates(suffix="-other")
    first = rule_service.evaluate(rows)
    assert calls == ["study", "study-other"]
    frozen_decision = decision(rows)
    assert rule_service.evaluate(rows) == first
    assert len(calls) == 2
    modified = deepcopy(rows)
    modified[3].review_status = "rejected"
    changed = rule_service.evaluate(modified)
    assert calls == ["study", "study-other", "study"]
    assert first[0]["calculation_id"] != changed[0]["calculation_id"]
    assert first[1] == changed[1]
    assert apply_decisions(first, [frozen_decision])[0]["decision"]
    assert apply_decisions(changed, [frozen_decision])[0]["decision"] is None
    # A relationship rejection is checked again even when numerical work is reused.
    modified = deepcopy(rows)
    modified[2].review_status = "rejected"
    unlinked = rule_service.evaluate(modified)
    assert not unlinked[0]["linked_to_source"]
    assert len(calls) == 3
    assert rule_service.evaluate(rows) == first


def test_report_and_graph_share_cached_operator_and_method_identity(monkeypatch):
    rule_service._results.clear()
    rows = candidates()
    result = rule_service.evaluate(rows)[0]
    plan, bundle = source_case(rows)

    def unexpected(*args):
        raise AssertionError("unchanged graph should reuse the shared operator result")

    monkeypatch.setattr(pde_calculation, "_evaluate_entity", unexpected)
    checks = execute_checks(TemplateV2.model_validate(plan["template"]), bundle)
    frozen = checks["pde:doc"]["rows"][0]
    assert frozen["calculation_id"] == result["calculation_id"]
    assert frozen["method_hash"] == result["method_hash"]


def test_type_change_rebuilds_automatic_plan_and_retains_explicit_requirements():
    old = [{"source_slot_id": "doc", "class_iri": pde_calculation.DEV + "CMCReport"}]
    automatic = rule_service.required_checks(old)
    custom = {**automatic[0], "check_id": "explicit-report-requirement"}
    other = [{"source_slot_id": "doc", "class_iri": "urn:OtherReport"}]
    assert rule_service.check_plan(other, automatic) == []
    assert rule_service.check_plan(other, [*automatic, custom]) == [custom]
    assert rule_service.check_plan(old, []) == automatic
