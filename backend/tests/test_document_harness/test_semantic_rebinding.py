"""Refinement changes only grounded ownership and affected current claims."""

from copy import deepcopy

import pytest
from test_grouped_alignment import engine_fixture
from test_lookup import lookup_fixture as lookup_fixture

from app.services.document_harness.referents import run_referent_alignment
from app.services.document_harness.work import make_work
from app.services.document_harness.work_execution import materialize_skeleton


@pytest.mark.parametrize("selection,members", [("members", 2), ("single", 1)])
def test_refinement_keeps_claim_ids_and_never_copies_collective_properties(
    lookup_fixture,
    selection,
    members,
):
    engine, window, _ = engine_fixture(lookup_fixture, selected=selection)
    ref = engine.state["entities"]["old"]["referent"]
    field = {
        "id": "collective",
        "label": "共同值",
        "value": ref["text"],
        "missing": False,
        "evidence": [ref],
        "value_evidence": [ref],
    }
    engine.state["fields"][field["id"]] = field
    engine.state["entities"]["old"]["field_ids"] = [field["id"]]
    engine.state["hints"] = {
        "use": {
            "id": "use",
            "subject_id": "document",
            "object_id": "old",
            "label": "uses",
            "evidence": [ref],
            "polarity": "positive",
            "conditions": [],
        }
    }
    materialize_skeleton(engine)
    prop = next(iter(engine.state["properties"].values()))
    relation = next(iter(engine.state["relations"].values()))
    proof = {"method": "llm", "semantic_verdict": "accepted", "dependency_hash": "old"}
    engine.state["properties"][prop["id"]].update(verification=proof, calibration={"old": True})
    engine.state["relations"][relation["id"]].update(verification=proof, calibration={"old": True})
    engine.state["relations"]["unrelated"] = {
        **relation,
        "id": "unrelated",
        "subject_id": "document",
        "object_id": "document",
        "verification": deepcopy(proof),
        "calibration": {"untouched": True},
    }
    untouched = deepcopy(engine.state["relations"]["unrelated"])
    run_referent_alignment(engine, window)
    parent = engine.state["entities"]["old"]
    assert parent["reason"] == "refined_into_members"
    assert len(parent["refined_member_ids"]) == members
    assert sum(p["field_id"] == "collective" for p in engine.state["properties"].values()) == 1
    current = engine.state["properties"][prop["id"]]
    assert current["verification"] is None and current["calibration"] is None
    if members == 2:
        assert current["subject_id"] == "old" and current["state"] == "unresolved"
        assert current["reason"] == "ambiguous_subject_members"
        group = engine.state["relation_groups"][relation["id"]]
        assert set(group["object_ids"]) == set(parent["refined_member_ids"])
        assert group["participation"] == "unknown"
    else:
        assert current["subject_id"] == parent["refined_member_ids"][0]
        assert engine.state["relations"][relation["id"]]["object_id"] == current["subject_id"]
    assert engine.state["relations"]["unrelated"] == untouched


def test_new_calibration_dependency_never_resets_supported_semantics(lookup_fixture):
    engine, _, _ = engine_fixture(lookup_fixture)
    entity = engine.state["entities"]["old"]
    entity.update(state="accepted", calibration={"old": True})
    work = make_work(
        "entity_calibration",
        {"entity_id": "old"},
        engine.state,
        engine.catalog,
        engine.execution_policy,
    )
    work.update(status="done", output_ids=["old"])
    engine.commit({"work": {work["id"]: work}})
    engine.commit({"entities": {"old": {**entity, "label": "updated source label"}}})
    assert engine.state["entities"]["old"]["state"] == "accepted"
    assert engine.state["entities"]["old"]["calibration"] is None
    assert engine.state["work"][work["id"]]["status"] == "ready"


def test_late_refinement_registers_member_reviews_even_when_phase_plans_exist(lookup_fixture):
    engine, window, _ = engine_fixture(lookup_fixture)
    engine.state["cursor"]["main"]["planned_steps"] = ["semantic:entities", "deterministic"]
    run_referent_alignment(engine, window)
    members = set(engine.state["entities"]["old"]["refined_member_ids"])
    for kind in ("entity_review", "entity_calibration"):
        assert {w["input"]["entity_id"] for w in engine.state["work"].values()
                if w["kind"] == kind and w["status"] == "ready"} == members
    assert engine.state["cursor"]["main"]["planned_steps"] == [
        "semantic:entities", "deterministic",
    ]
