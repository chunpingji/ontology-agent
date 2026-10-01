"""Window-local inputs and order-independent discovery publication."""

from copy import deepcopy
from threading import Barrier, Event, Lock, get_ident

import pytest
from docx import Document
from test_controller import Model
from test_controller import inputs as inputs

from app.services.document_harness.reading import decode_local_discovery, merge_local_discovery
from app.services.document_harness.source import Window, build_windows


def scheduled_case(tmp_path, inputs, model, *, calls=None, state=None, stop=lambda: False,
                   concurrency=2):
    from app.services.document_harness.controller import Engine
    from app.services.document_harness.source import make_window
    from app.services.extraction.document_ir import build_document_ir
    from app.services.extraction.docx_structure import parse_docx_structure

    path = tmp_path / "parallel.docx"
    if not path.exists():
        doc = Document()
        for text in ["slow", "fast", "third"]:
            doc.add_paragraph(text)
        doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path))
    saved, coordinator = [], get_ident()

    def save(changes):
        assert get_ident() == coordinator
        saved.append(deepcopy(changes))

    engine = Engine(ir=ir, catalog=inputs[1], state=state or {}, invoke=model, calls=calls,
                    save=save, should_stop=lambda: stop() or (
                        engine.state.get("cursor", {}).get("main", {}).get("phase") == "entities"
                    ), policy={"execution_policy": {"reading_concurrency": concurrency}},
                    rank=lambda *_: {"snapshot_id": inputs[1].snapshot_id, "selected_iris": []})
    if not state:
        engine.windows = [make_window(ir, [(u.evidence_id, 0, len(u.text))],
                                             [u.evidence_id]) for u in ir.evidence_units]
    return engine, saved


def empty_discovery():
    return {"entities": [], "document_field_ids": [], "document_source_fields": [],
            "unowned_fields": [], "relation_hints": [], "reference_cues": [], "complete": True}


def test_fast_window_opens_next_slot_without_waiting_for_slow_window(tmp_path, inputs):
    third, lock = Event(), Lock()
    live, peak, order = 0, 0, []

    def model(stage, payload, schema):
        nonlocal live, peak
        assert stage == "discover" and "known_mentions" not in payload
        name = payload["sources"][0]["text"]
        with lock:
            live += 1
            peak = max(peak, live)
            order.append(name)
        if name == "slow":
            assert third.wait(3), "third window must start while slow window is in flight"
        if name == "third":
            third.set()
        with lock:
            live -= 1
        return empty_discovery()

    engine, saved = scheduled_case(tmp_path, inputs, model)
    engine.run()
    assert peak == 2 and order == ["slow", "fast", "third"]
    assert engine.state["cursor"]["main"]["reading_windows"] == {
        "total": 3, "saved": 3, "complete": 3, "incomplete": 0, "active": 0,
    }
    assert not engine.state.get("work")
    assert any(c.get("cursor", {}).get("main", {}).get("reading_windows", {}).get("active") == 2
               for c in saved)


def test_pause_collects_both_paid_answers_then_continue_applies_without_recalling(tmp_path, inputs):
    from app.services.document_harness.calls import MemoryCalls

    gate, pause, names = Barrier(2), Event(), []

    def model(stage, payload, schema):
        name = payload["sources"][0]["text"]
        names.append(name)
        if len(names) <= 2:
            gate.wait(timeout=3)
            pause.set()
        return empty_discovery()

    calls = MemoryCalls(model)
    engine, _ = scheduled_case(tmp_path, inputs, model, calls=calls, stop=pause.is_set)
    engine.run()
    assert len(calls.answers) == 2 and len(names) == 2
    assert len(engine.state["cursor"]["main"]["active_batches"]) == 2
    assert not engine.state.get("window_entities")
    resumed, _ = scheduled_case(tmp_path, inputs, model, calls=calls, state=engine.state)
    resumed.run()
    assert sorted(names) == ["fast", "slow", "third"]
    assert resumed.state["cursor"]["main"]["reading_windows"]["saved"] == 3


def test_failed_window_still_saves_sibling_without_dispatching_third(tmp_path, inputs):
    from app.services.document_harness.calls import MemoryCalls

    gate, released, names = Barrier(2), Event(), []

    def model(stage, payload, schema):
        name = payload["sources"][0]["text"]
        names.append(name)
        gate.wait(timeout=3)
        if name == "fast":
            raise ValueError("failed_window")
        assert released.wait(3), "sibling must be collected after the failure is observed"
        return empty_discovery()

    calls = MemoryCalls(model)
    collect = calls.collect

    def collect_then_release(batches):
        outcomes = collect(batches)
        if any(error is not None for _, _, error in outcomes):
            released.set()
        return outcomes

    calls.collect = collect_then_release
    engine, _ = scheduled_case(tmp_path, inputs, model, calls=calls)
    with pytest.raises(ValueError, match="failed_window"):
        engine.run()
    assert sorted(names) == ["fast", "slow"]
    assert len(calls.answers) == 2 and not calls.futures
    assert engine.state["cursor"]["main"]["phase"] == "reading"


