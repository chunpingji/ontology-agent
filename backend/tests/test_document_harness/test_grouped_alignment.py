"""Regression of identifier ownership and grouped relation semantics on the online engine."""

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
    stage_schema,
)
from app.services.document_harness.referents import (
    run_referent_alignment,
    validate_bound_property,
    validate_partitions,
)
from app.services.document_harness.source import build_windows
from app.services.document_harness.work import make_work
from app.services.document_harness.work_execution import drain_work


def proposal(spans=None):
    spans = spans or {text: text for text in ("A1/A2", "A1", "A2")}
    return {
        "new_spans": [],
        "expressions": [
            {"id": key, "property_iri": NS + "code", "span_id": spans[value]}
            for key, value in (("whole", "A1/A2"), ("a", "A1"), ("b", "A2"))
        ],
        "partitions": [
            {
                "id": "single",
                "members": [
                    {"id": "whole", "expression_ids": ["whole"]},
                ],
                "reason": "One compound identifier",
            },
            {
                "id": "members",
                "members": [{"id": key, "expression_ids": [key]} for key in ("a", "b")],
                "reason": "Two mentioned objects, regardless of use selection",
            },
        ],
    }


def engine_fixture(fixture, selected="members", invoke=None, save=None):
    ir, catalog, lookup, *_ = fixture
    card = catalog.classes[NS + "Object"]
    catalog = catalog.model_copy(
        update={
            "classes": {
                **catalog.classes,
                card.iri: card.model_copy(
                    update={
                        "properties": tuple(
                            p.model_copy(update={"identity_key": True})
                            if p.iri == NS + "code"
                            else p
                            for p in card.properties
                        )
                    }
                ),
            }
        }
    )
    window = build_windows(ir)[0]
    ref = window.resolve(ir, quote("A1/A2"))
    state = {
        "cursor": {
            "main": {
                "skeleton_window_id": window.id,
                "active_batches": {}, "phase": "semantic", "semantic_step": "referents", "planned_steps": [],
                "stage": "referent_alignment",
                "scope_complete": False,
            }
        },
        "windows": {
            window.id: {
                **Engine.window_row(window, [0]),
                "skeleton_step": "done",
                "reading_state": "complete",
            }
        },
        "entities": {
            "document": {
                "id": "document",
                "label": "Report",
                "role": "document_root",
                "class_iri": NS + "Report",
                "class_label": "Report",
                "state": "accepted",
                "reason": "selected",
                "field_ids": [],
                "evidence": [],
            },
            "old": {
                "id": "old",
                "label": "A1/A2",
                "referent": ref,
                "name": ref,
                "class_iri": NS + "Object",
                "class_label": "Object",
                "role": "object",
                "state": "candidate",
                "reason": "draft",
                "field_ids": [],
                "evidence": [ref],
                "window_id": window.id,
            },
        },
        "window_entities": {window.id: {"ids": ["old"]}},
        "fields": {},
    }
    calls = []

    def respond(stage, payload, schema):
        calls.append((stage, deepcopy(payload)))
        if invoke:
            return invoke(stage, payload, schema)
        if stage == "referent_candidates":
            available = {
                s["text"]: s["span_id"]
                for s in reversed(payload["evidence_spans"])
                if s["identifier_candidate"]
            }
            needed = [value for value in ("A1/A2", "A1", "A2") if value not in available]
            result = (
                {
                    "new_spans": [quote(value) for value in needed],
                    "expressions": [],
                    "partitions": [],
                }
                if needed
                else proposal(available)
            )
        else:
            assert stage == "referent_selection"
            result = {
                "verdict": "supported" if selected is not None else "unresolved",
                "selected_partition_id": selected,
                "confidence": 0.98,
                "evidence_span_ids": [
                    next(s["span_id"] for s in payload["evidence_spans"] if s["text"] == "A1/A2")
                ],
                "reason": "Reference, not use selection",
            }
        jsonschema.validate(result, schema)
        return result

    engine = Engine(
        ir=ir,
        catalog=catalog,
        state=state,
        invoke=respond,
        save=save or (lambda _: None),
        should_stop=lambda: False,
        lookup=lookup,
    )
    return engine, window, calls


