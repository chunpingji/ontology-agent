"""Batching avoids per-mention calls without sharing judgments or source bindings."""

from copy import deepcopy

import jsonschema
import pytest
from test_controller_budget import engine, entity
from test_controller_budget import records as records
from test_work_evidence import add_alignment, create_engine, proposal, seed
from test_work_evidence import evidence_case as evidence_case
from test_work_recovery import MemoryRepository

from app.services.document_harness.controller import Engine, Paused
from app.services.document_harness.work import make_work
from app.services.document_harness.work_execution import drain_work


def type_answer(payload, selected):
    return {"entities": {row["entity_id"]: {
        "class_iri": selected, "confidence": 0.95, "evidence": row["evidence"],
        "reason": "independent source judgment",
    } for row in payload["entities"]}}


def test_entity_halves_keep_complete_type_menu_without_per_entity_comparisons(records, monkeypatch):
    ir, catalog, units, _ = records
    candidates = [entity(ir, str(i), units[text]) for i, text in enumerate(
        ("Name: Alpha", "Name: Beta", "Object: B0", "Object: B1"))]
    calls = []

    def invoke(stage, payload, schema):
        calls.append(deepcopy(payload))
        assert {c["iri"] for c in payload["classes"]} == set(catalog.reachable_class_iris)
        answer = type_answer(payload, "urn:budget:A")
        jsonschema.validate(answer, schema)
        return answer

    monkeypatch.setattr("app.services.document_harness.controller.request_size",
                        lambda _, p, s: 100 * (len(p["classes"]) + len(p["entities"])))
    runner, window = engine(records, ["Current reading"], candidates, invoke, budget=550)
    runner.type_alignment(window)
    assert len(calls) == 2
    assert [len(p["entities"]) for p in calls] == [2, 2]
    for candidate in candidates:
        saved = runner.state["entities"][candidate["id"]]
        assert saved["state"] == "candidate"
        assert saved["type_evidence"] == candidate["evidence"]


def test_large_menu_shards_share_comparison_but_keep_independent_evidence(records, monkeypatch):
    ir, _, units, _ = records
    candidates = [entity(ir, str(i), units[text]) for i, text in enumerate(
        ("Name: Alpha", "Name: Beta", "Object: B0", "Object: B1"))]
    calls = []
    compared = {"urn:budget:A", "urn:budget:Report"}

    def invoke(stage, payload, schema):
        calls.append(deepcopy(payload))
        menu = {c["iri"] for c in payload["classes"]}
        selected = "urn:budget:A" if menu == compared else (
            "urn:budget:Report" if "urn:budget:Report" in menu else "urn:budget:A")
        answer = type_answer(payload, selected)
        jsonschema.validate(answer, schema)
        return answer

    monkeypatch.setattr("app.services.document_harness.controller.request_size",
                        lambda _, p, s: 100 * len(p["classes"]))
    runner, window = engine(records, ["Current reading"], candidates, invoke, budget=250)
    runner.type_alignment(window)
    assert len(calls) == 3  # Two catalog shards, one joint comparison for four mentions.
    assert {c["iri"] for c in calls[-1]["classes"]} == compared
    assert len(calls[-1]["entities"]) == 4
    for candidate in candidates:
        saved = runner.state["entities"][candidate["id"]]
        assert saved["class_iri"] == "urn:budget:A"
        assert saved["type_evidence"] == candidate["evidence"]


