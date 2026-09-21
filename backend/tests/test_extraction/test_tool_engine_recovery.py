"""Recovery spends the same request budget and requires semantic/source progress."""

import copy
import json

import pytest

from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


@pytest.mark.parametrize("change", [False, True])
def test_empty_targets_recover_with_a_new_proposal_instead_of_empty_verification(
    source, monkeypatch, change,
):
    from app.services.extraction.ontology_guided import evidence_work
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery", lambda *_a, **_kw:
                        evidence_work.EvidenceRecoveryPlan("supplement", [], "unread_source", 3))
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if view["stage"] == "verification":
            assert view["verification_input"]["targets"]
        elif len(requests) == 1 or not change:
            payload = copy.deepcopy(source["proposal"])
            for entity in payload["entities"]:
                entity["mentions"] = [source["quote"]("原文中没有这个名称")]
            turn.output_items[0]["content"][0]["text"] = json.dumps(payload)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert storage["protocol"]["recovery_kind"] == "reproposal"
    assert storage["protocol"]["assertion_generation"] == (2 if change else 1)
    assert len(requests) == len(storage["reservations"]) == (3 if change else 2)
    assert outcome.complete == change
    assert bool(outcome.relationship_groups) == change
    frozen = next(row["value"] for row in storage["results"].values()
                  if row["field"] == "discovery")
    assert any("source_excerpt_mismatch" in issues for issues in frozen["claim_issues"].values())


def test_legacy_empty_supplement_closes_without_spending_remaining_calls(source, monkeypatch):
    from app.services.extraction.ontology_guided import evidence_work
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery", lambda *_a, **_kw:
                        evidence_work.EvidenceRecoveryPlan("supplement", [], "unread_source", 3))
    transport = local_client.responses_create
    checkpoint = context._protocol_hook

    def invalid_discovery(client, **kwargs):
        turn = transport(client, **kwargs)
        payload = copy.deepcopy(source["proposal"])
        for entity in payload["entities"]:
            entity["mentions"] = [source["quote"]("原文中没有这个名称")]
        turn.output_items[0]["content"][0]["text"] = json.dumps(payload)
        return turn

    def pause_before_recovery(value):
        checkpoint(value)
        if value["recovery_used"]:
            raise RuntimeError("pause before recovery")

    monkeypatch.setattr(local_client, "responses_create", invalid_discovery)
    context.bind_protocol_hook(pause_before_recovery)
    with pytest.raises(RuntimeError, match="pause before recovery"):
        adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 1
    context.protocol_state = copy.deepcopy(storage["protocol"])
    context.protocol_results = copy.deepcopy(storage["results"])
    context.protocol_state.update(
        stage="verification", recovery_kind="evidence",
        active_instructions=adapter._stage_instructions("verification"),
    )
    context.bind_protocol_hook(checkpoint)
    context.remaining_model_calls = 0
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.complete and not outcome.relationship_groups
    assert len(requests) == len(storage["reservations"]) == 1
    assert storage["protocol"]["request_attempt"] == 1
    assert storage["protocol"]["assertion_generation"] == 1


@pytest.mark.parametrize("change", [False, True])
def test_reproposal_rechecks_changed_claims_and_does_not_loop(source, monkeypatch, change):
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if turn.output_items[0]["type"] == "function_call":
            return turn
        payload = json.loads(turn.output_items[0]["content"][0]["text"])
        if len(requests) == 2:
            for target in payload["verifications"]:
                for facet in target["facets"]:
                    if facet["name"] == "type":
                        facet["verdict"] = "undetermined"
                        facet["support"] = []
        elif len(requests) == 3 and change:
            # A different grounded endpoint is a semantic edit, not a new label order.
            payload = copy.deepcopy(source["proposal"])
            payload["entities"] = payload["entities"][:1]
            payload["relations"][0]["object_ids"] = ["b"]
            payload["relations"][0]["selection"] = "all"
            payload["relations"][0]["selection_support"] = []
        turn.output_items[0]["content"][0]["text"] = json.dumps(payload)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert storage["protocol"]["recovery_used"]
    assert storage["protocol"]["recovery_kind"] == "reproposal"
    assert len(storage["reservations"]) == (4 if change else 3)
    assert storage["protocol"]["assertion_generation"] == (2 if change else 1)
    if change:
        assert outcome.complete and len(outcome.edges) == 1
    else:
        assert not outcome.complete and not outcome.relationship_groups


def test_recovery_does_not_borrow_calls_from_the_next_lineage(source, monkeypatch):
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    context.remaining_model_calls = 2
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if len(requests) == 2:
            value = json.loads(turn.output_items[0]["content"][0]["text"])
            for target in value["verifications"]:
                for facet in target["facets"]:
                    facet["verdict"] = "undetermined"
                    facet["support"] = []
            turn.output_items[0]["content"][0]["text"] = json.dumps(value)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.complete and not outcome.relationship_groups
    assert len(requests) == len(storage["reservations"]) == 2
    assert not storage["protocol"]["recovery_used"]
    assert storage["protocol"]["outcome_ref"]



