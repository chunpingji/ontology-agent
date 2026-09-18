"""Prefix integrity and linear receipt growth for repeated slot observations."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided.slot_replay import SlotSearchReplay


def test_search_receipts_preserve_exact_state_without_repeating_observation_history():
    writer, reader = SlotSearchReplay(), SlotSearchReplay()
    state = {"plan_id": "slot-a", "observed": {}, "attempts": [], "admitted": []}
    payloads = []
    for number in range(150):
        record = f"record-{number:03}"
        state["observed"][record] = {"status": "finished", "reason": "x" * 100}
        state["attempts"].append({"record_id": record, "task_id": f"task-{number}"})
        state["admitted"].append(record)
        payload = writer.capture("slot-a", state)
        payloads.append(payload)
        assert reader.restore("slot-a", json.loads(json.dumps(payload))) == state
    assert sum(len(json.dumps(payload)) for payload in payloads) < 150 * 1000
    assert len(json.dumps(payloads[-1])) < 1000


def test_delta_supports_changed_values_insertions_and_removals_with_detached_state():
    writer, reader = SlotSearchReplay(), SlotSearchReplay()
    old = {"plan_id": "a", "ids": ["a", "c", "d"], "nested": {"old": 1}, "status": "ready"}
    assert reader.restore("a", writer.capture("a", old)) == old
    new = {"plan_id": "a", "ids": ["a", "b", "c", "d"], "nested": {"new": [1, 2]}}
    payload = writer.capture("a", new)
    restored = reader.restore("a", payload)
    assert restored == new
    restored["ids"].clear()
    assert reader.restore("a", writer.capture("a", new)) == new


@pytest.mark.parametrize("tamper", ["base", "result", "plan", "splice"])
def test_invalid_delta_is_rejected_without_corrupting_the_valid_prefix(tamper):
    writer, reader = SlotSearchReplay(), SlotSearchReplay()
    state = {"plan_id": "a", "ids": ["a"]}
    reader.restore("a", writer.capture("a", state))
    state["ids"].append("b")
    valid = writer.capture("a", state)
    bad = deepcopy(valid)
    if tamper == "base":
        bad["base_hash"] = "0" * 64
    elif tamper == "result":
        bad["state_hash"] = "0" * 64
    elif tamper == "plan":
        bad["plan_id"] = "other"
    else:
        bad["changes"][0][2] = 1000
    with pytest.raises(ValueError):
        reader.restore("a", bad)
    assert reader.restore("a", valid) == state
