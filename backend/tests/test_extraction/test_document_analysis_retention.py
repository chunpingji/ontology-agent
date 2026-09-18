"""Focused retention tests using only temporary run files and database rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentAnalysisTombstone,
    DocumentRecognitionEvent,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunArtifactHead,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
)
from app.services.document_analysis.artifact_store import RunArtifactStorage
from app.services.document_analysis.retention import (
    DocumentAnalysisRetentionService,
    RetentionSafetyError,
    delete_run,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    InvalidRunState,
    RunDeleted,
    RunNotFound,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _create_run(
    db,
    storage_root,
    *,
    run_id: UUID,
    owner: str,
    key: str,
    ontology_artifact_id: str = "ontology:shared",
    source_storage_uri: str | None = None,
):
    source_uri = source_storage_uri or f"{run_id}/source/original.docx"
    if source_storage_uri is None:
        source_path = storage_root / source_uri
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(f"source:{run_id}".encode())
    store = DocumentAnalysisRunStore(db)
    run, created = store.create_run_with_source(
        owner_id=owner,
        request_key=key,
        request_hash=f"request:{key}",
        filename=f"{key}.docx",
        document_hash=f"document:{key}",
        root_class_iri="urn:ontology:CMCReport",
        root_class_label="CMC Report",
        source_artifact_id=f"source:{run_id}",
        source_storage_uri=source_uri,
        source_media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        source_size_bytes=10,
        recognition_run_id=run_id,
        ontology_snapshot_hash="ontology-hash",
        ontology_artifact_id=ontology_artifact_id,
        ontology_payload={"snapshot": "shared and immutable"},
        ontology_is_exclusive=False,
    )
    assert created is True
    return store, run, storage_root / source_uri


def test_delete_fences_then_removes_only_run_owned_content_and_keeps_tombstone(db, tmp_path):
    storage_root = tmp_path / "run-artifacts"
    first_id = uuid4()
    second_id = uuid4()
    store, first, first_source = _create_run(
        db,
        storage_root,
        run_id=first_id,
        owner="alice",
        key="first",
    )
    _, second, second_source = _create_run(
        db,
        storage_root,
        run_id=second_id,
        owner="bob",
        key="second",
    )
    db.commit()

    stale_token = store.claim(
        first_id,
        "alice",
        actor="dispatcher",
        worker_id="worker-1",
    )
    event = store.append_event(
        first_id,
        "alice",
        stale_token,
        expected_head=first.event_head,
        event_key="candidate-batch",
        event_type="decision",
        payload={"status": "supported"},
    )
    store.put_proof(
        first_id,
        "alice",
        stale_token,
        proof_id="proof-1",
        proof_revision=1,
        target_id="candidate-1@1",
        payload={"decision": "supported"},
        event_sequence=event.sequence,
    )
    store.put_candidate(
        first_id,
        "alice",
        stale_token,
        candidate_id="candidate-1",
        revision=1,
        kind="entity",
        payload={"class_iri": "urn:ontology:DrugProduct"},
        proof_refs=[{"proof_id": "proof-1", "proof_revision": 1}],
        event_sequence=event.sequence,
    )
    current = store.get_owned(first_id, "alice")
    generation_before_delete = db.get(DocumentAnalysisExecution, first_id).generation
    requested = store.request_control(
        first_id,
        "alice",
        action="delete",
        expected_revision=current.revision,
        request_key="delete-first",
        reason="user request",
    )
    assert requested.deletion_state == "requested"
    db.commit()

    # Exercise the API-compatible fresh-session wrapper.  It must continue an
    # already-requested deletion instead of submitting another control action.
    result = delete_run(
        first_id,
        "alice",
        bind=db.get_bind(),
        storage_root=storage_root,
        reason="user request",
    )
    db.expire_all()

    assert result.final_state == "deleted"
    assert result.token_generation > generation_before_delete
    assert result.deleted_artifact_ids == (f"source:{first_id}",)
    assert result.retained_shared_artifact_ids == ("ontology:shared",)
    assert result.deleted_storage_uris == (f"{first_id}/source/original.docx",)
    assert not first_source.exists()
    assert second_source.exists()

    assert db.get(DocumentAnalysisRun, first_id) is None
    assert db.get(DocumentAnalysisExecution, first_id) is None
    assert db.get(DocumentAnalysisArtifact, f"source:{first_id}") is None
    assert db.get(DocumentAnalysisArtifact, "ontology:shared") is not None
    assert db.get(DocumentAnalysisRun, second.recognition_run_id) is not None
    assert (
        db.query(DocumentRunArtifact)
        .filter_by(
            recognition_run_id=second_id,
            artifact_id="ontology:shared",
        )
        .count()
        == 1
    )
    for model in (
        DocumentRunArtifact,
        DocumentRunArtifactHead,
        DocumentRunCandidate,
        DocumentRunCandidateHead,
        DocumentVerificationProof,
        DocumentVerificationProofHead,
        DocumentRecognitionEvent,
        DocumentAnalysisControlOperation,
    ):
        assert db.query(model).filter_by(recognition_run_id=first_id).count() == 0

    marker = store.get_tombstone(first_id, "alice")
    assert marker.owner_hash != "alice"
    assert marker.token_generation == result.token_generation
    with pytest.raises(RunNotFound):
        store.get_tombstone(first_id, "bob")
    with pytest.raises(RunDeleted):
        store.assert_fence(first_id, "alice", stale_token)

    replay = delete_run(
        first_id,
        "alice",
        bind=db.get_bind(),
        storage_root=storage_root,
    )
    assert replay.idempotent_replay is True
    assert replay.token_generation == result.token_generation


def test_delete_orders_checkpoint_cleanup_for_enforced_foreign_keys(tmp_path):
    fk_engine = create_engine("sqlite://", poolclass=StaticPool)

    @sqlalchemy_event.listens_for(fk_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(fk_engine)
    storage_root = tmp_path / "fk-run-artifacts"
    first_id = uuid4()
    second_id = uuid4()
    checkpoint_id = f"checkpoint:{first_id}"
    try:
        with Session(fk_engine, expire_on_commit=False) as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            store, first, first_source = _create_run(
                session,
                storage_root,
                run_id=first_id,
                owner="alice",
                key="fk-first",
                ontology_artifact_id="ontology:fk-shared",
            )
            _, second, second_source = _create_run(
                session,
                storage_root,
                run_id=second_id,
                owner="bob",
                key="fk-second",
                ontology_artifact_id="ontology:fk-shared",
            )
            session.commit()

            token = store.claim(
                first_id,
                "alice",
                actor="dispatcher",
                worker_id="checkpoint-worker",
            )
            checkpoint_event = store.append_event(
                first_id,
                "alice",
                token,
                expected_head=first.event_head,
                event_key="checkpoint-event",
                event_type="progress",
                payload={"status": "checkpointed"},
            )
            store.update_artifact(
                first_id,
                "alice",
                token,
                artifact_kind="recognition_checkpoint",
                expected_revision=0,
                artifact_hash="c" * 64,
                status="ready",
                artifact_id=checkpoint_id,
                payload={"source_excerpt": "sensitive checkpoint content"},
                event_head=checkpoint_event.sequence,
            )
            store.put_event_batch(
                first_id,
                "alice",
                token,
                batch_id="checkpoint-batch",
                batch_hash="b" * 64,
                first_sequence=checkpoint_event.sequence,
                last_sequence=checkpoint_event.sequence,
                checkpoint_artifact_id=checkpoint_id,
            )
            session.commit()

            current = store.get_owned(first_id, "alice")
            result = DocumentAnalysisRetentionService(
                session,
                storage=RunArtifactStorage(storage_root),
            ).delete_run(
                first_id,
                "alice",
                expected_revision=current.revision,
                request_key="delete-with-checkpoint",
            )

            assert result.final_state == "deleted"
            assert session.get(DocumentAnalysisRun, first_id) is None
            assert (
                session.get(
                    DocumentRecognitionEventBatch,
                    (first_id, "checkpoint-batch"),
                )
                is None
            )
            assert session.get(DocumentAnalysisArtifact, checkpoint_id) is None
            assert not first_source.exists()

            assert session.get(DocumentAnalysisRun, second_id) is not None
            assert session.get(DocumentAnalysisArtifact, "ontology:fk-shared") is not None
            assert second_source.exists()
            assert (
                session.query(DocumentRunArtifact)
                .filter_by(
                    recognition_run_id=second_id,
                    artifact_id="ontology:fk-shared",
                )
                .count()
                == 1
            )
            assert store.get_tombstone(first_id, "alice").final_state == "deleted"
    finally:
        Base.metadata.drop_all(fk_engine)
        fk_engine.dispose()


def test_schedule_and_cleanup_expired_runs_skip_active_work(db, tmp_path):
    storage_root = tmp_path / "run-artifacts"
    clock = Clock()
    due_id = uuid4()
    active_id = uuid4()
    _, due, due_source = _create_run(
        db,
        storage_root,
        run_id=due_id,
        owner="alice",
        key="due",
    )
    _, active, active_source = _create_run(
        db,
        storage_root,
        run_id=active_id,
        owner="alice",
        key="active",
        ontology_artifact_id="ontology:other-shared",
    )
    due.execution_status = "finished"
    due.finished_at = clock.value
    # Even malformed legacy/input state must not make an active run eligible.
    active.expires_at = clock.value - timedelta(seconds=1)
    db.commit()

    service = DocumentAnalysisRetentionService(
        db,
        storage=RunArtifactStorage(storage_root),
        clock=clock,
        retention_days=7,
    )
    scheduled = service.schedule_expiry(
        due_id,
        "alice",
        expected_revision=due.revision,
    )
    scheduled_expiry = scheduled.expires_at
    assert scheduled_expiry is not None
    if scheduled_expiry.tzinfo is None:  # SQLite drops timezone information.
        scheduled_expiry = scheduled_expiry.replace(tzinfo=timezone.utc)
    assert scheduled_expiry == clock.value + timedelta(days=7)
    assert service.cleanup_expired(now=clock.value + timedelta(days=6)) == ()

    results = service.cleanup_expired(now=clock.value + timedelta(days=7))
    assert len(results) == 1
    assert results[0].recognition_run_id == due_id
    assert results[0].final_state == "expired"
    assert not due_source.exists()
    assert active_source.exists()
    assert db.get(DocumentAnalysisRun, due_id) is None
    assert db.get(DocumentAnalysisRun, active_id) is not None
    assert db.get(DocumentAnalysisTombstone, due_id).final_state == "expired"
    with pytest.raises(DocumentAnalysisError) as expired:
        DocumentAnalysisApplication(db, ontology_engine=object()).get_run(due_id, "alice")
    assert expired.value.code == "RUN_EXPIRED"

    with pytest.raises(InvalidRunState):
        service.schedule_expiry(
            active_id,
            "alice",
            expected_revision=active.revision,
        )


def test_unsafe_storage_reference_leaves_durable_deleting_fence(db, tmp_path):
    storage_root = tmp_path / "run-artifacts"
    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"must survive")
    run_id = uuid4()
    store, run, _ = _create_run(
        db,
        storage_root,
        run_id=run_id,
        owner="alice",
        key="unsafe",
        source_storage_uri="../outside.docx",
    )
    db.commit()
    stale_token = store.claim(
        run_id,
        "alice",
        actor="dispatcher",
        worker_id="worker-unsafe",
    )
    db.commit()

    service = DocumentAnalysisRetentionService(
        db,
        storage=RunArtifactStorage(storage_root),
    )
    with pytest.raises(RetentionSafetyError):
        service.delete_run(
            run_id,
            "alice",
            expected_revision=run.revision,
        )

    db.expire_all()
    fenced = store.get_owned(run_id, "alice", include_deleted=True)
    assert fenced.deletion_state == "deleting"
    assert outside.read_bytes() == b"must survive"
    assert db.get(DocumentAnalysisTombstone, run_id) is None
    with pytest.raises(FenceViolation):
        store.assert_fence(run_id, "alice", stale_token)
