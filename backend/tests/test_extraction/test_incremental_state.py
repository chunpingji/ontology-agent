"""Incremental writes preserve exact state, rollback, ownership and bounded recovery."""

from copy import deepcopy

import pytest

from app.models.document_analysis import DocumentAnalysisArtifact
from app.services.document_analysis.incremental_state import (
    decode_incremental_state,
    encode_incremental_state,
)
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_analysis.state_artifacts import StateIntegrityError, decode_state
from app.services.extraction.ontology_guided.state_delta import apply_delta, snapshot_delta

from .test_document_state_artifacts import seed


def test_frozen_ranking_cache_is_the_same_json_kind():
    from app.services.extraction.ontology_guided.semantic_reranker import _freeze

    tree = snapshot_delta({"cache": {"vectors": [[1.0, 2.0]]}, "count": 1})[0]
    value = {"cache": _freeze({"vectors": [[1.0, 2.0]]}), "count": 2}
    updated, edits = snapshot_delta(value, tree)
    assert updated.value["cache"] is tree.value["cache"]
    assert edits == [{"op": "put", "path": ["count"], "value": 2}]


def test_immutable_history_hot_delta_does_not_revisit_its_children(monkeypatch):
    from app.services.extraction.ontology_guided import state_delta

    history = state_delta.freeze_json([{"text": "evidence" * 100} for _ in range(1000)])
    tree = snapshot_delta({"history": history, "count": 1})[0]
    original = state_delta._equal
    visits = []

    def equal(left, right):
        visits.append(1)
        return original(left, right)

    monkeypatch.setattr(state_delta, "_equal", equal)
    newer, edits = snapshot_delta({"history": history, "count": 2}, tree)
    assert newer.value["history"] is history
    assert len(visits) < 10 and len(edits) == 1
    with pytest.raises(TypeError, match="immutable"):
        history[0]["text"] = "changed by consumer"


def save(store, run, token, value, kind="checkpoint"):
    head = store.get_artifact_head(run.recognition_run_id, run.owner_id, kind)
    revision = head.revision if head else 0
    encoded = encode_incremental_state(store, run, token, value, kind=kind)
    store.update_artifact(
        run.recognition_run_id, run.owner_id, token, artifact_kind=kind,
        expected_revision=revision, artifact_hash=content_hash(encoded), status="ready",
        artifact_id=f"{run.recognition_run_id}:{kind}:{revision + 1}:{content_hash(encoded)}",
        payload=encoded, event_head=run.event_head,
    )
    return encoded


def test_json_delta_exact_types_lists_deletion_and_input_detachment():
    before = {"flag": 1, "items": [{"x": 1}, 2, 3], "removed": "x", "$ref": "source"}
    tree = snapshot_delta(before)[0]
    before["items"][0]["x"] = 99
    after = {"flag": True, "items": [{"x": 1}, 3, 4, 5], "$ref": "source", "new": None}
    newer, edits = snapshot_delta(after, tree)
    assert apply_delta(tree.value, edits) == after
    assert tree.value["items"][0]["x"] == 1
    assert tree.value["flag"] is not True
    assert newer.digest == snapshot_delta(after)[0].digest != tree.digest
    after["items"].append("later")
    assert newer.value["items"] == [{"x": 1}, 3, 4, 5]
    assert snapshot_delta({"nested": {"x": 1}})[0].digest != snapshot_delta(
        {"nested": {"x": True}},
    )[0].digest


def test_hot_delta_hashes_no_unchanged_historical_payload(monkeypatch):
    from app.services.extraction.ontology_guided import state_delta

    old = {"history": [{"text": "immutable evidence " * 300} for _ in range(200)],
           "coverage": {"r1": "unattempted"}}
    tree = snapshot_delta(old)[0]
    changed = {**old, "coverage": {"r1": "examined"}}
    original = state_delta._hash
    serialized = []

    def observed(value):
        serialized.append(str(value))
        return original(value)

    monkeypatch.setattr(state_delta, "_hash", observed)
    newer, edits = snapshot_delta(changed, tree)
    assert not any("immutable evidence" in part for part in serialized)
    assert sum(map(len, serialized)) < 2000
    assert len(edits) == 1
    assert newer.value["history"] is tree.value["history"]


def test_delta_roundtrip_periodic_baseline_and_cold_recovery(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "delta-chain")
    value = {"history": [], "fixed": "frozen text" * 2000}
    versions = []
    for index in range(34):
        value = {**value, "history": [*value["history"], {"n": index}], "count": index}
        encoded = save(store, run, token, value)
        db.commit()
        versions.append(encoded)
        assert encoded["depth"] == index % 32
        if index % 32:
            assert encoded["baseline"] is None
            assert len(str(encoded)) < 2000
    fresh = DocumentAnalysisRunStore(db)
    assert decode_state(fresh, run, versions[-1]) == value
    assert decode_state(fresh, run, versions[31])["count"] == 31
    assert decode_state(fresh, run, versions[0])["history"] == [{"n": 0}]
    assert versions[32]["base"] is None


