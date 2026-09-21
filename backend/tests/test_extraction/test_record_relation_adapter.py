"""Relationship transport cannot create missing endpoints in the entity-first pipeline."""

import json

import pytest

from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.ontology_guided.record_discovery import (
    RECORD_PIPELINE,
    RecordDiscoveryPolicy,
)
from tests.test_extraction.test_batch_model_adapter import batch_setup, finalize
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


@pytest.fixture
def source(tool_source):
    return tool_source


def enable_pipeline(adapter, contexts):
    adapter.recognition_pipeline = RECORD_PIPELINE
    adapter.record_discovery = RecordDiscoveryPolicy()
    adapter.reference_resolution = True
    for context in contexts:
        context.tool_inputs["recognition_pipeline"] = RECORD_PIPELINE


def assert_closed_schema(schema, *, batch=False, endpoints=()):
    result = schema["$defs"]["BatchMemberResult"] if batch else schema
    for field in ("entities", "properties", "external_links", "reference_bindings"):
        assert result["properties"][field]["maxItems"] == 0
    if endpoints:
        assert schema["$defs"]["RelationProposal"]["properties"]["object_ids"]["items"][
            "enum"
        ] == list(endpoints)
        assert result["properties"]["relations"]["maxItems"] == len(endpoints)
    else:
        assert result["properties"]["relations"]["maxItems"] == 0


def test_registered_relation_schema_accepts_only_filtered_endpoints():
    from app.services.extraction.ontology_guided.tool_model_adapter import (
        restrict_registered_relation_schema,
    )

    schema = {
        "properties": {
            name: {} for name in (
                "entities", "properties", "relations", "external_links", "reference_bindings"
            )
        },
        "$defs": {
            "RelationProposal": {"properties": {"object_ids": {"items": {}}}},
        },
    }
    restricted = restrict_registered_relation_schema(
        schema, ["registered-object"],
    )

    assert_closed_schema(restricted, endpoints=("registered-object",))


def test_single_relation_rejects_model_created_endpoints(source, monkeypatch):
    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    enable_pipeline(adapter, [context])
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.nodes and not outcome.properties and not outcome.relationship_groups
    assert not outcome.complete
    assert_closed_schema(requests[0]["text_format"]["schema"])
    assert "尚未登记的对象只能写入observations" in requests[0]["instructions"]
    assert "组合来源必须包含fact_eligible=true原文" in requests[0]["instructions"]
    assert "完整覆盖该对象在registered_entities中的source_refs" in requests[0]["instructions"]
    assert "不得按证据单元复制同一关系" in requests[0]["instructions"]
    assert "允许实体、记录组成主体" not in requests[0]["instructions"]


def test_batched_relations_keep_registered_endpoint_authority(source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, relation=True,
    )
    enable_pipeline(adapter, [member.context for member in context.members])
    result = adapter.inspect_work_unit(unit, context, menu)
    assert_closed_schema(requests[0]["text_format"]["schema"], batch=True)
    assert "不能因此省略新对象entities" not in requests[0]["instructions"]
    for member in context.members:
        if member.task_id not in result.member_result_refs:
            continue
        frozen_ref = result.member_result_refs[member.task_id]["discovery_ref"]
        frozen = context.protocol_results[frozen_ref]["value"]
        assert frozen["claim_issues"]
    view = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
    assert view["members"][0]["registered_entities"]
    assert view["members"][0]["reference_dependencies"] == []


def test_single_relation_does_not_repeat_source_after_semantic_rejection(source, monkeypatch):
    from app.services.extraction.ontology_guided import evidence_work

    adapter, task, context, predicate, menu, storage, requests = setup_adapter(
        source, monkeypatch, budget=6,
    )
    enable_pipeline(adapter, [context])
    monkeypatch.setattr(
        evidence_work,
        "plan_evidence_recovery",
        lambda *_args, **_kwargs: evidence_work.EvidenceRecoveryPlan(
            "reproposal", [], "proposal_requires_revision", 2,
        ),
    )

    outcome = adapter.inspect(task, context, predicate, menu)

    assert not outcome.complete
    assert len(requests) == 1
    assert not storage["protocol"]["recovery_used"]
    assert storage["protocol"]["recovery_kind"] == "none"


def test_batched_relation_does_not_repeat_source_after_semantic_rejection(
    source, monkeypatch,
):
    from app.services.extraction.ontology_guided import evidence_work

    adapter, unit, context, menu, _, requests = batch_setup(
        source, monkeypatch, size=1, relation=True,
    )
    enable_pipeline(adapter, [member.context for member in context.members])
    monkeypatch.setattr(
        evidence_work,
        "plan_evidence_recovery",
        lambda *_args, **_kwargs: evidence_work.EvidenceRecoveryPlan(
            "reproposal", [], "proposal_requires_revision", 2,
        ),
    )

    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    outcome, = finalize(adapter, unit, context)

    task_id = unit.members[0].task_id
    assert not outcome.complete
    assert len(requests) == 1
    assert not adapter.work_unit_pending_recovery(context)
    assert not context.protocol_state["member_states"][task_id]["recovery_used"]
    adapter.inspect_work_unit(unit, context, menu)
    assert len(requests) == 1


def test_entity_first_pipeline_allows_explicit_property_repair_members(source, monkeypatch):
    adapter, unit, context, menu, _, requests = batch_setup(source, monkeypatch, size=1)
    enable_pipeline(adapter, [member.context for member in context.members])
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    assert not reviewed.member_errors
    outcome, = finalize(adapter, unit, context)
    assert outcome.complete and len(outcome.properties) == 1
    schema = requests[0]["text_format"]["schema"]["$defs"]["BatchMemberResult"]
    assert schema["properties"]["properties"].get("maxItems") != 0
    assert "人工属性修复成员可输出" in requests[0]["instructions"]
    assert "尚未登记的对象只能写入observations" not in requests[0]["instructions"]


def test_single_property_repair_keeps_property_schema_in_entity_first_pipeline(source, monkeypatch):
    slot = SlotSpec(
        iri="urn:quantity", label="数量", declared_by=["urn:Source"],
        datatype_iris=["http://www.w3.org/2001/XMLSchema#decimal"], canonical_unit="mg",
    )
    source["predicate"], source["options"] = slot, source["task_context"](slot)
    quote = source["quote"]
    source["proposal"] = dict(entities=[], relations=[], external_links=[], observations=[],
                              reference_bindings=[], properties=[dict(
        local_id="quantity", subject_id="subject-server-id", predicate_iri=slot.iri,
        value_quote=quote("5"), field_support=[quote("数量")], unit_support=[quote("mg")],
        qualifiers=dict(polarity="affirmed", modality="asserted",
                        condition_support=[], scope_qualifiers=[]),
        bridge_kind="explicit_assertion", bridge_ref_ids=[],
    )])
    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    enable_pipeline(adapter, [context])
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.complete and len(outcome.properties) == 1
    assert requests[0]["text_format"]["schema"]["properties"]["properties"].get("maxItems") != 0
    assert "尚未登记的对象只能写入observations" not in requests[0]["instructions"]