@pytest.mark.parametrize(
    "selected,values",
    [
        ("members", ["A1", "A2"]),
        ("single", ["A1/A2"]),
        (None, []),
    ],
)
def test_online_refinement_queries_original_expressions_and_binds_members(
    lookup_fixture,
    selected,
    values,
):
    engine, window, calls = engine_fixture(lookup_fixture, selected)
    run_referent_alignment(engine, window)
    entities = [e for e in engine.entities(window) if e.get("identity_binding")]
    assert [e["label"] for e in entities] == values
    queries = [arg["queries"] for op, arg in lookup_fixture[3] if op == "query"][-1]
    assert [q["property_filters"][0]["value"] for q in queries] == ["A1/A2", "A1", "A2"]
    assert [r["outcome"] for r in calls[-1][1]["lookup_feedback"]["results"]] == [
        "no_match",
        "matches",
        "matches",
    ]
    assert {o["value"] for c in calls[0][1]["key_candidates"] for o in c["key_occurrences"]} == {
        "A1",
        "A2",
    }
    for entity in entities:
        binding = engine.entity_input(entity, window, typed=True)["identity_binding"]
        assert binding["identifiers"][0]["value"] == entity["label"]
        assert binding["identifiers"][0]["identity_status"] == "not_checked"
    assert not engine.state.get("relations")
    if selected is None:
        assert engine.state["entities"]["old"]["referent_unresolved"]
    else:
        assert engine.state["entities"]["old"]["reason"] == "refined_into_members"
        assert all(e["state"] == "candidate" for e in entities)


