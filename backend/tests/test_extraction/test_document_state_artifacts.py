"""Exact block references, ownership, retention and transaction rollback."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models.document_analysis import DocumentAnalysisArtifact, DocumentRunArtifact
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_analysis.state_artifacts import (
    StateIntegrityError,
    decode_state,
    encode_state,
)


def seed(store, key="state"):
    run, _ = store.create_run(owner_id="state-owner", request_key=key, filename="state.docx",
                              document_hash="d" * 64, root_class_iri="urn:Report")
    store.db.commit()
    token = store.claim(run.recognition_run_id, run.owner_id, actor="test", worker_id="test",
                        lease_seconds=120)
    store.db.commit()
    return run, token


def test_exact_state_roundtrip_deduplicates_and_indexes_blocks(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    matrix = [[i / 997 + j / 113 for j in range(256)] for i in range(64)]
    original = {"cache": matrix, "cost": 1, "literal": {"$state_ref": "user-text"}}
    encoded = encode_state(store, run, token, original)
    db.commit()
    count = db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact))
    assert decode_state(store, run, encoded) == original
    assert encode_state(store, run, token, original) == encoded
    db.commit()
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact)) == count
    newer = encode_state(store, run, token, {**original, "cost": 2})
    db.commit()
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact)) - count <= 2
    assert decode_state(store, run, encoded)["cost"] == 1  # Never follows a new head.
    assert decode_state(store, run, newer)["cost"] == 2
    ids = set(db.scalars(select(DocumentAnalysisArtifact.artifact_id)))
    assert ids == set(db.scalars(select(DocumentRunArtifact.artifact_id)))


def test_reference_rejects_other_run_missing_corrupt_and_unknown_schema(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    encoded = encode_state(store, run, token, {"large": "原文" * 8000})
    db.commit()
    other, _ = seed(store, "other")
    with pytest.raises(StateIntegrityError, match="another run"):
        decode_state(store, other, encoded)
    invalid = {**encoded, "storage_schema_version": 99}
    with pytest.raises(StateIntegrityError, match="unsupported"):
        decode_state(store, run, invalid)
    reference = encoded["root"][1]
    artifact = db.get(DocumentAnalysisArtifact, reference["artifact_id"])
    original = deepcopy(artifact.payload)
    artifact.payload = {"schema_version": 1, "node": ["value", {"large": "changed"}]}
    db.flush()
    with pytest.raises(StateIntegrityError, match="corrupt"):
        decode_state(store, run, encoded)
    artifact.payload = original
    db.flush()
    link = db.get(DocumentRunArtifact, (run.recognition_run_id, reference["content_hash"], 1))
    db.delete(link)
    db.flush()
    with pytest.raises(StateIntegrityError, match="missing"):
        decode_state(store, run, encoded)


def test_failed_batch_rolls_back_all_new_blocks_and_keeps_old_refs(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    first = encode_state(store, run, token, {"large": "original" * 3000})
    db.commit()
    before = set(db.scalars(select(DocumentAnalysisArtifact.artifact_id)))
    with pytest.raises(RuntimeError, match="crash"):
        with db.begin_nested():
            encode_state(store, run, token, {"large": "new" * 10000})
            raise RuntimeError("crash")
    db.rollback()
    assert set(db.scalars(select(DocumentAnalysisArtifact.artifact_id))) == before
    assert content_hash(decode_state(store, run, first)) == content_hash(
        {"large": "original" * 3000}
    )


def test_legacy_inline_payload_is_read_only(db):
    store = DocumentAnalysisRunStore(db)
    run, _ = seed(store)
    original = {"version": 1, "cache": {"text": [0.1, 0.2]}}
    assert decode_state(store, run, original) == original
    assert not list(db.scalars(select(DocumentAnalysisArtifact)))


def test_deletion_reclaims_immutable_blocks_only_for_the_owned_run(db, tmp_path):
    from app.services.document_analysis.retention import delete_run

    store = DocumentAnalysisRunStore(db)
    first, first_token = seed(store, "delete-first")
    second, second_token = seed(store, "keep-second")
    payload = {"cache": {"text": "unchanged" * 10000}}
    first_encoded = encode_state(store, first, first_token, payload)
    second_encoded = encode_state(store, second, second_token, payload)
    db.commit()
    first_ids = set(db.scalars(select(DocumentRunArtifact.artifact_id).where(
        DocumentRunArtifact.recognition_run_id == first.recognition_run_id,
    )))
    second_ids = set(db.scalars(select(DocumentRunArtifact.artifact_id).where(
        DocumentRunArtifact.recognition_run_id == second.recognition_run_id,
    )))
    assert first_ids and second_ids and first_ids.isdisjoint(second_ids)
    assert decode_state(store, first, first_encoded) == payload
    result = delete_run(first.recognition_run_id, first.owner_id, bind=db.get_bind(),
                        storage_root=tmp_path / "artifacts")
    db.expire_all()
    assert result.final_state == "deleted"
    assert set(result.deleted_artifact_ids) == first_ids
    assert set(db.scalars(select(DocumentAnalysisArtifact.artifact_id))) == second_ids
    assert decode_state(store, second, second_encoded) == payload


def test_ranking_writer_reuses_only_its_exact_committed_boundary(db, monkeypatch):
    from app.services.document_analysis import execution

    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    run.run_fingerprint = "f" * 64
    db.commit()
    state = {"recognition_run_id": str(run.recognition_run_id),
             "run_fingerprint": run.run_fingerprint,
             "service": {"costs": {"model_calls": 1}, "cache": {"x": [0.1] * 10000}}}
    restore = execution._restore_ranking_state
    loaded = []

    def observed(*args, **kwargs):
        loaded.append(True)
        return restore(*args, **kwargs)

    monkeypatch.setattr(execution, "_restore_ranking_state", observed)
    execution._persist_ranking_state(
        db, store, run, token, final_fingerprint=run.run_fingerprint, state=state,
    )
    assert len(loaded) == 1
    state["service"]["costs"]["model_calls"] = 2
    execution._persist_ranking_state(
        db, store, run, token, final_fingerprint=run.run_fingerprint, state=state,
    )
    assert len(loaded) == 1
    with pytest.raises(execution.CheckpointMismatch, match="regressed"):
        execution._persist_ranking_state(
            db, store, run, token, final_fingerprint=run.run_fingerprint,
            state={**state, "service": {**state["service"], "costs": {"model_calls": 1}}},
        )
    new_store = DocumentAnalysisRunStore(db)
    restored = restore(db, new_store, run, final_fingerprint=run.run_fingerprint)
    assert restored["service"]["costs"] == {"model_calls": 2}
