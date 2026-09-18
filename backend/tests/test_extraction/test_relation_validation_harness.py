"""Required tools and the independent server gate over real Word evidence."""

import copy
import json
from dataclasses import replace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided import tool_runtime
from app.services.extraction.ontology_guided.claim_identity import duplicate_mentions
from app.services.extraction.ontology_guided.claim_protocol import (
    EntityDependencyView,
    EntityProposal,
)
from app.services.extraction.ontology_guided.contracts import VersionedRef
from app.services.extraction.ontology_guided.tool_contracts import RELATION_PROFILE, ToolCall
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


def registered_b(source):
    proposal = EntityProposal.model_validate(source["proposal"]["entities"][0])
    quote = proposal.mentions[0]
    unit = source["index"].ir.unit(quote.evidence_id)
    start = unit.text.index(quote.text)
    values = dict(
        entity_ref=VersionedRef(id="registered-b", revision=1), class_iri=proposal.class_iri,
        grounding_kind="mention", root_origin=None, proposal=proposal,
        source_refs=[source["index"].ir.anchor(quote.evidence_id, start, start + len(quote.text))],
        dependency_refs=[],
    )
    return EntityDependencyView(**values, content_hash=evidence_hash(values))


def test_model_cannot_skip_relation_tool_by_repeating_supported(source, monkeypatch):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, skip_relation_check=True,
    )
    with pytest.raises(RuntimeError, match="required_relation_validation_missing"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 3
    assert all(request["tool_choice"] == "required" for request in requests[1:])
    assert stored["protocol"]["tool_calls_used"] == 0
    assert not stored["protocol"]["verification_ref"]
    assert not stored["protocol"]["outcome_ref"]


def test_insufficient_budget_never_skips_required_check(source, monkeypatch):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, budget=2,
    )
    with pytest.raises(RuntimeError, match="required_relation_validation_missing"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == len(stored["reservations"]) == 1
    assert not stored["protocol"]["outcome_ref"]


@pytest.mark.parametrize("budget", [3, 4])
def test_required_checks_respect_batch_limit_and_reserve_semantic_answer(
    source, monkeypatch, budget,
):
    relation = source["proposal"]["relations"][0]
    source["proposal"]["relations"] = [dict(
        relation, local_id=f"r-{identity}", object_ids=[identity], selection="all",
        selection_support=[],
    ) for identity in ("b", "c")]
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, budget=budget,
    )
    adapter.tool_limits = replace(adapter.tool_limits, max_calls_per_response=1)
    if budget == 3:
        with pytest.raises(RuntimeError, match="required_relation_validation_missing"):
            adapter.inspect(task, context, predicate, menu)
        assert len(requests) == 1 and not stored["protocol"]["outcome_ref"]
    else:
        outcome = adapter.inspect(task, context, predicate, menu)
        assert outcome.complete and len(outcome.edges) == 2
        assert len(requests) == 4 and stored["protocol"]["tool_calls_used"] == 2
        for request in requests[1:3]:
            required = json.loads(request["instructions"].splitlines()[-1])
            assert len(required["required_relation_checks"]) == 1
        for previous, current in zip(requests[1:3], requests[2:]):
            inputs = previous["input_items"]
            assert current["input_items"][:len(inputs)] == inputs


def test_validator_technical_failure_is_not_semantic_rejection(source, monkeypatch):
    def unavailable(*_args):
        raise RuntimeError("isolated validator failure")

    monkeypatch.setattr(tool_runtime, "_validate_relation", unavailable)
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    with pytest.raises(RuntimeError, match="required_relation_validation_missing"):
        adapter.inspect(task, context, predicate, menu)
    results = [r["value"]["result"] for r in stored["results"].values()
               if r["field"] == "tool_result"]
    assert len(results) == 2
    assert all(r["status"] == "error" and r["data"] is None for r in results)
    assert all(r["issues"][0]["code"] == "tool_execution_failed" for r in results)
    assert len(requests) == 3 and not stored["protocol"]["outcome_ref"]


