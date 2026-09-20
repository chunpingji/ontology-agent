"""Model input compaction must preserve evidence, scope and verification identity."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.extraction.evidence_identity import canonical_json
from app.services.extraction.ontology_guided.model_context_projection import (
    MODEL_CONTEXT_INSTRUCTIONS,
    compact_model_context,
)
from app.services.extraction.ontology_guided.model_reference_projection import (
    ModelReferenceProjection,
)
from tests.test_extraction.test_batch_model_adapter import batch_setup, finalize
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


def member(task_id="task-1", *, section_id="section-a", count=22):
    ref = {"id": "subject", "revision": 3}
    return {
        "task_id": task_id, "stage": "discovery", "subject_ref": ref,
        "schema_card": {
            "subject_ref": deepcopy(ref), "class_iris": ["urn:Subject", "urn:Object"],
            "predicates": [{"iri": "urn:describes", "range_class_iris": ["urn:Object"],
                            "range_classes": [{"iri": "urn:Object", "label": "对象"}]}],
        },
        "registered_refs": [deepcopy(ref)],
        "registered_entities": [{"entity_ref": deepcopy(ref), "class_iri": "urn:Subject",
                                 "source_refs": [], "content_hash": "entity-content"}],
        "source_catalog": [{
            "record_id": f"record-{i}", "section_id": section_id,
            "title": "产品的基本性质", "summary": "仅用于定位的章节摘要。" * 10,
            "summary_source": "llm", "authorized_evidence_ids": [f"evidence-{i}"],
        } for i in range(count)],
        "evidence_refs": [{
            "unit_id": f"unit-{i}", "evidence_id": f"evidence-{i}",
            "role": "target" if i == 0 else "binding", "fact_eligible": i == 0,
            "span_start": 0, "span_end": 10,
        } for i in range(count)],
        "verification_input": None,
    }


def test_section_description_is_shared_without_merging_records_or_fact_permissions():
    original = {"stage": "discovery", "members": [member()]}
    before = deepcopy(original)
    compact = compact_model_context(original)
    row = compact["members"][0]
    sections = compact["shared_context"]["source_sections"]
    assert len(sections) == 1
    assert len(row["source_catalog"]) == 22
    for old, new in zip(original["members"][0]["source_catalog"], row["source_catalog"]):
        assert {**sections[new["section_ref"]],
                **{k: v for k, v in new.items() if k != "section_ref"}} == old
    assert row["evidence_refs"] == original["members"][0]["evidence_refs"]
    assert original == before
    assert len(canonical_json(compact).encode()) < len(canonical_json(original).encode()) * .7
    assert compact_model_context(compact) == compact


def test_shared_description_never_grants_another_members_evidence_permissions():
    left, right = member("left", count=1), member("right", count=1)
    right["evidence_refs"][0].update(role="binding", fact_eligible=False)
    value = compact_model_context({"members": [left, right]})
    assert len(value["shared_context"]["source_sections"]) == 1
    assert [m["source_catalog"] for m in value["members"]] == [
        value["members"][0]["source_catalog"], value["members"][0]["source_catalog"],
    ]
    assert [m["evidence_refs"][0]["fact_eligible"] for m in value["members"]] == [True, False]
    # Equal text in distinct physical sections must not erase section provenance.
    right["source_catalog"][0]["section_id"] = "another-section"
    value = compact_model_context({"members": [left, right]})
    assert len(value["shared_context"]["source_sections"]) == 2


def test_schema_deduplication_keeps_complete_ranges_and_subject_revisions():
    value = member(count=1)
    compact = compact_model_context(value)
    assert "subject_ref" not in compact["schema_card"]
    assert compact["subject_ref"] == value["subject_ref"]
    assert "range_class_iris" not in compact["schema_card"]["predicates"][0]
    assert compact["schema_card"]["predicates"][0]["range_classes"] == [
        {"iri": "urn:Object", "label": "对象"},
    ]
    assert "registered_refs" not in compact
    value["schema_card"]["subject_ref"]["revision"] = 4
    value["schema_card"]["predicates"][0]["range_class_iris"].append("urn:Unexpanded")
    value["registered_refs"].append({"id": "other", "revision": 1})
    compact = compact_model_context(value)
    assert compact["schema_card"] == value["schema_card"]
    assert compact["registered_refs"] == value["registered_refs"]


@pytest.mark.parametrize("change", ["revision", "content", "other_member"])
def test_verification_dependencies_share_only_identical_member_local_content(change):
    value = member(count=1)
    matched = deepcopy(value["registered_entities"][0])
    distinct = deepcopy(matched)
    if change == "revision":
        distinct["entity_ref"]["revision"] += 1
    elif change == "content":
        distinct["source_refs"] = [{"evidence_id": "new-evidence"}]
    else:
        distinct["entity_ref"]["id"] = "other-member-entity"
    binding = {"binding_ref": {"id": "binding", "revision": 2}, "scope": "scope-a"}
    value["reference_dependencies"] = [binding]
    target = {"target_id": "target-1", "content_hash": "exact-frozen-hash",
              "scope": {"scope_id": "scope-a"}, "payload": {"value": "original"}}
    value["verification_input"] = {
        "targets": [target], "entity_dependencies": [matched, distinct],
        "reference_dependencies": [deepcopy(binding)],
    }
    other = member("other", count=1)
    other["registered_entities"] = [distinct]
    compact = compact_model_context({"members": [value, other]})
    verification = compact["members"][0]["verification_input"]
    assert verification["entity_dependency_refs"] == [matched["entity_ref"]]
    assert verification["entity_dependencies"] == [distinct]
    assert verification["reference_dependency_refs"] == [binding["binding_ref"]]
    assert "reference_dependencies" not in verification
    assert verification["targets"] == [target]


def test_record_input_keeps_type_specific_properties_and_source_text():
    value = {"task_id": "record-task", "stage": "discovery",
             "schema_card": {"kind": "record_discovery", "class_cards": [
                 {"class_iri": "urn:A", "properties": [{"iri": "urn:code", "max_count": 1}]},
                 {"class_iri": "urn:B", "properties": [{"iri": "urn:code", "max_count": 2}]},
             ]}, "source_catalog": member(count=2)["source_catalog"],
             "evidence_units": [{"evidence_id": "ev", "text": "逐字原文", "fact_eligible": True}]}
    compact = compact_model_context(value)
    assert compact["schema_card"] == value["schema_card"]
    assert compact["evidence_units"] == value["evidence_units"]
    assert "subject_ref" not in compact


def test_actual_batch_request_shares_sections_and_still_verifies_each_member(source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch)
    section_ids = {record.section_node_id for record in adapter.index.records}
    adapter.metadata = SimpleNamespace(node_summaries=[SimpleNamespace(
        node_id=identity, heading="shared heading", summary="shared summary", summary_source="llm",
    ) for identity in section_ids])
    result = adapter.inspect_work_unit(unit, context, menu)
    assert not result.member_errors
    outcomes = finalize(adapter, unit, context)
    assert all(outcome.complete and len(outcome.properties) == 1 for outcome in outcomes)
    view = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
    sections = view["shared_context"]["source_sections"]
    assert len(sections) < sum(len(m["source_catalog"]) for m in view["members"])
    assert all(entry["section_ref"] in sections
               for m in view["members"] for entry in m["source_catalog"])
    assert "shared_context.source_sections" in requests[0]["instructions"]
    assert len(requests) == 2


def test_old_frozen_instructions_do_not_receive_compact_initial_input(source, monkeypatch):
    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    instructions = adapter._stage_instructions
    monkeypatch.setattr(adapter, "_stage_instructions", lambda *args, **kwargs:
                        instructions(*args, **kwargs).replace(MODEL_CONTEXT_INSTRUCTIONS, ""))
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete
    for request in requests:
        view = json.loads(request["input_items"][0]["content"][0]["text"])
        assert "shared_context" not in view
        assert "subject_ref" in view["schema_card"]
        assert all("section_ref" not in entry for entry in view["source_catalog"])


def test_resume_preserves_saved_legacy_input_and_only_compacts_a_new_stage(source, monkeypatch):
    call = {"type": "function_call", "id": "output-item", "call_id": "call-1",
            "name": "get_schema_card", "arguments": json.dumps({
                "subject_id": source["options"]["task"].subject.entity_id, "predicate_iri": None,
            })}
    adapter, task, context, predicate, menu, stored, _ = setup_adapter(
        source, monkeypatch, tool_calls=[call], stop_at="model_turn",
    )
    instructions = adapter._stage_instructions
    monkeypatch.setattr(adapter, "_stage_instructions", lambda *args, **kwargs:
                        instructions(*args, **kwargs).replace(MODEL_CONTEXT_INSTRUCTIONS, ""))
    with pytest.raises(RuntimeError, match="pause after durable commit"):
        adapter.inspect(task, context, predicate, menu)
    saved = deepcopy(stored)
    saved_input = deepcopy(saved["protocol"]["stage_input_items"])
    adapter, task, context, predicate, menu, storage, requests = setup_adapter(source, monkeypatch)
    storage.update(saved)
    context.protocol_state = deepcopy(saved["protocol"])
    context.protocol_results = deepcopy(saved["results"])
    context.remaining_model_calls = 4 - len(saved["reservations"])
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete
    saved_wire = ModelReferenceProjection({
        "input": saved_input, "instructions": saved["protocol"]["active_instructions"],
    }).request
    assert requests[0]["input_items"][0] == saved_wire["input"][0]
    assert requests[0]["instructions"] == saved_wire["instructions"]
    verification = json.loads(requests[-1]["input_items"][0]["content"][0]["text"])
    assert verification["stage"] == "verification"
    assert "shared_context" in verification
    assert len(requests) == 3
