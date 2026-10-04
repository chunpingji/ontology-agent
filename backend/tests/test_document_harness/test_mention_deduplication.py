"""Physical mention identity is invariant under wording, windows and publication order."""

from copy import deepcopy
from itertools import permutations

from test_controller import inputs as inputs
from test_discovery_optimization import answer, ir_for, reader
from test_parallel_reading import local_case, publish

from app.services.document_harness.protocols import Discovery
from app.services.document_harness.reading import decode_local_discovery
from app.services.document_harness.source import Window


def test_role_and_type_alternatives_merge_in_any_window_completion_order(inputs):
    ir, first, root, original = local_case(inputs)
    windows = [first, *[Window(f"child-{i}", deepcopy(first.sources), deepcopy(first.fields),
                              list(first.primary_ids)) for i in range(2)]]
    deltas = []
    for i, window in enumerate(windows):
        changed = original.model_copy(deep=True)
        changed.entities[0].role = f"原文对象角色说明{i}"
        changed.entities[0].candidate_class_iri = (
            "urn:test:Alternative" if i == 1 else "urn:test:Thing")
        deltas.append(decode_local_discovery(ir, window, changed, {}, root,
                                            class_iris=["urn:test:Thing", "urn:test:Alternative"]))
    states = []
    order = {window.id: [i] for i, window in enumerate(windows)}
    for sequence in permutations(deltas):
        state = {"entities": {"document": deepcopy(root)}}
        for delta in sequence:
            publish(state, delta, order)
        states.append(state)
    assert all(state == states[0] for state in states)
    assert len(states[0]["entities"]) == 3
    merged = next(e for e in states[0]["entities"].values()
                  if len(e.get("role_candidates", [])) == 3)
    assert merged["discovery_class_iris"] == ["urn:test:Alternative", "urn:test:Thing"]
    assert merged["class_iri"] is None and merged["state"] == "candidate"
    assert merged["window_id"] == first.id
    assert all(merged["id"] in row["ids"] for row in states[0]["window_entities"].values())


def test_same_answer_rephrases_one_anchor_without_losing_fields_or_hint_endpoints(inputs):
    ir, window, root, response = local_case(inputs)
    repeated = response.entities[0].model_copy(deep=True)
    repeated.local_id = "repeated"
    repeated.role = "同一对象的另一种角色表述"
    response.entities.append(repeated)
    delta = decode_local_discovery(ir, window, response, {}, root, class_iris=["urn:test:Thing"])
    entities = {key: e for key, e in delta["changes"]["entities"].items() if key != "document"}
    assert len(entities) == 2
    duplicate = next(e for e in entities.values() if len(e["role_candidates"]) == 2)
    assert duplicate["field_ids"]
    assert all(h["subject_id"] in entities and h["object_id"] in entities
               for h in delta["changes"]["hints"].values())


def test_parent_split_rereads_keep_separate_occurrences_without_multiplying(tmp_path, inputs):
    ir = ir_for(tmp_path, ["隔膜泵用于滴加。", "隔膜泵用于另一操作。"])
    calls = []

    def invoke(stage, payload, schema):
        assert stage == "discover"
        result = answer(payload)
        first = not calls
        calls.append(payload)
        for mention in result["entities"]:
            mention["name"] = {"source_id": mention["anchor"]["source_id"], "text": "隔膜泵"}
            mention["anchor"]["text"] = "隔膜泵"
            mention["role"] = "用于转移的泵" if first else "用于滴加的设备"
        result["complete"] = not first
        return result

    engine = reader(ir, inputs[1], invoke)
    engine.run()
    entities = [e for e in engine.state["entities"].values() if e["id"] != "document"]
    assert len(calls) == 3 and len(entities) == 2
    assert all(len(e["role_candidates"]) == 2 for e in entities)
    assert len({e["referent"]["source_id"] for e in entities}) == 2
    assert not engine.state.get("coreferences")


def test_identical_names_at_different_offsets_remain_distinct(tmp_path, inputs):
    ir = ir_for(tmp_path, ["左侧隔膜泵与右侧隔膜泵用于不同操作。"])

    def invoke(stage, payload, schema):
        result = answer(payload)
        source_id = result["entities"][0]["anchor"]["source_id"]
        result["entities"] = [
            {**result["entities"][0], "local_id": str(i), "anchor": {
                "source_id": source_id, "text": "隔膜泵", "occurrence": i,
            }} for i in range(2)
        ]
        return Discovery.model_validate(result).model_dump(mode="json")

    engine = reader(ir, inputs[1], invoke)
    engine.run()
    entities = [e for e in engine.state["entities"].values() if e["id"] != "document"]
    assert len(entities) == 2
    assert len({e["referent"]["start"] for e in entities}) == 2
