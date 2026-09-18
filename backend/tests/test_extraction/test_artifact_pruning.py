"""Operational pruning preserves cold recovery and durable model/batch receipts."""

import pytest
from sqlalchemy import select

from app.models.document_analysis import (
    DocumentAnalysisArtifact as Artifact,
)
from app.models.document_analysis import (
    DocumentRecognitionEventBatch as Batch,
)
from app.models.document_analysis import (
    DocumentRunArtifact as Ref,
)
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_analysis.state_artifacts import decode_state, encode_state
from scripts.prune_document_analysis_artifacts import apply_plan, build_plan, root_digests

from .test_document_state_artifacts import seed
from .test_incremental_state import save


def pause(db, run):
    run.execution_status = "paused"
    db.commit()


def test_prune_inline_keeps_three_and_batch_idempotency_metadata(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    for revision in range(1, 7):
        body = {"version": revision, "history": list(range(revision))}
        identity = f"checkpoint-{revision}"
        store.update_artifact(
            run.recognition_run_id, run.owner_id, token,
            artifact_kind="recognition_checkpoint", expected_revision=revision - 1,
            artifact_hash=content_hash(body), status="ready", artifact_id=identity, payload=body,
        )
        db.add(Batch(recognition_run_id=run.recognition_run_id, batch_id=f"batch-{revision}",
                     content_hash=content_hash(body), first_sequence=1, last_sequence=1,
                     checkpoint_artifact_id=identity))
        db.commit()
    pause(db, run)
    plan = build_plan(db)
    assert len(plan.roots) == 3 and len(plan.erase_payload) == 3
    before = root_digests(db, plan)
    apply_plan(db, plan)
    assert root_digests(db, plan) == before
    assert list(db.scalars(select(Ref.revision).where(
        Ref.artifact_kind == "recognition_checkpoint",
    ).order_by(Ref.revision))) == [4, 5, 6]
    assert db.get(Artifact, "checkpoint-1").payload is None
    assert db.get(Batch, (run.recognition_run_id, "batch-1")) is not None
    assert db.get(Artifact, "checkpoint-4").payload["version"] == 4
    repeated = build_plan(db)
    assert not repeated.remove_refs and not repeated.erase_payload
    assert repeated.verified_hashes == plan.verified_hashes


def test_prune_v3_retains_predecessors_and_collects_unreachable_blocks(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    latest = None
    for revision in range(40):
        latest = save(store, run, token, {"text": str(revision) * 5000}, kind="graph")
        db.commit()
    pause(db, run)
    plan = build_plan(db)
    assert plan.dependency_versions == {"graph": 5}  # Versions 33-37 support 38-40.
    assert any(plan.artifact_kinds[item] == "state_block" for item in plan.delete_artifacts)
    before = root_digests(db, plan)
    apply_plan(db, plan)
    assert root_digests(db, plan) == before
    after = build_plan(db)
    assert after.verified_hashes == plan.verified_hashes
    assert after.dependency_edges == plan.dependency_edges
    assert decode_state(DocumentAnalysisRunStore(db), run, latest) == {"text": "39" * 5000}
    assert list(db.scalars(select(Ref.revision).where(
        Ref.artifact_kind == "graph",
    ).order_by(Ref.revision))) == list(range(33, 41))


def test_prune_keeps_all_model_call_history_blocks(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    versions = []
    for revision in range(1, 5):
        body = {"protocol": f"model-evidence-{revision}" * 2000}
        encoded = encode_state(store, run, token, body)
        store.update_artifact(
            run.recognition_run_id, run.owner_id, token,
            artifact_kind="recognition-model-calls", expected_revision=revision - 1,
            artifact_hash=content_hash(encoded), status="ready",
            artifact_id=f"model-{revision}", payload=encoded,
        )
        versions.append((encoded, body))
        db.commit()
    encode_state(store, run, token, {"orphan": "unreferenced" * 5000})
    pause(db, run)
    plan = build_plan(db)
    assert plan.delete_artifacts
    apply_plan(db, plan)
    for encoded, body in versions:
        assert decode_state(DocumentAnalysisRunStore(db), run, encoded) == body


def test_prune_rejects_active_run_and_broken_dependency(db):
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store)
    save(store, run, token, {"n": 1}, kind="graph")
    latest = save(store, run, token, {"n": 2}, kind="graph")
    db.commit()
    with pytest.raises(ValueError, match="pause active"):
        build_plan(db)
    pause(db, run)
    db.get(Artifact, latest["base"]["artifact_id"]).payload = None
    db.commit()
    with pytest.raises(ValueError, match="missing or corrupt"):
        build_plan(db)