def test_delta_rollback_rejects_uncommitted_cached_state(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "delta-rollback")
    first = save(store, run, token, {"stable": "x" * 10000, "count": 1})
    db.commit()
    with pytest.raises(RuntimeError):
        with db.begin_nested():
            save(store, run, token, {"stable": "bad" * 10000, "count": 2})
            raise RuntimeError("failed graph transaction")
    db.rollback()
    third = save(store, run, token, {"stable": "x" * 10000, "count": 3})
    db.commit()
    assert decode_state(store, run, first)["count"] == 1
    assert decode_state(store, run, third) == {"stable": "x" * 10000, "count": 3}


def test_delta_rejects_other_run_kind_corruption_and_missing_predecessor(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "delta-guards")
    save(store, run, token, {"count": 1})
    db.commit()
    second = save(store, run, token, {"count": 2})
    db.commit()
    other, _ = seed(store, "delta-other")
    with pytest.raises(StateIntegrityError):
        decode_state(store, other, second)
    with pytest.raises(StateIntegrityError):
        decode_incremental_state(store, run, second, expected_kind="other")
    tampered = deepcopy(second)
    tampered["operations"][0]["value"] = 7
    with pytest.raises(StateIntegrityError, match="hash"):
        decode_state(store, run, tampered)
    prior = db.get(DocumentAnalysisArtifact, second["base"]["artifact_id"])
    prior.payload = {**prior.payload, "value_hash": "0" * 64}
    db.flush()
    with pytest.raises(StateIntegrityError, match="corrupt"):
        decode_state(store, run, second)


def test_delta_rejects_unbounded_or_invalid_paths():
    for operation in [
        {"op": "put", "path": ["absent", "x"], "value": 1},
        {"op": "splice", "path": ["items"], "index": 5, "delete_count": 0, "values": []},
        {"op": "put", "path": ["items", True], "value": 1},
        {"op": "remove", "path": ["absent"]},
    ]:
        with pytest.raises(ValueError):
            apply_delta({"items": [1, 2]}, [operation])


def test_many_delta_edits_copy_a_wide_ancestor_once_and_preserve_aliases():
    class CountedDict(dict):
        copies = 0

        def __iter__(self):
            return super().__iter__()

        def keys(self):
            type(self).copies += 1
            return super().keys()

    shared = CountedDict({str(index): index for index in range(1000)})
    source = {"ledger": shared, "alias": shared}
    edits = [{"op": "put", "path": ["ledger", str(index)], "value": -index - 1}
             for index in range(1000)]
    result = apply_delta(source, edits)
    assert result["ledger"]["999"] == -1000
    assert result["alias"] is shared and shared["999"] == 999
    assert CountedDict.copies == 1
    assert result["ledger"] is not shared


def test_large_state_rebuild_and_patch_are_cooperatively_interruptible():
    class Interrupted(RuntimeError):
        pass

    visits = []

    def guard():
        visits.append(1)
        if len(visits) == 20:
            raise Interrupted()

    source = {str(index): index for index in range(1000)}
    with pytest.raises(Interrupted):
        snapshot_delta(source, check=guard)
    assert len(visits) == 20
    visits.clear()
    with pytest.raises(Interrupted):
        apply_delta(source, [{"op": "put", "path": [str(index)], "value": -1}
                             for index in range(1000)], check=guard)
    assert source["0"] == 0 and source["19"] == 19


def test_graph_plan_mapping_avoids_rewriting_middle_list_payload(db, monkeypatch):
    from app.services.document_analysis import state_artifacts

    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "graph-stable-plans")
    monkeypatch.setattr(state_artifacts, "performance_policy",
                        lambda *_: {"state_storage_version": 3})
    plans = [{"plan_id": f"plan-{index}", "ledger": {"record": "large evidence " * 300}}
             for index in range(100)]

    def publish(value):
        head = store.get_artifact_head(run.recognition_run_id, run.owner_id, "graph")
        revision = head.revision if head else 0
        prepared = state_artifacts.prepare_run_state(store, run, value, kind="graph")
        state_artifacts.publish_prepared_state(store, run, token, prepared)
        store.update_artifact(run.recognition_run_id, run.owner_id, token, artifact_kind="graph",
                              expected_revision=revision,
                              artifact_hash=content_hash(prepared.payload),
                              artifact_id=f"graph:{revision + 1}", payload=prepared.payload,
                              status="ready")
        db.commit()
        return prepared.payload

    first = publish({"retrieval_plans": plans})
    # The incident changed an early plan and appended a new one in the same
    # batch. A list splice then included every unchanged plan in between.
    changed = [{**plans[0], "status": "examined"}, *plans[1:],
               {"plan_id": "new-plan", "ledger": {}}]
    second = publish({"retrieval_plans": changed})
    assert len(str(second["operations"])) < 1000
    assert state_artifacts.decode_state(DocumentAnalysisRunStore(db), run, second) == {
        "retrieval_plans": changed,
    }
    assert state_artifacts.decode_state(store, run, first) == {"retrieval_plans": plans}
