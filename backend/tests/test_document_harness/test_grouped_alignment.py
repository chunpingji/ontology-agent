"""Regression of identifier ownership and grouped relation semantics on the online engine."""

from collections import defaultdict
from copy import deepcopy

import jsonschema
import pytest
from test_lookup import NS, quote
from test_lookup import lookup_fixture as lookup_fixture

from app.services.document_harness.controller import Engine, Paused
from app.services.document_harness.coreference import project_coreferences
from app.services.document_harness.protocols import (
    PropertyMapping,
    ReferentCandidates,
    RelationGroupChoice,
    stage_schema,
)
from app.services.document_harness.referents import (
    run_referent_alignment,
    validate_bound_property,
    validate_partitions,
)
from app.services.document_harness.relation_groups import add_groups, review_groups
from app.services.document_harness.source import build_windows


def proposal(spans=None):
    spans = spans or {text: text for text in ("A1/A2", "A1", "A2")}
    return {"new_spans": [], "expressions": [
        {"id": key, "property_iri": NS + "code", "span_id": spans[value]}
        for key, value in (("whole", "A1/A2"), ("a", "A1"), ("b", "A2"))
    ], "partitions": [
        {"id": "single", "members": [
            {"id": "whole", "expression_ids": ["whole"]},
        ], "reason": "One compound identifier"},
        {"id": "members", "members": [
            {"id": key, "expression_ids": [key]}
            for key in ("a", "b")
        ], "reason": "Two mentioned objects, regardless of use selection"},
    ]}


def engine_fixture(fixture, selected="members", invoke=None, save=None):
    ir, catalog, lookup, *_ = fixture
    card = catalog.classes[NS + "Object"]
    catalog = catalog.model_copy(update={"classes": {
        **catalog.classes, card.iri: card.model_copy(update={"properties": tuple(
            p.model_copy(update={"identity_key": True}) if p.iri == NS + "code" else p
            for p in card.properties
        )}),
    }})
    window = build_windows(ir)[0]
    ref = window.resolve(ir, quote("A1/A2"))
    state = {
        "cursor": {"main": {"window_index": 0, "stage": "referent_alignment",
                            "windows_discovered": 1, "windows_reviewed": 0,
                            "windows_total": 1, "scope_complete": False}},
        "windows": {window.id: {"complete": True}},
        "entities": {
            "document": {"id": "document", "label": "Report", "role": "document_root",
                         "class_iri": NS + "Report", "class_label": "Report", "state": "accepted",
                         "reason": "selected", "field_ids": [], "evidence": []},
            "old": {"id": "old", "label": "A1/A2", "referent": ref, "name": ref,
                    "class_iri": NS + "Object", "class_label": "Object", "role": "object",
                    "state": "candidate", "reason": "draft", "field_ids": [],
                    "evidence": [ref], "window_id": window.id},
        },
        "window_entities": {window.id: {"ids": ["old"]}}, "fields": {},
    }
    calls = []

    def respond(stage, payload, schema):
        calls.append((stage, deepcopy(payload)))
        if invoke:
            return invoke(stage, payload, schema)
        if stage == "referent_candidates":
            available = {s["text"]: s["span_id"] for s in reversed(payload["evidence_spans"])
                         if s["identifier_candidate"]}
            needed = [value for value in ("A1/A2", "A1", "A2") if value not in available]
            result = ({"new_spans": [quote(value) for value in needed],
                       "expressions": [], "partitions": []} if needed else proposal(available))
        else:
            assert stage == "referent_selection"
            result = {"verdict": "supported" if selected is not None else "unresolved",
                      "selected_partition_id": selected, "confidence": 0.98,
                      "evidence_span_ids": [next(s["span_id"] for s in payload["evidence_spans"]
                                                if s["text"] == "A1/A2")],
                      "reason": "Reference, not use selection"}
        jsonschema.validate(result, schema)
        return result

    engine = Engine(ir=ir, catalog=catalog, state=state, invoke=respond,
                    save=save or (lambda _: None), should_stop=lambda: False, lookup=lookup)
    return engine, window, calls


