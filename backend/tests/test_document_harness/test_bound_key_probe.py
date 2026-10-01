"""Regression boundaries for experimental unified model-selected ID bindings."""

from copy import deepcopy
from types import SimpleNamespace

import jsonschema
import pytest

from app.evaluation.harness_bound_key_probe import (
    INSTRUCTION,
    STAGE,
    BoundAnswer,
    BoundEngine,
    apply_bindings,
    guard_answer,
)
from app.services.document_harness.planning import context_window
from app.services.document_harness.protocols import INSTRUCTIONS, STAGES
from tests.test_document_harness.test_key_realign_probe import prefix
from tests.test_document_harness.test_lookup import NS, quote
from tests.test_document_harness.test_lookup import (
    lookup_fixture as source_lookup_fixture,
)


@pytest.fixture
def lookup_fixture(db, tmp_path):
    return source_lookup_fixture.__wrapped__(db, tmp_path)


def answer(payload, names=("A1", "A2"), grouping="multiple_objects"):
    candidates = {v["value"]: c["candidate_id"] for c in payload["key_candidates"]
                  for prop in c["key_values"] for v in prop["values"]}
    return {"groups": {"E1": {"grouping": grouping, "reason": "Source wording and key meanings",
            "members": [{"anchor": quote(name), "role": "object", "evidence": ["S1"],
                         "identifiers": [{"property_iri": NS + "code", "quote": quote(name),
                                          "candidate_ids": [candidates[name]]
                                          if name in candidates else []}]}
                        for name in names]}}}


def build(fixture, monkeypatch, transform=lambda data: data, carry=True):
    initial = prefix(fixture)
    monkeypatch.setitem(INSTRUCTIONS, STAGE, INSTRUCTION)
    def invoke(stage, payload, schema):
        assert stage == STAGE
        result = transform(answer(payload))
        jsonschema.validate(result, schema)
        return result
    return BoundEngine(ir=initial.ir, catalog=initial.catalog, state=initial.state,
                       invoke=invoke, save=lambda _: None, should_stop=lambda: False,
                       lookup=fixture[2], trace=lambda *_: None, carry_bindings=carry)


def test_model_bindings_generate_only_member_fields_and_leave_aggregate_observation(
    lookup_fixture, monkeypatch,
):
    engine = build(lookup_fixture, monkeypatch)
    old = deepcopy(engine.state)
    engine.bind(engine.windows[0])
    members = engine.entities(engine.windows[0])
    assert [e["label"] for e in members] == ["A1", "A2"]
    for entity in members:
        assert entity["class_iri"] is None and entity["state"] == "candidate"
        assert [engine.state["fields"][key]["value"] for key in entity["field_ids"]] == [
            entity["label"],
        ]
        assert engine.state["source_candidates"][entity["id"]]["identity_status"] == "not_checked"
    assert engine.state["entities"]["document"] == old["entities"]["document"]
    assert any(f["value"] == "A1/A2" for f in engine.state["fields"].values())
    assert engine.state["cursor"]["main"]["windows_discovered"] == 1
    assert not engine.state.get("properties")


@pytest.mark.parametrize("carry", [True, False])
def test_identical_binding_fields_with_and_without_downstream_context(
    lookup_fixture, monkeypatch, carry,
):
    engine = build(lookup_fixture, monkeypatch, carry=carry)
    engine.bind(engine.windows[0])
    entity = engine.entities(engine.windows[0])[0]
    window = context_window(engine.ir, engine.windows[0], [entity], engine.state["fields"])
    subject = engine.entity_input(entity, window)
    alias = subject["fields"][0]["field_id"]
    def invoke(stage, payload, schema):
        assert ("identifier_binding" in payload["subject"]) == carry
        assert ("binding_review_policy" in payload) == carry
        assert payload["subject"]["fields"][0]["value"] == "A1"
        return {"properties": {alias: {"mappings": [{"predicate_iri": NS + "code",
                "value_component": "whole", "value_quote": None, "confidence": 0.9}],
                "reason": "This member's exact original ID"}}}
    engine.invoke = invoke
    engine.call("property_alignment", {"subject": subject, "property_field_ids": [alias]}, {})


def test_swapped_member_quote_is_rejected_before_state_replacement(lookup_fixture, monkeypatch):
    def change(data):
        data["groups"]["E1"]["members"][0]["identifiers"][0]["quote"] = quote("A2")
        return data
    engine = build(lookup_fixture, monkeypatch, change)
    before = deepcopy(engine.state)
    with pytest.raises(ValueError, match="identifier_outside_member"):
        engine.bind(engine.windows[0])
    assert engine.state == before


def test_wrong_real_source_candidate_cannot_bind_another_key(lookup_fixture, monkeypatch):
    def change(data):
        members = data["groups"]["E1"]["members"]
        members[0]["identifiers"][0]["candidate_ids"] = (
            members[1]["identifiers"][0]["candidate_ids"]
        )
        return data
    engine = build(lookup_fixture, monkeypatch, change)
    before = deepcopy(engine.state)
    with pytest.raises(ValueError, match="source_key_mismatch"):
        engine.bind(engine.windows[0])
    assert engine.state == before


