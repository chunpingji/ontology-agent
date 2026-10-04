"""Public behavior of the new order, isolated failures and restart boundaries."""

from copy import deepcopy

import pytest
from test_controller import Model, execute
from test_controller import inputs as inputs

from app.services.document_harness.controller import Engine
from app.services.document_harness.projection import build_graph_base


@pytest.mark.parametrize("omit_judgment", [False, True])
def test_runtime_keeps_paid_json_and_finishes_with_current_item_failure_status(
    db,
    monkeypatch,
    inputs,
    omit_judgment,
):
    from app.services.document_harness import model
    from app.services.document_harness.projection import graph_response
    from app.services.document_harness.runtime import Repository, execute_claimed
    from tests.test_extraction.test_document_harness_runtime import _run

    ir, catalog = inputs
    run, token = _run(
        db, document_hash=ir.original_document_hash, catalog=catalog.model_dump(mode="json")
    )
    run.root_class_iri = catalog.root_class_iri
    db.commit()
    repo = Repository(db, run, token)
    repo.save({"input": {"document": ir.model_dump(mode="json")}})
    responder = Model()
    omitted = False

    def call(stage, payload, schema, policy):
        nonlocal omitted
        output = responder(stage, payload, schema)
        if omit_judgment and stage == "evidence_review" and not omitted:
            output["judgments"].pop(next(iter(output["judgments"])))
            omitted = True
        return {
            "output": output,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "seconds": 0.01,
            "raw_response": {},
            "error": None,
        }

    monkeypatch.setattr(model, "call_model", call)
    execute_claimed(db, run, token)
    db.refresh(run)
    graph = graph_response(db, run)
    assert run.execution_status == ("failed" if omit_judgment else "finished"), run.error
    assert graph["progress"]["phase"] == "done"
    assert graph["properties"] and graph["relations"]
    assert graph["progress"]["completed_calls"] == len(responder.calls)
    assert all(cost["phase"] != "deterministic" for cost in graph["progress"]["stage_costs"])
    if omit_judgment:
        assert run.error["code"] == "HARNESS_ITEMS_FAILED" and not run.error["retryable"]
        failed = [w for w in repo.load()["work"].values() if w["status"] == "failed"]
        assert failed and all(not w["retryable"] for w in failed)
    else:
        assert run.error is None
    current = deepcopy(repo.load())
    calls = len(responder.calls)
    assert graph_response(db, run) == graph
    assert repo.load() == current and len(responder.calls) == calls


def test_skeleton_precedes_all_semantic_calls_and_deterministic_is_model_free(inputs):
    model = Model()
    state, snapshots = execute(inputs, model)
    phases = [payload["execution_phase"] for _, payload, _ in model.calls]
    assert phases == sorted(phases, key=("discovery", "skeleton", "semantic").index)
    semantic = {}
    for delta in snapshots:
        for domain, rows in delta.items():
            semantic.setdefault(domain, {}).update(deepcopy(rows))
        if semantic["cursor"]["main"]["phase"] == "semantic":
            break
    assert semantic["properties"] and semantic["relations"]
    assert all(
        row["state"] == "candidate"
        for domain in ("properties", "relations")
        for row in semantic[domain].values()
    )
    assert all(not row.get("verification") for row in semantic["properties"].values())
    assert all(
        stage
        not in {"entity_review", "referent_candidates", "coreference_review", "evidence_review"}
        for stage, payload, _ in model.calls
        if payload["execution_phase"] != "semantic"
    )
    assert all(
        row.get("calibration")
        for domain in ("entities", "properties", "relations")
        for row in state[domain].values()
    )
    assert "deterministic" not in phases
    assert [row["cursor"]["main"]["phase"] for row in snapshots][-1] == "done"


def test_missing_types_preserve_null_drafts_without_false_acceptance(inputs):
    base = Model()

    def invoke(stage, payload, schema):
        result = base(stage, payload, schema)
        if stage == "type_alignment":
            for choice in result["entities"].values():
                choice.update(class_iri=None, reason="原文类型未决")
        return result

    state, _ = execute(inputs, invoke)
    assert {row["source_value"] for row in state["properties"].values()} >= {"red", "Alpha"}
    assert all(
        row["predicate_iri"] is None and row["state"] != "accepted"
        for row in state["properties"].values()
    )
    graph = build_graph_base(state, inputs[1].model_dump(mode="json"))
    assert graph["properties"] and graph["relations"]
    assert not any(row["state"] == "accepted" for row in graph["relations"])