@pytest.mark.parametrize("mutation", [
    "missing", "content_hash", "context_hash", "ontology_snapshot_id", "menu_hash", "claim_ref",
])
def test_final_gate_rejects_missing_or_stale_result_even_after_model_support(
    source, monkeypatch, mutation,
):
    adapter, task, context, predicate, menu, _, _ = setup_adapter(source, monkeypatch)
    check = adapter._final_checks

    def altered(ctx, verification):
        checks = dict(ctx.relation_checks)
        assert len(checks) == 1
        identity, result = next(iter(checks.items()))
        if mutation == "missing":
            checks.clear()
        else:
            value = VersionedRef(id="other", revision=1) if mutation == "claim_ref" else "stale"
            checks[identity] = result.model_copy(update={mutation: value})
        return check(replace(ctx, relation_checks=checks), verification)

    monkeypatch.setattr(adapter, "_final_checks", altered)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.edges and not outcome.relationship_groups and not outcome.complete
    assert "required_relation_validation_missing" in outcome.reason


def test_registered_mention_cannot_be_recreated_despite_supported_answer(source, monkeypatch):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    existing = registered_b(source)
    context.tool_inputs["entity_dependencies"].append(existing.model_dump(mode="json"))
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.edges and not outcome.relationship_groups and not outcome.complete
    assert len(outcome.nodes) == 1 and outcome.nodes[0].label == "对象丙"
    frozen = next(r["value"] for r in stored["results"].values() if r["field"] == "discovery")
    assert frozen["claim_issues"]["b"] == ["entity_referent_already_registered"]
    assert frozen["claim_issues"]["r"] == ["entity_dependency_invalid"]
    # Invalid claims are removed from verification targets before any model check.
    assert not any(r["field"] == "tool_result" for r in stored["results"].values())
    assert len(requests) == 2 and not stored["protocol"]["recovery_used"]
    response = next(r["value"] for r in stored["results"].values()
                    if r["field"] == "model_turn" and r["value"]["attempt"] == 2)
    payload = json.loads(response["output_items"][0]["content"][0]["text"])
    assert all(f["verdict"] == "supported" for t in payload["verifications"] for f in t["facets"])


def test_model_supported_cannot_override_failed_relation_tool(source, monkeypatch):
    validate = tool_runtime._validate_relation
    existing = registered_b(source)

    def duplicate_in_tool(args, ctx, target):
        ctx = replace(ctx, entity_dependencies={
            **ctx.entity_dependencies, existing.entity_ref.id: existing,
        })
        return validate(args, ctx, target)

    monkeypatch.setattr(tool_runtime, "_validate_relation", duplicate_in_tool)
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.complete and not outcome.edges and not outcome.relationship_groups
    assert "entity_referent_already_registered" in outcome.reason
    assert len(requests) == 3 and stored["protocol"]["tool_calls_used"] == 1
    result = next(item for item in requests[-1]["input_items"]
                  if item.get("type") == "function_call_output")
    assert json.loads(result["output"])["data"]["identity_status"] == "failed"
    response = next(r["value"] for r in stored["results"].values()
                    if r["field"] == "model_turn" and r["value"]["attempt"] == 3)
    payload = json.loads(response["output_items"][0]["content"][0]["text"])
    assert all(f["verdict"] == "supported" for t in payload["verifications"] for f in t["facets"])


def test_same_name_in_different_source_cells_does_not_merge(source):
    units = [u for u in source["index"].ir.evidence_units if u.text == "同名记录"]
    assert len(units) == 2
    entities = [EntityProposal(
        local_id=str(i), class_iri="urn:T", representation="mention",
        mentions=[dict(evidence_id=u.evidence_id, text=u.text, context_text=None)],
        record_components=[], identifier_claims=[],
    ) for i, u in enumerate(units)]
    refs = {e.local_id: VersionedRef(id=e.local_id, revision=1) for e in entities}

    def resolve(quote):
        return source["index"].ir.anchor(quote.evidence_id, 0, len(quote.text))

    assert not duplicate_mentions(entities, [], refs, resolve)
    same = entities[0].model_copy(update={"local_id": "duplicate"})
    refs["duplicate"] = VersionedRef(id="duplicate", revision=1)
    assert duplicate_mentions([*entities, same], [], refs, resolve) == {"duplicate": refs["0"]}