@pytest.mark.parametrize("selected,values", [
    ("members", ["A1", "A2"]), ("single", ["A1/A2"]), (None, []),
])
def test_online_refinement_queries_original_expressions_and_binds_members(
    lookup_fixture, selected, values,
):
    engine, window, calls = engine_fixture(lookup_fixture, selected)
    run_referent_alignment(engine, window)
    entities = [e for e in engine.entities(window) if e.get("identity_binding")]
    assert [e["label"] for e in entities] == values
    queries = [arg["queries"] for op, arg in lookup_fixture[3] if op == "query"][-1]
    assert [q["property_filters"][0]["value"] for q in queries] == ["A1/A2", "A1", "A2"]
    assert [r["outcome"] for r in calls[-1][1]["lookup_feedback"]["results"]] == [
        "no_match", "matches", "matches",
    ]
    assert {o["value"] for c in calls[0][1]["key_candidates"]
            for o in c["key_occurrences"]} == {"A1", "A2"}
    for entity in entities:
        binding = engine.entity_input(entity, window, typed=True)["identity_binding"]
        assert binding["identifiers"][0]["value"] == entity["label"]
        assert binding["identifiers"][0]["identity_status"] == "not_checked"
    assert not engine.state.get("relations")
    if selected is None:
        assert engine.state["entities"]["old"]["referent_unresolved"]
    else:
        assert "old" not in engine.state["entities"]
        assert all(e["state"] == "candidate" for e in entities)