def test_reproposal_receives_specific_frozen_source_errors_without_expanding_scope(
    source, monkeypatch,
):
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if turn.output_items[0]["type"] == "function_call":
            return turn
        part = turn.output_items[0]["content"][0]
        if len(requests) == 1:
            payload = json.loads(part["text"])
            payload["entities"][0]["mentions"] = [source["quote"]("对象丁", supplemental=True)]
            part["text"] = json.dumps(payload)
        elif len(requests) == 3:
            view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
            issues = {(item["field_path"], item["code"]) for item in view["feedback"]}
            assert ("entities.0", "fact_source_outside_scope") in issues
            assert ("relations.0", "entity_dependency_invalid") in issues
            first = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
            assert view["evidence_units"] == first["evidence_units"]
            assert view["context_hash"] == first["context_hash"]
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.relationship_groups) == 1
    assert storage["protocol"]["recovery_kind"] == "reproposal"
    assert storage["protocol"]["evidence_revision"] == 1
    assert len(requests) == len(storage["reservations"]) == 4


@pytest.mark.parametrize("new_evidence,cold_resume", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("legacy_input", [False, True])
def test_supplement_reverifies_only_after_confirming_new_authorized_sources(
    source, monkeypatch, new_evidence, cold_resume, legacy_input,
):
    from types import SimpleNamespace

    from app.services.extraction.ontology_guided import evidence_work, tool_runtime
    from app.services.extraction.ontology_guided.contracts import MetadataSnapshot
    from app.services.extraction.ontology_guided.model_context_projection import (
        MODEL_CONTEXT_INSTRUCTIONS,
    )
    from app.services.llm import local_client

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    if legacy_input:
        instructions = adapter._stage_instructions
        monkeypatch.setattr(adapter, "_stage_instructions", lambda *args, **kwargs:
                            instructions(*args, **kwargs).replace(MODEL_CONTEXT_INSTRUCTIONS, ""))
    index = source["index"]
    adapter.metadata = MetadataSnapshot(
        snapshot_id="metadata", analysis_id=index.ir.analysis_id,
        document_hash=index.ir.document_hash, structure_hash=index.ir.structure_hash,
        summary_version="v1", generation_source="structure_only", dependency_hash="a" * 64,
    )
    seen = {fragment.anchor.evidence_id for fragment in context.fragments}
    record = next(r for r in index.records
                  if any(u.evidence_id not in seen for u in r.source_units))
    monkeypatch.setattr(evidence_work, "plan_evidence_recovery", lambda *_a, **_kw:
                        evidence_work.EvidenceRecoveryPlan("supplement", [record.record_id],
                                                          "unread_source_match", 2))
    monkeypatch.setattr(tool_runtime, "plan_slot", lambda *_a, **_kw: SimpleNamespace(
        records=[SimpleNamespace(record_id=record.record_id)] if new_evidence else [],
    ))
    transport = local_client.responses_create

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        if len(requests) == 2:
            value = json.loads(turn.output_items[0]["content"][0]["text"])
            for target in value["verifications"]:
                for facet in target["facets"]:
                    if facet["name"] == "type":
                        facet["verdict"] = "undetermined"
                        facet["support"] = []
            turn.output_items[0]["content"][0]["text"] = json.dumps(value)
        if len(requests) == 3:
            turn.output_items[:] = [{
                "type": "function_call", "id": "item-retrieve", "call_id": "call-retrieve",
                "name": "retrieve_evidence", "arguments": json.dumps({
                    "subject_id": task.subject.entity_id, "predicate_iri": predicate.iri,
                    "missing_facets": ["type"],
                }),
            }]
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    old_requests = []
    if cold_resume:
        checkpoint = context._protocol_hook

        def pause_after_tool(value):
            checkpoint(value)
            if any(row["field"] == "tool_result" and row["value"]["call_id"] == "call-retrieve"
                   for row in value.get("result_changes", {}).values()):
                raise RuntimeError("pause after supplement commit")

        context.bind_protocol_hook(pause_after_tool)
        with pytest.raises(RuntimeError, match="pause after supplement commit"):
            adapter.inspect(task, context, predicate, menu)
        assert len(requests) == 3
        saved, metadata, old_requests = copy.deepcopy(storage), adapter.metadata, list(requests)
        adapter, task, context, predicate, menu, storage, requests = setup_adapter(
            source, monkeypatch, budget=6,
        )
        adapter.metadata = metadata
        storage.update(saved)
        context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
        context.remaining_model_calls = 2
    outcome = adapter.inspect(task, context, predicate, menu)
    assert len(old_requests) + len(requests) == (4 if new_evidence else 3)
    assert storage["protocol"]["assertion_generation"] == 1
    assert storage["protocol"]["evidence_revision"] == (2 if new_evidence else 1)
    assert len([row for row in storage["results"].values() if row["field"] == "discovery"]) == 1
    if new_evidence:
        view = json.loads(requests[-1]["input_items"][0]["content"][0]["text"])
        from app.services.extraction.ontology_guided.model_reference_projection import (
            project_reference_payload,
        )

        seen_wire = project_reference_payload({"evidence_ids": sorted(seen)})["evidence_ids"]
        extra = [unit for unit in view["evidence_units"] if unit["evidence_id"] not in seen_wire]
        assert extra and all(not unit["fact_eligible"] for unit in extra)
        assert view["context_hash"] == project_reference_payload({
            "context_hash": storage["protocol"]["context_hash"],
        })["context_hash"]
        assert ("shared_context" in view) is not legacy_input
        assert ("subject_ref" in view["schema_card"]) is legacy_input
        assert outcome.complete and outcome.relationship_groups
        assert len([row for row in storage["results"].values()
                    if row["field"] == "tool_result"]) == 3
    else:
        assert not outcome.complete and not outcome.relationship_groups
