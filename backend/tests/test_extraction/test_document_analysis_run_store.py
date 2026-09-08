"""Focused contract tests for the independent DocumentAnalysisRun store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentAnalysisTombstone,
    DocumentRecognitionEvent,
    DocumentRunArtifact,
    DocumentRunArtifactHead,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)
from app.services.document_analysis.run_store import (
    ArtifactConflict,
    DocumentAnalysisRunStore,
    EventConflict,
    FenceViolation,
    HeadConflict,
    IdempotencyConflict,
    RunDeleted,
    RunNotFound,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def create_run(store, *, owner="alice", key="request-1", digest="request-hash"):
    return store.create_run(
        owner_id=owner,
        request_key=key,
        request_hash=digest,
        filename="report.docx",
        document_hash="document-hash",
        root_class_iri="urn:ontology:CMCReport",
        root_class_label="CMC Report",
        ontology_snapshot_hash="ontology-hash",
    )


def claim(store, run, *, owner="alice"):
    return store.claim(
        run.recognition_run_id,
        owner,
        actor="dispatcher",
        worker_id="worker-1",
        lease_seconds=30,
    )


def test_owner_request_key_is_idempotent_but_not_cross_owner(db):
    store = DocumentAnalysisRunStore(db)
    first, created = create_run(store)
    replay, replay_created = create_run(store)

    assert created is True
    assert replay_created is False
    assert replay.recognition_run_id == first.recognition_run_id
    with pytest.raises(IdempotencyConflict):
        create_run(store, digest="different-input")

    other, other_created = create_run(store, owner="bob")
    assert other_created is True
    assert other.recognition_run_id != first.recognition_run_id
    with pytest.raises(RunNotFound):
        store.get_owned(first.recognition_run_id, "bob")


def test_create_with_source_and_ontology_is_one_idempotent_watermark(db):
    store = DocumentAnalysisRunStore(db)
    kwargs = {
        "owner_id": "alice",
        "request_key": "atomic-create",
        "request_hash": "atomic-input",
        "filename": "report.docx",
        "document_hash": "source-hash",
        "root_class_iri": "urn:ontology:CMCReport",
        "root_class_label": "CMC Report",
        "ontology_snapshot_hash": "ontology-hash",
        "source_artifact_id": "source-artifact",
        "source_storage_uri": "runs/private/source.docx",
        "source_media_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "source_size_bytes": 123,
        "ontology_artifact_id": "ontology-artifact",
        "ontology_storage_uri": "runs/private/ontology.json",
        "ontology_payload": {"classes": ["urn:ontology:CMCReport"]},
    }
    run, created = store.create_run_with_source(**kwargs)
    replay, replay_created = store.create_run_with_source(**kwargs)

    assert created is True
    assert replay_created is False
    assert replay.recognition_run_id == run.recognition_run_id
    assert run.event_head == 1
    assert run.artifact_revision == 1  # one committed public watermark, not object count
    assert run.source_artifact_ref == "source-artifact"
    assert set(run.artifact_manifest) == {"source", "ontology_snapshot"}
    assert run.artifact_manifest["source"]["status"] == "ready"
    assert (
        db.query(DocumentRecognitionEvent)
        .filter_by(recognition_run_id=run.recognition_run_id)
        .count()
        == 1
    )
    assert (
        db.query(DocumentRunArtifact).filter_by(recognition_run_id=run.recognition_run_id).count()
        == 2
    )
    assert (
        db.query(DocumentRunArtifactHead)
        .filter_by(recognition_run_id=run.recognition_run_id)
        .count()
        == 2
    )
    assert db.query(DocumentAnalysisArtifact).count() == 2

    with pytest.raises(ArtifactConflict):
        store.create_run_with_source(
            **{
                **kwargs,
                "owner_id": "bob",
                "request_key": "bob-atomic-create",
                "request_hash": "bob-atomic-input",
                "ontology_artifact_id": None,
            }
        )
    assert db.query(DocumentAnalysisRun).filter_by(owner_id="bob").count() == 0


def test_event_sequence_event_key_and_event_head_are_atomic(db):
    store = DocumentAnalysisRunStore(db)
    run, _ = create_run(store)
    token = claim(store, run)

    first = store.append_event(
        run.recognition_run_id,
        "alice",
        token,
        expected_head=0,
        event_key="batch-1:accepted",
        event_type="run_state",
        payload={"status": "running"},
    )
    assert first.sequence == 1
    assert store.get_owned(run.recognition_run_id, "alice").event_head == 1

    # Delivery retry is idempotent even though its original expected head is stale.
    replay = store.append_event(
        run.recognition_run_id,
        "alice",
        token,
        expected_head=0,
        event_key="batch-1:accepted",
        event_type="run_state",
        payload={"status": "running"},
    )
    assert replay.sequence == first.sequence
    with pytest.raises(EventConflict):
        store.append_event(
            run.recognition_run_id,
            "alice",
            token,
            expected_head=1,
            event_key="batch-1:accepted",
            event_type="run_state",
            payload={"status": "finished"},
        )
    with pytest.raises(HeadConflict):
        store.append_event(
            run.recognition_run_id,
            "alice",
            token,
            expected_head=0,
            event_key="batch-2",
            event_type="progress",
            payload={"tasks_attempted": 1},
        )

    second = store.append_event(
        run.recognition_run_id,
        "alice",
        token,
        expected_head=1,
        event_key="batch-2",
        event_type="progress",
        payload={"tasks_attempted": 1},
    )
    assert second.sequence == 2
    assert [event.sequence for event in store.list_events(run.recognition_run_id, "alice")] == [
        1,
        2,
    ]


def test_candidate_and_proof_keep_their_real_first_revision(db):
    store = DocumentAnalysisRunStore(db)
    run, _ = create_run(store)
    token = claim(store, run)
    event = store.append_event(
        run.recognition_run_id,
        "alice",
        token,
        expected_head=0,
        event_key="verified-product",
        event_type="decision",
        payload={"target": "product"},
    )

    proof = store.put_proof(
        run.recognition_run_id,
        "alice",
        token,
        proof_id="proof:type:product",
        proof_revision=7,
        target_id="candidate:product@2",
        payload={"decision": "supported", "source_refs": ["span:1"]},
        event_sequence=event.sequence,
    )
    candidate = store.put_candidate(
        run.recognition_run_id,
        "alice",
        token,
        candidate_id="candidate:product",
        revision=2,
        kind="entity",
        payload={"class_iri": "urn:ontology:DrugProduct"},
        proof_refs=[{"proof_id": proof.proof_id, "revision": proof.proof_revision}],
        event_sequence=event.sequence,
    )
    assert candidate.revision == 2
    assert proof.proof_revision == 7
    assert (
        db.get(
            DocumentRunCandidateHead,
            (run.recognition_run_id, "candidate:product"),
        ).revision
        == 2
    )
    assert (
        db.get(
            DocumentVerificationProofHead,
            (run.recognition_run_id, "proof:type:product"),
        ).proof_revision
        == 7
    )

    later = store.put_candidate(
        run.recognition_run_id,
        "alice",
        token,
        candidate_id="candidate:product",
        revision=5,
        expected_head_revision=2,
        kind="entity",
        payload={"class_iri": "urn:ontology:DrugProduct", "state": "supported"},
        proof_refs=[{"proof_id": proof.proof_id, "revision": 7}],
        event_sequence=event.sequence,
    )
    assert later.revision == 5
    assert (
        db.get(
            DocumentRunCandidate,
            (run.recognition_run_id, "candidate:product", 2),
        )
        is candidate
    )
    with pytest.raises(HeadConflict):
        store.put_candidate(
            run.recognition_run_id,
            "alice",
            token,
            candidate_id="candidate:product",
            revision=6,
            expected_head_revision=2,
            kind="entity",
            payload={"state": "stale"},
        )


def test_artifact_head_cas_keeps_ids_distinct_and_access_owner_scoped(db):
    store = DocumentAnalysisRunStore(db)
    alice_run, _ = create_run(store)
    alice_token = claim(store, alice_run)

    artifact = store.update_artifact(
        alice_run.recognition_run_id,
        "alice",
        alice_token,
        artifact_kind="metadata",
        expected_revision=0,
        artifact_id="metadata-artifact-a",
        artifact_hash="same-content-hash",
        status="ready",
        payload={"title": "Report"},
        analysis_id="analysis-a",
        metadata_snapshot_id="metadata-a",
    )
    current = store.get_owned(alice_run.recognition_run_id, "alice")
    assert artifact.revision == 1
    assert current.analysis_id == "analysis-a"
    assert current.metadata_snapshot_id == "metadata-a"
    assert (
        len(
            {
                str(current.recognition_run_id),
                current.analysis_id,
                current.metadata_snapshot_id,
            }
        )
        == 3
    )
    assert current.artifact_revision == 1
    with pytest.raises(ArtifactConflict):
        store.update_artifact(
            alice_run.recognition_run_id,
            "alice",
            alice_token,
            artifact_kind="metadata",
            expected_revision=0,
            artifact_id="metadata-artifact-new",
            artifact_hash="new-hash",
            status="ready",
        )

    bob_run, _ = create_run(store, owner="bob", digest="bob-input")
    bob_token = claim(store, bob_run, owner="bob")
    store.update_artifact(
        bob_run.recognition_run_id,
        "bob",
        bob_token,
        artifact_kind="metadata",
        expected_revision=0,
        artifact_id="metadata-artifact-b",
        artifact_hash="same-content-hash",
        status="ready",
    )
    with pytest.raises(RunNotFound):
        store.get_artifact(alice_run.recognition_run_id, "bob", "metadata")


def test_expired_and_replaced_lease_tokens_are_fenced(db):
    clock = Clock()
    store = DocumentAnalysisRunStore(db, clock=clock)
    run, _ = create_run(store)
    old_token = claim(store, run)
    assert store.assert_fence(run.recognition_run_id, "alice", old_token).generation == 1

    clock.advance(31)
    with pytest.raises(FenceViolation):
        store.assert_fence(run.recognition_run_id, "alice", old_token)
    new_token = claim(store, run)
    assert new_token != old_token
    assert store.assert_fence(run.recognition_run_id, "alice", new_token).generation == 2
    with pytest.raises(FenceViolation):
        store.assert_fence(run.recognition_run_id, "alice", old_token)


def test_pause_resume_and_tombstone_revoke_workers_without_deleting_rows(db):
    clock = Clock()
    store = DocumentAnalysisRunStore(db, clock=clock)
    run, _ = create_run(store)
    old_token = claim(store, run)
    proof = store.put_proof(
        run.recognition_run_id,
        "alice",
        old_token,
        proof_id="proof-1",
        proof_revision=3,
        target_id="candidate-1@4",
        payload={"decision": "supported"},
    )

    current = store.get_owned(run.recognition_run_id, "alice")
    pause_expected = current.revision
    pausing = store.request_control(
        run.recognition_run_id,
        "alice",
        action="pause",
        expected_revision=pause_expected,
        request_key="pause-operation",
        reason="operator pause",
    )
    assert pausing.execution_status == "pausing"
    replay = store.request_control(
        run.recognition_run_id,
        "alice",
        action="pause",
        expected_revision=pause_expected,
        request_key="pause-operation",
        reason="operator pause",
    )
    assert replay.revision == pausing.revision
    assert db.query(DocumentAnalysisControlOperation).count() == 1
    with pytest.raises(IdempotencyConflict):
        store.request_control(
            run.recognition_run_id,
            "alice",
            action="pause",
            expected_revision=pause_expected,
            request_key="pause-operation",
            reason="changed reason",
        )
    paused = store.complete_pause(
        run.recognition_run_id,
        "alice",
        old_token,
        expected_revision=pausing.revision,
    )
    assert paused.execution_status == "paused"
    with pytest.raises(FenceViolation):
        store.assert_fence(run.recognition_run_id, "alice", old_token)

    resumed_token = store.resume(
        run.recognition_run_id,
        "alice",
        expected_revision=paused.revision,
        actor="dispatcher",
        worker_id="worker-2",
    )
    assert store.assert_fence(run.recognition_run_id, "alice", resumed_token)
    current = store.get_owned(run.recognition_run_id, "alice")
    requested = store.request_control(
        run.recognition_run_id,
        "alice",
        action="delete",
        expected_revision=current.revision,
        reason="user request",
    )
    assert requested.deletion_state == "requested"
    with pytest.raises(FenceViolation):
        store.assert_fence(run.recognition_run_id, "alice", resumed_token)
    deleting = store.mark_deleting(
        run.recognition_run_id,
        "alice",
        expected_revision=requested.revision,
    )
    marker = store.tombstone(
        run.recognition_run_id,
        "alice",
        expected_revision=deleting.revision,
        reason="retention cleanup",
    )

    assert marker.owner_hash != "alice"
    assert marker.final_state == "deleted"
    assert db.get(DocumentAnalysisTombstone, run.recognition_run_id) is marker
    assert (
        db.get(
            DocumentVerificationProof,
            (run.recognition_run_id, proof.proof_id, proof.proof_revision),
        )
        is proof
    )
    assert db.get(DocumentAnalysisExecution, run.recognition_run_id) is not None
    with pytest.raises(RunDeleted):
        store.get_owned(run.recognition_run_id, "alice")
    with pytest.raises(RunNotFound):
        store.get_tombstone(run.recognition_run_id, "bob")


def test_candidate_event_reference_cannot_cross_runs(db):
    store = DocumentAnalysisRunStore(db)
    first, _ = create_run(store)
    first_token = claim(store, first)
    event = store.append_event(
        first.recognition_run_id,
        "alice",
        first_token,
        expected_head=0,
        event_key="event-1",
        event_type="decision",
        payload={},
    )
    second, _ = create_run(store, key="request-2", digest="request-hash-2")
    second_token = claim(store, second)
    with pytest.raises(HeadConflict):
        store.put_candidate(
            second.recognition_run_id,
            "alice",
            second_token,
            candidate_id="candidate-1",
            revision=2,
            kind="entity",
            payload={},
            event_sequence=event.sequence,
        )
    assert (
        db.scalar(
            select(DocumentRunCandidate).where(
                DocumentRunCandidate.recognition_run_id == second.recognition_run_id
            )
        )
        is None
    )