def test_local_partition_omission_does_not_block_unrelated_property(inputs):
    ir, catalog = inputs
    card = catalog.classes["urn:test:Thing"]
    catalog = catalog.model_copy(
        update={
            "classes": {
                **catalog.classes,
                card.iri: card.model_copy(
                    update={
                        "properties": tuple(
                            p.model_copy(update={"identity_key": True}) for p in card.properties
                        )
                    }
                ),
            }
        }
    )
    base = Model()
    correction_calls = []

    def invoke(stage, payload, schema):
        if stage == "referent_candidates":
            if payload["mentions"][0]["label"] != "Alpha":
                return {"new_spans": [], "expressions": [], "partitions": []}
            correction_calls.append(deepcopy(payload))
            spans = payload["evidence_spans"]
            selected = [s for s in spans if s["identifier_candidate"]]
            if len(selected) == 1:
                return {
                    "new_spans": [
                        {"source_id": selected[0]["source_id"], "text": "Al", "occurrence": None},
                        {"source_id": selected[0]["source_id"], "text": "pha", "occurrence": None},
                    ],
                    "expressions": [],
                    "partitions": [],
                }
            return {
                "new_spans": [],
                "expressions": [
                    {"id": str(i), "property_iri": "urn:test:color", "span_id": span["span_id"]}
                    for i, span in enumerate(selected)
                ],
                "partitions": [
                    {
                        "id": "omitted",
                        "members": [{"id": "a", "expression_ids": ["2"]}],
                        "reason": "遗漏其它输入表达",
                    }
                ],
            }
        result = base(stage, payload, schema)
        if stage == "discover":
            color = next(f["field_id"] for f in payload["fields"] if f["label"] == "颜色")
            result["entities"][1]["field_ids"].append(color)
        return result

    state, _ = execute((ir, catalog), invoke)
    failed = [row for row in state["work"].values() if row["status"] == "failed"]
    assert len(failed) == 1 and not failed[0]["retryable"]
    assert failed[0]["reason_code"] == "partition_omits_group_mention"
    assert len(correction_calls) == 3
    assert correction_calls[2]["proposal_feedback"]["issue"] == "partition_omits_group_mention"
    assert any(
        p["value"] == "red" and p["state"] == "accepted" for p in state["properties"].values()
    )
    assert state["relations"]
    assert any(p["reason"] == "referent_alignment_failed" for p in state["properties"].values())
    assert state["cursor"]["main"]["phase"] == "done"
    calls = len(base.calls)
    restored = Engine(
        ir=ir,
        catalog=catalog,
        state=state,
        invoke=invoke,
        save=lambda _: None,
        should_stop=lambda: False,
    )
    restored.run()
    assert len(base.calls) == calls and len(correction_calls) == 3


@pytest.mark.parametrize("pause_phase", ["skeleton", "semantic", "deterministic"])
def test_phase_pause_preserves_plans_and_does_not_repeat_paid_calls(inputs, pause_phase):
    ir, catalog = inputs
    model = Model()
    stopped = False
    latest = {}

    def save(changes):
        nonlocal stopped
        for domain, rows in changes.items():
            latest.setdefault(domain, {}).update(deepcopy(rows))
        stopped = stopped or latest.get("cursor", {}).get("main", {}).get("phase") == pause_phase

    engine = Engine(
        ir=ir, catalog=catalog, state={}, invoke=model, save=save, should_stop=lambda: stopped
    )
    engine.run()
    before = [(stage, repr(payload)) for stage, payload, _ in model.calls]
    restored = Engine(
        ir=ir,
        catalog=catalog,
        state=engine.state,
        invoke=model,
        save=lambda _: None,
        should_stop=lambda: False,
        calls=engine._memory_calls,
    )
    restored.run()
    after = [(stage, repr(payload)) for stage, payload, _ in model.calls]
    assert len(after) == len(set(after))
    assert after[: len(before)] == before
    assert restored.state["cursor"]["main"]["phase"] == "done"