@pytest.mark.parametrize("failure", ["duplicate", "range", "profile"])
def test_relation_tool_itself_checks_identity_range_and_profile(source, monkeypatch, failure):
    adapter, task, context, predicate, menu, _, _ = setup_adapter(source, monkeypatch)
    final_checks, captured = adapter._final_checks, []

    def capture(ctx, verification):
        captured.append(ctx)
        return final_checks(ctx, verification)

    monkeypatch.setattr(adapter, "_final_checks", capture)
    assert adapter.inspect(task, context, predicate, menu).complete
    ctx = replace(captured[0], stage="verification")
    target = next(t for t in ctx.frozen_claims.values() if t.target_kind == "relation")
    if failure == "duplicate":
        dep = registered_b(source)
        ctx = replace(ctx, entity_dependencies={**ctx.entity_dependencies, dep.entity_ref.id: dep})
    elif failure == "range":
        bad_menu = ctx.menu.model_copy(deep=True)
        bad_menu.relationships[0].range_class_iris = ["urn:Other"]
        ctx = replace(ctx, menu=bad_menu)
    call = ToolCall(call_id="check", name="validate_graph", arguments_json=json.dumps({
        "claim_id": target.claim_ref.id,
        "shape_profile_id": "model-invented-profile" if failure == "profile" else RELATION_PROFILE,
    }))
    result = tool_runtime.dispatch_tool(call, ctx)
    if failure == "profile":
        assert result.status == "blocked" and result.data is None
        assert result.issues[0].code == "relation_profile_mismatch"
    else:
        assert result.data.validation_status == "failed"
        if failure == "duplicate":
            assert result.data.ontology_status == "passed"
            assert result.data.identity_status == "failed"
            assert result.data.duplicate_entities == {"b": dep.entity_ref}
        else:
            assert result.data.ontology_status == "failed"
            assert any(i.code == "range_mismatch" for i in result.data.issues)


def test_new_reference_policy_reports_physical_reuse_without_identity_rejection(
    source, monkeypatch,
):
    adapter, task, context, predicate, menu, _, _ = setup_adapter(source, monkeypatch)
    final_checks, captured = adapter._final_checks, []

    def capture(ctx, verification):
        captured.append(ctx)
        return final_checks(ctx, verification)

    monkeypatch.setattr(adapter, "_final_checks", capture)
    assert adapter.inspect(task, context, predicate, menu).complete
    dependency = registered_b(source)
    ctx = replace(
        captured[0], stage="verification", reference_resolution=True,
        entity_dependencies={
            **captured[0].entity_dependencies, dependency.entity_ref.id: dependency,
        },
    )
    target = next(t for t in ctx.frozen_claims.values() if t.target_kind == "relation")
    result = tool_runtime.dispatch_tool(ToolCall(
        call_id="physical-reuse", name="validate_graph", arguments_json=json.dumps({
            "claim_id": target.claim_ref.id, "shape_profile_id": RELATION_PROFILE,
        }),
    ), ctx)
    assert result.status == "ok"
    assert result.data.validation_status == result.data.identity_status == "passed"
    assert result.data.duplicate_entities == {"b": dependency.entity_ref}
    assert not any(issue.code == "entity_referent_already_registered"
                   for issue in result.data.issues)


def test_resume_consumes_saved_validation_without_rerunning_tool(source, monkeypatch):
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(
        source, monkeypatch, stop_at="tool_result",
    )
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 2
    saved = copy.deepcopy(stored)
    adapter, task, context, predicate, menu, stored, requests = setup_adapter(source, monkeypatch)
    stored.update(saved)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    context.remaining_model_calls = 2

    def repeated(*_args):
        pytest.fail("confirmed relation validation was executed twice")

    monkeypatch.setattr(tool_runtime, "_validate_relation", repeated)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    assert len(requests) == 1 and stored["protocol"]["tool_calls_used"] == 1
    results = [item for item in requests[0]["input_items"]
               if item.get("type") == "function_call_output"]
    assert len(results) == 1 and json.loads(results[0]["output"])["data"]["profile"] == (
        "ontology-relation-v1"
    )
