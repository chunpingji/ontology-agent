"""A lineage's frozen reference selection survives exact protocol persistence."""

from copy import deepcopy

import pytest

from app.services.document_analysis import current_state
from app.services.document_analysis.execution import CheckpointMismatch
from app.services.document_analysis.run_store import HeadConflict
from app.services.extraction.ontology_guided.current_work import validate_tool_protocol
from tests.test_extraction.test_tool_engine_resume import persist, protocol_state

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def reference_context():
    return {
        "version": 1,
        "entity_refs": [{"id": "entity-first", "revision": 1},
                        {"id": "entity-second", "revision": 2}],
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_reference_context_roundtrip_keeps_legacy_shape(current_run, enabled):
    store, run, _token = current_run
    protocol = protocol_state()
    if enabled:
        protocol["reference_context"] = reference_context()
    before = deepcopy(protocol)
    validate_tool_protocol(protocol)
    persist(current_run, protocol)
    store.db.expire_all()
    restored = current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"]
    assert restored == before
    assert ("reference_context" in restored) is enabled
    # Reapplying the same current checkpoint neither adds a default to old
    # protocols nor changes the order/revision of the frozen entity selection.
    persist(current_run, restored)
    assert current_state.restore_calls(store, run, run.run_fingerprint)["protocols"]["e"] == before


@pytest.mark.parametrize("invalid", [
    None,
    {"version": True, "entity_refs": []},
    {"version": "1", "entity_refs": []},
    {"version": 2, "entity_refs": []},
    {"version": 1, "entity_refs": None},
    {"version": 1, "entity_refs": [], "unregistered": True},
    {"version": 1, "entity_refs": [{"id": "same", "revision": 1}] * 2},
    {"version": 1, "entity_refs": [{}]},
    {"version": 1, "entity_refs": [{"id": "same"}]},
    {"version": 1, "entity_refs": [{"id": "", "revision": 1}]},
    {"version": 1, "entity_refs": [{"id": "same", "revision": 0}]},
    {"version": 1, "entity_refs": [{"id": "same", "revision": True}]},
    {"version": 1, "entity_refs": [{"id": "same", "revision": "1"}]},
    {"version": 1, "entity_refs": [{"id": "same", "revision": 1, "label": "untrusted"}]},
])
def test_invalid_reference_context_never_reaches_durable_checkpoint(current_run, invalid):
    store, run, _token = current_run
    original = protocol_state()
    persist(current_run, original)
    changed = {**original, "reference_context": invalid}
    with pytest.raises(ValueError):
        validate_tool_protocol(changed)
    with pytest.raises(CheckpointMismatch):
        persist(current_run, changed)
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"]["e"] == original


@pytest.mark.parametrize("change", ["add", "remove", "replace", "revision", "reorder"])
@pytest.mark.parametrize("new_evidence_revision", [False, True])
def test_committed_reference_selection_cannot_be_changed(
    current_run, change, new_evidence_revision,
):
    store, run, _token = current_run
    original = protocol_state()
    if change != "add":
        original["reference_context"] = reference_context()
    persist(current_run, original)
    changed = deepcopy(original)
    if change == "add":
        changed["reference_context"] = reference_context()
    elif change == "remove":
        del changed["reference_context"]
    elif change == "replace":
        changed["reference_context"]["entity_refs"][0]["id"] = "unrelated-entity"
    elif change == "revision":
        changed["reference_context"]["entity_refs"][0]["revision"] += 1
    else:
        changed["reference_context"]["entity_refs"].reverse()
    if new_evidence_revision:
        changed["evidence_revision"] += 1
    # Every mutated object is valid on its own. Persistence must reject changing
    # the paid lineage's identity even when ordinary evidence revision advances.
    validate_tool_protocol(changed)
    with pytest.raises(HeadConflict, match="^tool protocol frozen identity changed$"):
        persist(current_run, changed)
    store.db.expire_all()
    restored = current_state.restore_calls(store, run, run.run_fingerprint)
    assert restored["protocols"]["e"] == original