def test_one_object_can_have_two_identifiers_without_becoming_two_members(
    lookup_fixture, monkeypatch,
):
    def change(data):
        group = data["groups"]["E1"]
        first, second = group["members"]
        first["anchor"] = quote("A1/A2")
        first["identifiers"].extend(second["identifiers"])
        group.update(grouping="single_object", members=[first])
        return data
    engine = build(lookup_fixture, monkeypatch, change)
    engine.bind(engine.windows[0])
    members = engine.entities(engine.windows[0])
    assert len(members) == 1
    assert sorted(engine.state["fields"][key]["value"] for key in members[0]["field_ids"]) == [
        "A1", "A2",
    ]


def test_multiple_object_claim_cannot_hide_two_ids_on_one_member(lookup_fixture, monkeypatch):
    def change(data):
        group = data["groups"]["E1"]
        first, second = group["members"]
        first["anchor"] = quote("A1/A2")
        first["identifiers"].extend(second["identifiers"])
        group["members"] = [first]
        return data
    engine = build(lookup_fixture, monkeypatch, change)
    before = deepcopy(engine.state)
    with pytest.raises(ValueError, match="group_cardinality_conflict"):
        engine.bind(engine.windows[0])
    assert engine.state == before


def test_single_composite_without_matching_source_is_permitted(lookup_fixture, monkeypatch):
    def change(data):
        group = data["groups"]["E1"]
        member = group["members"][0]
        member["anchor"] = quote("A1/A2")
        member["identifiers"] = [{"property_iri": NS + "code", "quote": quote("A1/A2"),
                                  "candidate_ids": []}]
        group.update(grouping="single_object", members=[member])
        return data
    engine = build(lookup_fixture, monkeypatch, change)
    engine.bind(engine.windows[0])
    assert len(engine.entities(engine.windows[0])) == 1
    assert not engine.state.get("source_candidates")


def test_unknown_source_reference_rejected_without_silent_repair(lookup_fixture, monkeypatch):
    engine = build(lookup_fixture, monkeypatch)
    data = answer({"key_candidates": []})
    data["groups"]["E1"]["members"][0]["identifiers"][0]["candidate_ids"] = ["invented"]
    before = deepcopy(engine.state)
    with pytest.raises(ValueError, match="source_key_mismatch"):
        apply_bindings(
            engine, engine.windows[0], BoundAnswer.model_validate(data), {}, [NS + "code"],
        )
    assert engine.state == before


def test_unresolved_and_overlapping_group_cannot_be_applied(lookup_fixture, monkeypatch):
    engine = build(lookup_fixture, monkeypatch)
    before = deepcopy(engine.state)
    data = answer({"key_candidates": []})
    data["groups"]["E1"].update(grouping="unresolved", members=[])
    with pytest.raises(ValueError, match="group_unresolved"):
        apply_bindings(
            engine, engine.windows[0], BoundAnswer.model_validate(data), {}, [NS + "code"],
        )
    data = answer({"key_candidates": []}, names=("A1", "A1"))
    with pytest.raises(ValueError, match="members_overlap"):
        apply_bindings(
            engine, engine.windows[0], BoundAnswer.model_validate(data), {}, [NS + "code"],
        )
    assert engine.state == before


def test_downstream_accepted_aggregate_cannot_override_member_binding():
    subject = {"identifier_binding": {"identifiers": [{"property_iri": NS + "code",
                                                        "quote": quote("A1")}]}}
    payload = {"candidates": [{"id": "C1", "kind": "properties", "subject": subject,
                                "predicate_definition": {"iri": NS + "code"},
                                "value": "A1/A2"}]}
    with pytest.raises(ValueError, match="downstream_value_conflict"):
        guard_answer("evidence_review", payload, SimpleNamespace(judgments={
            "C1": SimpleNamespace(verdict="accepted"),
        }))
    guard_answer("evidence_review", payload, SimpleNamespace(judgments={
        "C1": SimpleNamespace(verdict="rejected"),
    }))


def test_alignment_cannot_reintroduce_aggregate_span(lookup_fixture, monkeypatch):
    engine = build(lookup_fixture, monkeypatch)
    engine.bind(engine.windows[0])
    entity = engine.entities(engine.windows[0])[0]
    window = context_window(engine.ir, engine.windows[0], [entity], engine.state["fields"])
    subject = engine.entity_input(entity, window)
    alias = subject["fields"][0]["field_id"]
    result = STAGES["property_alignment"].model_validate({
        "properties": {alias: {"mappings": [{"predicate_iri": NS + "code",
            "value_component": "span", "value_quote": quote("A1/A2"), "confidence": 1}],
            "reason": "Conflicting aggregate value"}},
    })
    with pytest.raises(ValueError, match="downstream_value_conflict"):
        guard_answer("property_alignment", {"subject": subject}, result)