def test_one_slot_uses_identical_pipeline_and_request_set(tmp_path, inputs):
    results = []
    for concurrency in [1, 2]:
        payloads = []

        def model(stage, payload, schema):
            payloads.append((stage, deepcopy(payload), deepcopy(schema)))
            return empty_discovery()

        engine, _ = scheduled_case(tmp_path, inputs, model, concurrency=concurrency)
        engine.run()
        results.append((engine.state, payloads))
    assert results[0][0] == results[1][0]
    assert sorted(results[0][1], key=str) == sorted(results[1][1], key=str)


def test_changed_source_binding_cannot_apply_a_paid_answer(tmp_path, inputs):
    from app.services.document_harness.calls import MemoryCalls

    gate, paused = Barrier(2), Event()

    def model(*_):
        gate.wait(timeout=3)
        paused.set()
        return empty_discovery()

    calls = MemoryCalls(model)
    engine, _ = scheduled_case(tmp_path, inputs, model, calls=calls, stop=paused.is_set)
    engine.run()
    batch = next(iter(engine.state["cursor"]["main"]["active_batches"].values()))
    batch["source_bindings"]["S1"][1] += 1
    resumed, _ = scheduled_case(tmp_path, inputs, model, calls=calls, state=engine.state)
    with pytest.raises(ValueError, match="harness_active_batch_input_changed"):
        resumed.run()
    assert not resumed.state.get("window_entities")
    assert len(calls.answers) == 2


def local_case(inputs):
    ir, catalog = inputs
    window = build_windows(ir)[0]
    root = {"id": "document", "role": "document_root", "label": "Report",
            "class_iri": catalog.root_class_iri, "field_ids": [], "evidence": []}
    answer = Model()("discover", window.payload(), {})
    from app.services.document_harness.protocols import Discovery

    return ir, window, root, Discovery.model_validate(answer)


def publish(state, delta, order):
    changes = merge_local_discovery(state, delta, order)
    for domain, rows in changes.items():
        state.setdefault(domain, {}).update(deepcopy(rows))


def test_overlapping_windows_publish_same_mentions_and_fields_in_any_order(inputs):
    ir, a, root, answer = local_case(inputs)
    b = Window("later-window", deepcopy(a.sources), deepcopy(a.fields), list(a.primary_ids))
    deltas = [decode_local_discovery(ir, w, answer, {}, root) for w in [a, b]]
    states = []
    for sequence in [deltas, list(reversed(deltas))]:
        state = {"entities": {"document": deepcopy(root)}}
        for delta in sequence:
            publish(state, delta, {a.id: [0], b.id: [1]})
        states.append(state)
    assert states[0] == states[1]
    assert len(states[0]["entities"]) == 3
    assert all(e["window_id"] == a.id for k, e in states[0]["entities"].items()
               if k != "document")
    assert set(states[0]["window_entities"]) == {a.id, b.id}


def test_local_decode_never_mutates_shared_root_or_raw_answer(inputs):
    ir, window, root, answer = local_case(inputs)
    before = deepcopy((root, answer.model_dump()))
    delta = decode_local_discovery(ir, window, answer, {}, root)
    assert (root, answer.model_dump()) == before
    assert delta["complete"]
    assert all(e["state"] == "candidate" for k, e in delta["changes"]["entities"].items()
               if k != "document")


def test_document_fields_are_merged_from_current_state(inputs):
    ir, window, root, answer = local_case(inputs)
    ids = [f["alias"] for f in window.fields]
    a = answer.model_copy(update={"document_field_ids": ids[:1]})
    b = answer.model_copy(update={"document_field_ids": ids[1:2]})
    state = {"entities": {"document": deepcopy(root)}}
    for value in [a, b]:
        publish(state, decode_local_discovery(ir, window, value, {}, root), {window.id: [0]})
    assert set(state["entities"]["document"]["field_ids"]) == {
        f["id"] for f in window.fields if f["alias"] in ids[:2]
    }


def test_third_answer_does_not_erase_conflicting_name_candidates(inputs):
    ir, window, root, answer = local_case(inputs)
    altered = answer.model_copy(deep=True)
    altered.entities[0].name = altered.entities[1].name
    states = []
    for values in [[answer, altered, answer], [altered, answer, answer]]:
        state = {"entities": {"document": deepcopy(root)}}
        for value in values:
            publish(state, decode_local_discovery(ir, window, value, {}, root), {window.id: [0]})
        states.append(state)
    assert states[0] == states[1]
    entity = next(e for e in states[0]["entities"].values() if e.get("role") == "container")
    assert entity["name"] is None and len(entity["name_candidates"]) == 2
    assert any(o.get("reason_code") == "conflicting_mention_names"
               for o in states[0]["observations"].values())


def test_three_source_suggestion_sets_merge_associatively():
    from itertools import permutations

    deltas = [{"changes": {"source_candidates": {"entity": {
        "entity_id": "entity", "reason": reason, "candidates": [{"id": reason}],
    }}}} for reason in ("a", "b", "c")]
    states = []
    for sequence in permutations(deltas):
        state = {}
        for delta in sequence:
            publish(state, delta, {})
        states.append(state)
    assert all(state == states[0] for state in states)
    assert len(states[0]["source_candidates"]["entity"]["candidates"]) == 3