def test_comparison_batches_split_entities_without_splitting_competing_menu(records, monkeypatch):
    ir, _, units, _ = records
    candidates = [entity(ir, str(i), units[text]) for i, text in enumerate(
        ("Name: Alpha", "Name: Beta", "Object: B0", "Object: B1"))]
    compared = {"urn:budget:A", "urn:budget:Report"}
    calls = []

    def size(_, payload, schema):
        menu = {c["iri"] for c in payload["classes"]}
        if len(menu) == 3:
            return 500
        return 100 * len(payload["entities"]) if menu == compared else 100

    def invoke(stage, payload, schema):
        menu = {c["iri"] for c in payload["classes"]}
        calls.append(deepcopy(payload))
        return type_answer(payload, "urn:budget:Report" if "urn:budget:Report" in menu
                           else "urn:budget:A")

    monkeypatch.setattr("app.services.document_harness.controller.request_size", size)
    runner, window = engine(records, ["Current reading"], candidates, invoke, budget=250)
    runner.type_alignment(window)
    comparisons = [p for p in calls if {c["iri"] for c in p["classes"]} == compared]
    assert len(comparisons) == 2
    assert [len(p["entities"]) for p in comparisons] == [2, 2]


def test_section_relation_batch_keeps_per_item_direction_polarity_and_conditions(evidence_case):
    calls = []

    def invoke(stage, payload, schema):
        assert stage == "relation_alignment"
        calls.append(deepcopy(payload))
        answer = proposal(payload)
        for item in payload["items"]:
            answer["proposals"][item["candidate_id"]].update(
                polarity=item["polarity_hint"], conditions=item["condition_hints"],
                evidence=item["clue_sources"],
            )
        jsonschema.validate(answer, schema)
        return answer

    runner = create_engine(evidence_case, invoke)
    runner.state["cursor"]["main"].update(phase="skeleton", semantic_step=None)
    inputs = [
        seed(runner),
        {**seed(runner), "subject_id": "document", "object_ids": ["a"],
         "predicate_iri": "urn:gate:describes", "region_id": "another-row",
         "polarity_hint": "negative", "condition_hints": ["under C"]},
        {**seed(runner), "subject_id": "b", "object_ids": ["c"], "region_id": "third-row"},
    ]
    keys = [add_alignment(runner, data) for data in inputs]
    assert drain_work(runner, None, kind="relation_alignment")
    assert not drain_work(runner, None, kind="relation_alignment")
    assert len(calls) == 1 and len(calls[0]["items"]) == 3
    assert len(calls[0]["predicates"]) == 2
    assert all(runner.state["work"][key]["status"] == "done" for key in keys)
    actual = {(r["subject_id"], r["predicate_iri"], r["object_id"], r["polarity"],
               tuple(r["conditions"])) for r in runner.state["relations"].values()}
    expected = {(r["subject_id"], r["predicate_iri"], r["object_ids"][0], r["polarity_hint"],
                 tuple(r["condition_hints"])) for r in inputs}
    assert actual == expected
    assert all(r["state"] == "candidate" for r in runner.state["relations"].values())


def test_saved_single_relation_batch_is_applied_before_packing_new_ready_work(evidence_case):
    original = create_engine(evidence_case, lambda *_: None)
    original.state["cursor"]["main"].update(phase="skeleton", semantic_step=None)
    first = add_alignment(original, seed(original))
    repo = MemoryRepository(original.state, lambda _, p, s: proposal(p),
                            pause_after="relation_alignment")

    def restore():
        return Engine(ir=original.ir, catalog=original.catalog, state=repo.state,
                      invoke=lambda *_: pytest.fail("prepared path bypassed"), save=repo.save,
                      should_stop=lambda: False, prepare_batch=repo.prepare,
                      invoke_prepared=repo.invoke, batch_request=repo.request)

    with pytest.raises(Paused):
        drain_work(restore(), None, kind="relation_alignment")
    saved_request = deepcopy(repo.requests)
    second = make_work("relation_alignment", seed(original, objects=["c"]), repo.state,
                       original.catalog, original.execution_policy, phase="skeleton")
    repo.save({"work": {second["id"]: second}})
    runner = restore()
    assert drain_work(runner, None, kind="relation_alignment")
    assert len(repo.paid) == 1 and repo.requests == saved_request
    assert runner.state["work"][first]["status"] == "done"
    assert runner.state["work"][second["id"]]["status"] == "ready"
    assert drain_work(runner, None, kind="relation_alignment")
    assert len(repo.paid) == 2
    assert runner.state["work"][second["id"]]["status"] == "done"