def test_identifier_partition_keeps_uncovered_typed_mention_explicitly_unresolved(
    lookup_fixture,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    ref = window.resolve(engine.ir, quote("trial"))
    engine.state["entities"]["extra"] = {
        "id": "extra",
        "label": "trial",
        "name": ref,
        "referent": ref,
        "class_iri": NS + "Object",
        "class_label": "Object",
        "role": "object",
        "state": "candidate",
        "reason": "draft",
        "field_ids": [],
        "evidence": [ref],
        "window_id": window.id,
    }
    engine.state["window_entities"][window.id]["ids"].append("extra")

    with pytest.raises(ValueError, match="partition_omits_group_mention"):
        run_referent_alignment(engine, window)
    assert engine.state["entities"]["extra"]["state"] == "candidate"
    assert not engine.state["entities"]["extra"].get("identity_binding")
    assert engine.state["entities"]["old"]["reason"] == "refined_into_members"
    assert {e["label"] for e in engine.entities(window) if e.get("identity_binding")} == {"A1", "A2"}
    assert engine.state["referent_work"]


def test_identifier_partition_disjoint_from_every_input_mention_creates_no_entity(
    lookup_fixture,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    ref = window.resolve(engine.ir, quote("trial"))
    engine.state["entities"]["old"].update(label="trial", name=ref, referent=ref, evidence=[ref])

    with pytest.raises(ValueError, match="partition_omits_group_mention"):
        run_referent_alignment(engine, window)
    assert set(engine.state["entities"]) == {"document", "old"}
    assert engine.state["entities"]["old"]["referent"] == ref
    assert not any(e.get("identity_binding") for e in engine.entities(window))


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
            engine.ir,
            [NS + "code"],
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
    mapping = PropertyMapping(
        predicate_iri=NS + "code", value_component="whole", value_quote=None, confidence=0.99
    , unit_quote=None)
    validate_bound_property(entity, field, mapping, {"value": "A1"})
    with pytest.raises(ValueError, match="property_conflicts"):
        validate_bound_property(entity, field, mapping, {"value": "A2"})


@pytest.mark.parametrize(
    "participation,timing,verdict,expected",
    [
        ("options", "unspecified", "accepted", "accepted"),
        ("unknown", "unspecified", "accepted", "unresolved"),
        ("all", "parallel", "accepted", "accepted"),
        ("all", "sequential", "unresolved", "unresolved"),
    ],
)
def test_group_review_preserves_options_without_binary_edges(
    lookup_fixture,
    participation,
    timing,
    verdict,
    expected,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    run_referent_alignment(engine, window)
    # Refinement now plans root searches as well. This test supplies its own
    # fixed group work below, independently of automatic candidate collection.
    automatic = [w for w in engine.state.get("work", {}).values()
                 if w["kind"] == "relation_alignment"]
    assert all(w["input"]["subject_id"] == "document" for w in automatic)
    engine.commit({"work": {w["id"]: None for w in automatic}})
    objects = engine.entities(window)
    for obj in objects:
        obj["state"] = "accepted"
    seed = {
        "subject_id": "document", "object_ids": [obj["id"] for obj in objects],
        "predicate_iri": NS + "uses", "region_id": window.id,
        "clue_refs": window.quotes(engine.ir, ["S1"]), "required_context_refs": [],
        "origin_ids": ["explicit-use"], "priority": "explicit", "polarity_hint": "positive",
        "condition_hints": [], "endpoint_hypothesis": False,
    }
    engine.state["cursor"]["main"].update(phase="semantic", semantic_step="assertions")
    work = make_work(
        "relation_alignment", seed, engine.state, engine.catalog, engine.execution_policy,
    )
    engine.commit({"work": {work["id"]: work}})
    calls = []

    def review(stage, payload, schema):
        calls.append(stage)
        if stage == "relation_alignment":
            result = {"proposals": {item["candidate_id"]: {
                "verdict": "proposed", "polarity": "positive", "conditions": [],
                "participation": participation, "timing": timing, "selection": "unspecified",
                "evidence": [source["source_id"] for source in payload["sources"]],
                "confidence": 0.95, "reason": "Candidate group", "missing_context": "none",
             "ordered_object_ids": item["object_ids"] if timing == "sequential" else None,
             "order_evidence": [quote("A1/A2")] if timing == "sequential" else []}
                for item in payload["items"]}}
            jsonschema.validate(result, schema)
            return result
        assert stage == "evidence_review"
        result = {
            "judgments": {
                c["id"]: {
                    "verdict": verdict,
                    "confidence": 0.96,
                    "evidence": [source["source_id"] for source in payload["sources"]],
                    "reason": "Source check",
                }
                for c in payload["candidates"]
            },
            "type_concerns": [],
        }
        jsonschema.validate(result, schema)
        return result

    engine.invoke = review
    for _ in range(4):
        if not (drain_work(engine, window, kind="relation_alignment")
                or drain_work(engine, window, kind="group_interpretation")
                or drain_work(engine, window, kind="evidence_review")):
            break
    else:
        pytest.fail("group work did not settle")
    group = next(iter(engine.state["relation_groups"].values()))
    assert group["object_ids"] == [obj["id"] for obj in objects]
    assert group["state"] == expected
    assert group["timing_state"] == (expected if timing != "unspecified" else "unresolved")
    assert calls == (["relation_alignment"] if participation == "unknown"
                     else ["relation_alignment", "evidence_review"])
    assert not engine.state.get("relations")


def test_relation_group_schema_does_not_allow_model_to_replace_fixed_endpoints():
    schema = stage_schema(
        "relation_alignment", source_ids=["S1"],
        relation_items=[{"candidate_id": "C1", "object_ids": ["E1", "E2"]}],
    )
    proposed = {
        "verdict": "proposed",
        "evidence": ["S1"],
        "polarity": "positive",
        "conditions": [],
        "participation": "unknown",
        "selection": "unspecified",
        "timing": "unspecified", "ordered_object_ids": None, "order_evidence": [],
        "missing_context": "participation",
        "reason": "Slash membership is unresolved",
        "confidence": 0.9,
    }
    jsonschema.validate({"proposals": {"C1": proposed}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"proposals": {"C1": {**proposed, "object_ids": ["E9"]}}}, schema)


def test_group_with_out_of_range_member_is_retained_without_losing_valid_group(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    run_referent_alignment(engine, window)
    # Isolate the two assertion-review jobs created explicitly by this test.
    automatic = [w for w in engine.state.get("work", {}).values()
                 if w["kind"] == "relation_alignment"]
    assert all(w["input"]["subject_id"] == "document" for w in automatic)
    engine.commit({"work": {w["id"]: None for w in automatic}})
    objects = engine.entities(window)
    for obj in objects:
        obj["state"] = "accepted"
    invalid = {**objects[0], "id": "out-of-range", "class_iri": NS + "Report",
               "identity_binding": None}
    engine.state["entities"][invalid["id"]] = invalid
    members = [obj["id"] for obj in objects]
    groups = {key: {
        "id": key, "subject_id": "document", "object_ids": object_ids,
        "predicate_iri": NS + "uses", "alignment_class_iri": NS + "Report", "label": "uses",
        "participation": "all", "selection": "unspecified", "timing": "unspecified", "ordered_object_ids": None, "order_evidence": [],
        "polarity": "positive", "conditions": [], "evidence": window.quotes(engine.ir, ["S1"]),
        "state": "candidate", "timing_state": "candidate", "reason": "Source group",
    } for key, object_ids in [("valid", members), ("invalid", [*members, invalid["id"]])]}
    engine.state["cursor"]["main"].update(phase="semantic", semantic_step="assertions")
    state = {**engine.state, "relation_groups": groups}
    works = [make_work("evidence_review", {"domain": "relation_groups", "assertion_id": key},
                       state, engine.catalog, engine.execution_policy) for key in groups]
    engine.commit({"relation_groups": groups, "work": {work["id"]: work for work in works}})
    calls = []

    def review(stage, payload, schema):
        calls.append(stage)
        assert stage == "evidence_review"
        assert len(payload["candidates"]) == 1
        assert len(payload["candidates"][0]["objects"]) == 2
        result = {"judgments": {payload["candidates"][0]["id"]: {
            "verdict": "accepted", "confidence": 0.99, "reason": "Whole legal group",
            "evidence": [source["source_id"] for source in payload["sources"]],
        }}, "type_concerns": []}
        jsonschema.validate(result, schema)
        return result

    engine.invoke = review
    for _ in range(4):
        if not (drain_work(engine, window, kind="relation_alignment")
                or drain_work(engine, window, kind="group_interpretation")
                or drain_work(engine, window, kind="evidence_review")):
            break
    else:
        pytest.fail("group work did not settle")
    assert calls == ["evidence_review"]
    assert engine.state["relation_groups"]["valid"]["state"] == "accepted"
    rejected = engine.state["relation_groups"]["invalid"]
    assert rejected["state"] == "unresolved" and rejected["reason"] == "ontology_incompatible"
    assert rejected["object_ids"] == [*members, invalid["id"]] and rejected["evidence"]
    assert not engine.state.get("relations")


@pytest.mark.parametrize("property_verdict", ["accepted", "unresolved", "rejected"])
def test_local_identifier_review_never_confirms_source_identity_or_overrides_rejection(
    lookup_fixture,
    property_verdict,
):
    engine, window, _ = engine_fixture(lookup_fixture)
    original = engine.invoke

    engine.state["hints"] = {
        "explicit-use": {
            "id": "explicit-use",
            "subject_id": "document",
            "object_id": "old",
            "label": "uses",
            "evidence": [engine.state["entities"]["old"]["referent"]],
            "polarity": "uncertain",
            "conditions": [],
            "window_id": window.id,
        }
    }

    def respond(stage, payload, schema):
        source_ids = [s["source_id"] for s in payload["sources"]]
        if stage in {"referent_candidates", "referent_selection"}:
            return original(stage, payload, schema)
        if stage == "property_alignment":
            result = {
                "properties": {
                    fid: {
                        "mappings": [
                            {
                                "predicate_iri": NS + "code",
                                "value_component": "whole",
                                "value_quote": None, "unit_quote": None,
                                "confidence": 0.97,
                            }
                        ],
                        "reason": "Identifier belongs to this member",
                    }
                    for fid in payload["property_field_ids"]
                },
            }
        elif stage == "relation_alignment":
            result = {
                "proposals": {
                    item["candidate_id"]: {
                        "verdict": "proposed",
                        "evidence": source_ids,
                        "polarity": "uncertain",
                        "conditions": [],
                        "participation": "unknown" if len(item["object_ids"]) > 1 else None,
                        "selection": "unspecified" if len(item["object_ids"]) > 1 else None,
                        "timing": "unspecified" if len(item["object_ids"]) > 1 else None, "ordered_object_ids": None, "order_evidence": [],
                        "missing_context": "participation",
                        "reason": "Slash leaves use open",
                        "confidence": 0.9,
                    }
                    for item in payload["items"]
                }
            }
        elif stage == "group_interpretation":
            result = {
                "verdict": "unresolved",
                "participation": "unknown",
                "selection": "unspecified",
                "timing": "unspecified", "ordered_object_ids": None, "order_evidence": [],
                "evidence": source_ids,
                "reason": "Slash leaves use open",
            }
        elif stage == "coreference_review":
            result = {
                "judgments": {
                    p["pair_id"]: {
                        "verdict": "unresolved",
                        "basis": "insufficient",
                        "confidence": 0.95,
                        "evidence": [],
                        "proof": [],
                        "alias_binding": None,
                        "reason": "No identity proof",
                    }
                    for p in payload["pairs"]
                }
            }
        else:
            result = {
                "judgments": {
                    c["id"]: {
                        "verdict": (
                            "unresolved"
                            if c["kind"] == "relation_groups"
                            else property_verdict
                            if c["kind"] == "properties"
                            else "accepted"
                        ),
                        "confidence": 0.97,
                        "evidence": source_ids,
                        "reason": "Independent source review",
                    }
                    for c in payload["candidates"]
                }
            }
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
    assert {
        p["value"] for p in engine.state["properties"].values() if p["state"] == property_verdict
    } == {"A1", "A2"}
    assert {p["state"] for p in engine.state["properties"].values()} == {property_verdict}
    assert all(e["state"] == "accepted" for e in engine.entities(window))
    for entity in engine.entities(window):
        for binding in entity["identity_binding"]["identifiers"]:
            assert binding["identity_status"] == "not_checked"
            assert binding["source_candidates"]
            assert all(
                c["identity_status"] == "not_checked"
                and c["business_scope_status"] == "unspecified"
                for c in binding["source_candidates"]
            )
    assert engine.state["relation_groups"]
    assert all(g["state"] == "unresolved" for g in engine.state["relation_groups"].values())
    assert not engine.state.get("relations")


def test_group_projection_keeps_original_member_ids():
    entities = [
        {"id": key, "evidence": [], "state": "accepted", "class_iri": NS + "Object"}
        for key in ("s", "a", "b")
    ]
    result = {
        "entities": entities,
        "properties": [],
        "relations": [],
        "observations": [],
        "relation_groups": [{"subject_id": "s", "object_ids": ["a", "b"]}],
    }
    project_coreferences(result, [], {})
    assert result["relation_groups"][0]["object_mention_ids"] == ["a", "b"]
