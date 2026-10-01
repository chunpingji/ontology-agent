"""Explicit source-supported decisions replace an uncalibrated numeric veto."""

from copy import deepcopy
from dataclasses import replace

import jsonschema
import pytest
from pydantic import ValidationError
from test_grouped_alignment import engine_fixture
from test_lookup import lookup_fixture as lookup_fixture

from app.services.document_harness.controller import Engine, Paused
from app.services.document_harness.protocols import ReferentSelection, stage_schema
from app.services.document_harness.referents import run_referent_alignment


def intercept_selection(engine, transform):
    original = engine.invoke

    def invoke(stage, payload, schema):
        answer = original(stage, payload, schema)
        if stage == "referent_selection":
            transform(answer, payload)
        return answer

    engine.invoke = invoke


@pytest.mark.parametrize("score", [0.0, 0.77, 0.84, 0.85, 1.0])
@pytest.mark.parametrize("selected", ["members", None])
def test_score_cannot_override_semantic_verdict(lookup_fixture, score, selected):
    engine, window, _ = engine_fixture(lookup_fixture, selected=selected)
    intercept_selection(engine, lambda answer, _: answer.update(confidence=score))
    run_referent_alignment(engine, window)
    work = next(iter(engine.state["referent_work"].values()))
    assert work["selection"]["confidence"] == score
    assert work["selected_partition_id"] == selected
    bound = [e for e in engine.entities(window) if e.get("identity_binding")]
    if selected:
        assert work["selection_issue"] is None
        assert {e["label"] for e in bound} == {"A1", "A2"}
        assert all(e["state"] == "candidate" for e in bound)
        assert all(b["identity_status"] == "not_checked" for e in bound
                   for b in e["identity_binding"]["identifiers"])
    else:
        assert work["selection_issue"] == "semantic_unresolved"
        assert not bound
        assert engine.state["entities"]["old"]["state"] == "unresolved"


@pytest.mark.parametrize("score", [0.77, 1.0])
def test_supported_decision_with_incomplete_evidence_stays_unresolved(lookup_fixture, score):
    engine, window, _ = engine_fixture(lookup_fixture)

    def omit_member(answer, payload):
        answer.update(confidence=score, evidence_span_ids=[next(
            s["span_id"] for s in payload["evidence_spans"] if s["text"] == "A1"
        )])

    intercept_selection(engine, omit_member)
    run_referent_alignment(engine, window)
    work = next(iter(engine.state["referent_work"].values()))
    assert work["selection"]["verdict"] == "supported"
    assert work["selected_partition_id"] is None
    assert work["selection_issue"] == "selection_evidence_does_not_cover_members"
    assert list(engine.state["entities"]) == ["document", "old"]
    assert "未覆盖全部成员" in engine.state["entities"]["old"]["reason"]
    assert all("未覆盖全部成员" in o["reason"]
               for o in engine.state["observations"].values())


@pytest.mark.parametrize("changes,error", [
    ({"selected_partition_id": None}, "supported_partition_requires_selection_and_evidence"),
    ({"evidence_span_ids": []}, "supported_partition_requires_selection_and_evidence"),
    ({"verdict": "unresolved"}, "unresolved_partition_cannot_be_selected"),
])
def test_incoherent_decision_cannot_register_members(lookup_fixture, changes, error):
    engine, window, _ = engine_fixture(lookup_fixture)
    intercept_selection(engine, lambda answer, _: answer.update(changes))
    with pytest.raises(ValidationError, match=error):
        run_referent_alignment(engine, window)
    assert list(engine.state["entities"]) == ["document", "old"]
    assert not engine.state["fields"]