def test_identifier_partition_keeps_uncovered_typed_mention_explicitly_unresolved(
    lookup_fixture,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    ref = window.resolve(engine.ir, quote("trial"))
    engine.state["entities"]["extra"] = {
        "id": "extra", "label": "trial", "name": ref, "referent": ref,
        "class_iri": NS + "Object", "class_label": "Object", "role": "object",
        "state": "candidate", "reason": "draft", "field_ids": [],
        "evidence": [ref], "window_id": window.id,
    }
    engine.state["window_entities"][window.id]["ids"].append("extra")

    run_referent_alignment(engine, window)

    assert [e["label"] for e in engine.entities(window) if e.get("identity_binding")] == [
        "A1", "A2",
    ]
    extra = engine.state["entities"]["extra"]
    assert extra["state"] == "unresolved" and extra["referent_unresolved"] is True
    assert "未覆盖该草案提及" in extra["reason"]
    assert "extra" in engine.state["window_entities"][window.id]["ids"]
    assert "old" not in engine.state["entities"]


def test_identifier_partition_disjoint_from_every_input_mention_creates_no_entity(
    lookup_fixture,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    ref = window.resolve(engine.ir, quote("trial"))
    engine.state["entities"]["old"].update(label="trial", name=ref, referent=ref,
                                            evidence=[ref])

    run_referent_alignment(engine, window)

    assert engine.state["entities"]["old"]["state"] == "unresolved"
    assert engine.state["entities"]["old"]["referent_unresolved"] is True
    assert not any(e.get("identity_binding") for e in engine.entities(window))
    assert next(iter(engine.state["referent_work"].values()))["selection_issue"] == (
        "no_input_mention_covered"
    )


@pytest.mark.parametrize("bad", ["omission", "overlap", "foreign", "duplicate"])
def test_invalid_partition_cannot_become_live_entities(lookup_fixture, bad):
    engine, window, _ = engine_fixture(lookup_fixture)
    data = proposal()
    partition = data["partitions"][1]
    if bad == "omission":
        partition["members"].pop()
    elif bad == "overlap":
        partition["members"][0].update(expression_ids=["whole"])
    elif bad == "foreign":
        data["expressions"][0]["span_id"] = "invented"
    else:
        partition["members"][1]["id"] = "a"
    with pytest.raises(ValueError):
        validate_partitions(
            ReferentCandidates.model_validate(data),
            {text: window.resolve(engine.ir, quote(text)) for text in ("A1/A2", "A1", "A2")},
            engine.ir, [NS + "code"],
            ("A1/A2", "A1", "A2"),
        )
    assert list(engine.state["entities"]) == ["document", "old"]


def test_pause_after_query_reuses_feedback_and_does_not_double_register(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    original = engine.invoke
    engine.invoke = lambda stage, p, s: (
        (_ for _ in ()).throw(Paused()) if stage == "referent_selection" else original(stage, p, s)
    )
    with pytest.raises(Paused):
        run_referent_alignment(engine, window)
    assert "old" in engine.state["entities"]
    engine.invoke = original
    run_referent_alignment(engine, window)
    run_referent_alignment(engine, window)
    assert len(engine.entities(window)) == 2
    # One bounded browse and one exact query; neither is replayed on resume.
    assert sum(op == "query" for op, _ in lookup_fixture[3]) == 2


def test_bound_property_rejects_other_member_value(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    run_referent_alignment(engine, window)
    entity = engine.entities(window)[0]
    field = engine.state["fields"][entity["field_ids"][0]]
    mapping = PropertyMapping(predicate_iri=NS + "code", value_component="whole",
                              value_quote=None, confidence=0.99)
    validate_bound_property(entity, field, mapping, {"value": "A1"})
    with pytest.raises(ValueError, match="property_conflicts"):
        validate_bound_property(entity, field, mapping, {"value": "A2"})


@pytest.mark.parametrize("participation,timing,verdict,expected", [
    ("options", "unspecified", "accepted", "accepted"),
    ("unknown", "unspecified", "accepted", "unresolved"),
    ("all", "parallel", "accepted", "accepted"),
    ("all", "sequential", "unresolved", "unresolved"),
])
def test_group_review_preserves_options_without_binary_edges(
    lookup_fixture, participation, timing, verdict, expected,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    run_referent_alignment(engine, window)
    objects = engine.entities(window)
    for obj in objects:
        obj["state"] = "accepted"
    subject = engine.state["entities"]["document"]
    aliases = engine.entity_aliases(window)
    proposed = RelationGroupChoice(
        object_ids=[aliases[e["id"]] for e in objects], predicate_iri=NS + "uses",
        participation=participation, timing=timing, selection="unspecified",
        polarity="positive", evidence=["S1"], confidence=0.95, reason="Candidate group",
    )
    changes = defaultdict(dict)
    add_groups(engine, window, subject, objects, aliases,
               [{"iri": NS + "uses", "label": "uses"}], [proposed], changes)
    engine.commit(changes)

    def review(stage, payload, schema):
        result = {"judgments": {c["id"]: {
            "verdict": verdict, "confidence": 0.96, "evidence": ["S1"], "reason": "Source check",
        } for c in payload["candidates"]}, "type_concerns": []}
        jsonschema.validate(result, schema)
        return result
    engine.invoke = review
    review_groups(engine, window)
    group = next(iter(engine.state["relation_groups"].values()))
    assert group["state"] == expected
    assert group["timing_state"] == (expected if timing != "unspecified" else "unresolved")
    assert not engine.state.get("relations")


def test_relation_group_schema_forbids_unavailable_endpoints():
    schema = stage_schema("assertion_alignment", source_ids=["S1"],
                          entity_ids=["E1"], relation_iris=[NS + "uses"])
    assert schema["properties"]["relation_groups"]["maxItems"] == 0


def test_group_with_out_of_range_member_is_observed_without_losing_valid_group(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    run_referent_alignment(engine, window)
    objects = engine.entities(window)
    invalid = {**objects[0], "id": "out-of-range", "class_iri": NS + "Report"}
    engine.state["entities"][invalid["id"]] = invalid
    aliases = {**engine.entity_aliases(window), invalid["id"]: "E9"}
    valid_members = [aliases[obj["id"]] for obj in objects]

    def group(member_ids):
        return RelationGroupChoice(
            object_ids=member_ids, predicate_iri=NS + "uses",
            participation="unknown", selection="unspecified", timing="unspecified",
            polarity="uncertain", evidence=["S1"], confidence=0.9,
            reason="Source mentions several objects",
        )

    changes = defaultdict(dict)
    covered = add_groups(
        engine, window, engine.state["entities"]["document"], [*objects, invalid],
        aliases, [{"iri": NS + "uses", "label": "uses"}],
        [group(valid_members), group([valid_members[0], "E9"])], changes,
    )

    assert len(changes["relation_groups"]) == 1
    assert next(iter(changes["relation_groups"].values()))["object_ids"] == [
        obj["id"] for obj in objects
    ]
    assert covered == {(NS + "uses", obj["id"]) for obj in objects}
    assert len(changes["observations"]) == 1
    observation = next(iter(changes["observations"].values()))
    assert observation["kind"] == "relation"
    assert observation["evidence"]
    assert "整组未采信" in observation["reason"]


@pytest.mark.parametrize("property_verdict", ["accepted", "unresolved", "rejected"])
def test_local_identifier_review_never_confirms_source_identity_or_overrides_rejection(
    lookup_fixture, property_verdict,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    original = engine.invoke

    def respond(stage, payload, schema):
        source_ids = [s["source_id"] for s in payload["sources"]]
        if stage in {"referent_candidates", "referent_selection"}:
            return original(stage, payload, schema)
        if stage == "assertion_alignment":
            result = {
                "properties": {fid: {"mappings": [{"predicate_iri": NS + "code",
                    "value_component": "whole", "value_quote": None, "confidence": 0.97}],
                    "reason": "Identifier belongs to this member"}
                    for fid in payload["property_field_ids"]},
                "relations": [], "relation_groups": [], "complete": True,
            }
            if payload["objects"] and payload["card"]["relations"]:
                result["relation_groups"] = [{
                    "predicate_iri": NS + "uses",
                    "object_ids": [obj["entity_id"] for obj in payload["objects"]],
                    "participation": "unknown", "selection": "unspecified",
                    "timing": "unspecified", "polarity": "uncertain", "conditions": [],
                    "evidence": source_ids, "confidence": 0.9, "reason": "Slash leaves use open",
                }]
        elif stage == "coreference_review":
            result = {"judgments": {p["pair_id"]: {
                "verdict": "unresolved", "basis": "insufficient", "confidence": 0.95,
                "evidence": [], "proof": [], "alias_binding": None, "reason": "No identity proof",
            } for p in payload["pairs"]}}
        else:
            result = {"judgments": {c["id"]: {
                "verdict": ("unresolved" if c["kind"] == "relation_groups" else
                            property_verdict if c["kind"] == "properties" else "accepted"),
                "confidence": 0.97, "evidence": source_ids, "reason": "Independent source review",
            } for c in payload["candidates"]}}
            if stage == "evidence_review":
                for candidate in payload["candidates"]:
                    if candidate["kind"] != "properties":
                        continue
                    bound = candidate["subject"]["identity_binding"]["identifiers"]
                    assert any(i["value"] == candidate["value"] for i in bound)
                    assert all(i["identity_status"] == "not_checked" for i in bound)
                result["type_concerns"] = []
        jsonschema.validate(result, schema)
        return result

    engine.invoke = respond
    engine.run()
    assert engine.state["cursor"]["main"]["scope_complete"]
    assert {p["value"] for p in engine.state["properties"].values()
            if p["state"] == property_verdict} == {"A1", "A2"}
    assert {p["state"] for p in engine.state["properties"].values()} == {property_verdict}
    assert all(e["state"] == "accepted" for e in engine.entities(window))
    for entity in engine.entities(window):
        for binding in entity["identity_binding"]["identifiers"]:
            assert binding["identity_status"] == "not_checked"
            assert binding["source_candidates"]
            assert all(c["identity_status"] == "not_checked"
                       and c["business_scope_status"] == "unspecified"
                       for c in binding["source_candidates"])
    assert all(g["state"] == "unresolved" for g in engine.state["relation_groups"].values())
    assert not engine.state.get("relations")


def test_group_projection_keeps_original_member_ids():
    entities = [{"id": key, "evidence": [], "state": "accepted", "class_iri": NS + "Object"}
                for key in ("s", "a", "b")]
    result = {"entities": entities, "properties": [], "relations": [], "observations": [],
              "relation_groups": [{"subject_id": "s", "object_ids": ["a", "b"]}]}
    project_coreferences(result, [], {})
    assert result["relation_groups"][0]["object_mention_ids"] == ["a", "b"]