def test_missing_verdict_is_not_inferred_from_a_high_score_or_partition_id():
    answer = {"selected_partition_id": "P1", "evidence_span_ids": ["span"],
              "confidence": 1.0, "reason": "A score cannot substitute for a semantic verdict"}
    schema = stage_schema("referent_selection", partition_ids=["P1"], span_ids=["span"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(answer, schema)
    with pytest.raises(ValidationError):
        ReferentSelection.model_validate(answer)
    answer["verdict"] = "supported"
    jsonschema.validate(answer, schema)
    ReferentSelection.model_validate(answer)


def test_high_score_cannot_select_an_unknown_partition(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    intercept_selection(engine, lambda answer, _: answer.update(
        confidence=1.0, selected_partition_id="not-provided",
    ))
    with pytest.raises(ValueError, match="unknown_selected_partition"):
        run_referent_alignment(engine, window)
    assert "old" in engine.state["entities"]


def test_resumed_low_score_supported_decision_does_not_call_or_register_again(lookup_fixture):
    engine, window, calls = engine_fixture(lookup_fixture)
    intercept_selection(engine, lambda answer, _: answer.update(confidence=0.77))
    run_referent_alignment(engine, window)
    count, saved = len(calls), deepcopy(engine.state)
    restored = Engine(ir=engine.ir, catalog=engine.catalog, state=saved, invoke=engine.invoke,
                      save=lambda _: None, should_stop=lambda: False, lookup=engine.lookup)
    run_referent_alignment(restored, window)
    assert len(calls) == count
    assert len(restored.entities(window)) == 2
    assert next(iter(saved["referent_work"].values()))["selection"]["confidence"] == 0.77


@pytest.mark.parametrize("selected,member_count", [("members", 2), ("single", 1)])
def test_refinement_rebinds_collective_object_hints_without_flattening(
    lookup_fixture, selected, member_count,
):
    engine, window, _ = engine_fixture(lookup_fixture, selected=selected)
    evidence = engine.state["entities"]["old"]["evidence"]
    hint = {"id": "h", "subject_id": "document", "object_id": "old", "label": "uses",
            "evidence": evidence, "window_id": window.id, "polarity": "negative",
            "conditions": ["Only when X"]}
    engine.state["hints"] = {"h": deepcopy(hint)}
    engine.state["reference_cues"] = {"c": {
        "id": "c", "subject_id": "document", "target_ids": ["old"],
        "target_expression": evidence[0], "evidence": evidence,
        "polarity": "uncertain", "conditions": ["If approved"],
    }}
    run_referent_alignment(engine, window)
    members = next(iter(engine.state["referent_work"].values()))["member_ids"]
    assert len(members) == member_count
    retained = engine.state["hints"]["h"]
    assert retained["object_ids"] == members
    assert "object_id" not in retained
    assert retained["evidence"] == hint["evidence"]
    assert retained["polarity"] == "negative" and retained["conditions"] == ["Only when X"]
    assert engine.state["reference_cues"]["c"]["target_ids"] == members
    assert engine.state["reference_cues"]["c"]["conditions"] == ["If approved"]
    assert not engine.state.get("relations")


def test_refinement_retains_ambiguous_subjects_and_waiting_observation(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    evidence = engine.state["entities"]["old"]["evidence"]
    engine.state["hints"] = {"h": {
        "id": "h", "subject_id": "old", "object_id": "document", "label": "mentioned in",
        "evidence": evidence, "window_id": window.id, "polarity": "positive", "conditions": [],
    }}
    engine.state["reference_cues"] = {"c": {
        "id": "c", "subject_id": "old", "target_ids": ["document"],
        "target_expression": evidence[0], "evidence": evidence,
    }}
    run_referent_alignment(engine, window)
    members = next(iter(engine.state["referent_work"].values()))["member_ids"]
    for domain, key in [("hints", "h"), ("reference_cues", "c")]:
        row = engine.state[domain][key]
        assert row["subject_id"] is None
        assert row["ambiguous_subject_members"] == members
        assert row["status"] == "waiting"
        assert row["evidence"] == evidence
    observation = next(row for row in engine.state["observations"].values()
                       if row.get("reason_code") == "ambiguous_subject_members")
    assert observation["state"] == "unresolved"
    assert observation["candidate_subject_ids"] == members
    assert not engine.state.get("relations")


def test_referent_task_identity_does_not_depend_on_window_packaging(lookup_fixture):
    keys = []
    for renamed in (False, True):
        engine, window, _ = engine_fixture(lookup_fixture)
        if renamed:
            original_id = window.id
            window = replace(window, id="different-reading-package")
            engine.windows = [window]
            engine.state["entities"]["old"]["window_id"] = window.id
            engine.state["window_entities"][window.id] = engine.state["window_entities"].pop(
                original_id,
            )
            engine.state["windows"] = {window.id: {
                **Engine.window_row(window, [0]), "phase": "referent_alignment",
            }}
            engine.state["cursor"]["main"]["active_window_id"] = window.id

        def pause(*args):
            raise Paused()

        engine.invoke = pause
        with pytest.raises(Paused):
            run_referent_alignment(engine, window)
        keys.append(next(iter(engine.state["referent_work"])))
    assert keys[0] == keys[1]


def test_every_referent_call_pins_physical_window_and_current_work(lookup_fixture):
    engine, window, calls = engine_fixture(lookup_fixture)
    original = engine.invoke

    def invoke(stage, payload, schema):
        assert engine._call_window is not None
        assert engine._call_window.payload()["sources"] == payload["sources"]
        assert len(engine._call_targets) == 1
        target = engine._call_targets[0]
        assert target["domain"] == "referent_work"
        assert target["id"] in engine.state["referent_work"]
        assert target["dependency_hash"]
        return original(stage, payload, schema)

    engine.invoke = invoke
    run_referent_alignment(engine, window)
    assert {stage for stage, _ in calls} == {"referent_candidates", "referent_selection"}
