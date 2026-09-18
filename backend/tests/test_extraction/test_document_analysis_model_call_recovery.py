"""Durable pre-chat reservations remain charged across worker replacement."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentRecognitionEventBatch,
)
from app.services.document_analysis import execution as execution_service
from app.services.document_analysis.run_store import FenceViolation
from app.services.extraction.ontology_guided.executor import (
    ModelCallPersistenceFailure,
    TaskOutcome,
)
from app.services.llm.model_runtime import ModelCancelled
from tests.test_extraction.test_document_analysis_execution_recovery import (
    SimulatedProcessCrash,
    _claim,
    _create_pending_run,
    _expire_lease,
)


class TwoStageAdapter:
    model_identity = "online-budget-recovery-v1"

    def __init__(self):
        self.calls = []

    def inspect(self, task, context, _predicate, _menu):
        completed = 0
        for ordinal, stage in enumerate(("discovery", "verification"), 1):
            if completed >= context.remaining_model_calls:
                return TaskOutcome(
                    semantic_outcome="not_checked", complete=False,
                    reason_code="record_model_call_budget_exhausted",
                    reason="Verification has no remaining budget.", model_calls=completed,
                )
            context.before_model_call(stage, ordinal)
            self.calls.append((task.task_id, stage))
            completed += 1
        return TaskOutcome(
            semantic_outcome="unsupported", reason_code="record_not_supporting_target",
            reason="Controlled request returned no supported assertion.", model_calls=completed,
        )


def _reservation_state(run_id):
    return {
        "version": 1, "recognition_run_id": run_id, "run_fingerprint": "call-fingerprint",
        "lineage_calls": {"private-lineage": 1},
        "reservations": [{"sequence": 1, "task_id": "private-task", "stage": "discovery",
                          "ordinal": 1, "lineage_id": "private-lineage"}],
    }


def test_model_call_sidecar_is_monotonic_fenced_and_private(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="model-call-private-sidecar"
    )
    store, token = _claim(db, run_id)
    run = store.get_owned(run_id, "analyst")
    run = store.update_stage(
        run_id, "analyst", token, expected_revision=run.revision,
        stage="recognition", run_fingerprint="call-fingerprint",
    )
    db.commit()
    state = _reservation_state(run_id)

    def persist(value, current_token=token):
        execution_service._persist_model_call_state(
            db, store, store.get_owned(run_id, "analyst"), current_token,
            final_fingerprint="call-fingerprint", state=value,
        )

    def restore():
        return execution_service._restore_model_call_state(
            db, store, store.get_owned(run_id, "analyst"), final_fingerprint="call-fingerprint"
        )

    persist(state)
    assert restore() == state
    run = store.get_owned(run_id, "analyst")
    waterline = (run.revision, run.event_head, run.artifact_revision)
    persist(state)
    for path in (f"/api/document-analysis/runs/{run_id}",
                 f"/api/document-analysis/runs/{run_id}/graph"):
        response = client.get(path, headers=analyst_headers)
        assert response.status_code == 200
        assert "private-task" not in response.text
        assert "private-lineage" not in response.text
        assert "recognition-model-calls" not in response.text
        assert "reservations" not in response.text
        if path.endswith(run_id):
            assert response.json()["progress"]["model_calls_reserved"] == 1
            assert response.json()["progress"]["model_calls_unresolved"] == 1
            assert response.json()["progress"]["model_calls"] == 0
    run = store.get_owned(run_id, "analyst")
    assert (run.revision, run.event_head, run.artifact_revision) == waterline

    # A completed outcome predating reservations can conservatively increase
    # the next count; old reservation entries themselves remain immutable.
    changed = deepcopy(state)
    changed["lineage_calls"]["private-lineage"] = 3
    changed["reservations"].append({**changed["reservations"][0], "sequence": 2})
    persist(changed)
    for mutate in (
        lambda value: value["reservations"][0].update(task_id="replacement"),
        lambda value: value.update(reservations=value["reservations"][:1]),
        lambda value: value["lineage_calls"].update({"private-lineage": 2}),
        lambda value: value["reservations"][0].update(sequence=True),
        lambda value: value["reservations"][0].update(ordinal=0),
        lambda value: value["reservations"][0].update(prompt="secret request text"),
        lambda value: value.update(version=True),
    ):
        invalid = deepcopy(changed)
        mutate(invalid)
        with pytest.raises(execution_service.CheckpointMismatch):
            persist(invalid)
    foreign = deepcopy(changed)
    foreign["run_fingerprint"] = "foreign"
    with pytest.raises(execution_service.FingerprintMismatch):
        persist(foreign)
    assert restore() == changed
    _expire_lease(db, run_id)
    _claim(db, run_id)
    with pytest.raises(FenceViolation):
        persist(changed)
    assert restore() == changed

    ref = store.get_artifact(run_id, "analyst", "recognition-model-calls")
    artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id)
    artifact.payload = {**artifact.payload, "lineage_calls": {}}
    db.commit()
    with pytest.raises(execution_service.CheckpointMismatch, match="artifact head"):
        restore()


def test_pre_chat_crashes_charge_budget_without_completed_checkpoint(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="model-call-crash-before-chat"
    )
    monkeypatch.setattr(settings, "semantic_ranking_enabled", False)
    monkeypatch.setattr(settings, "document_analysis_max_model_calls_per_record", 2)
    adapter = TwoStageAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)
    original = execution_service._persist_model_call_state

    def persist_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise SimulatedProcessCrash("reservation committed before chat")

    monkeypatch.setattr(execution_service, "_persist_model_call_state", persist_then_crash)
    states = []
    for _attempt in range(2):
        store, token = _claim(db, run_id)
        with pytest.raises(SimulatedProcessCrash):
            execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
        assert adapter.calls == []
        assert db.scalar(select(DocumentRecognitionEventBatch).where(
            DocumentRecognitionEventBatch.recognition_run_id == run_id
        )) is None
        current = store.get_owned(run_id, "analyst")
        states.append(execution_service._restore_model_call_state(
            db, store, current, final_fingerprint=current.run_fingerprint,
        ))
        public = client.get(f"/api/document-analysis/runs/{run_id}", headers=analyst_headers).json()
        assert public["progress"]["model_calls"] == 0
        assert public["progress"]["model_calls_reserved"] == _attempt + 1
        assert public["progress"]["model_calls_unresolved"] == _attempt + 1
        _expire_lease(db, run_id)
    assert states[1]["reservations"][:1] == states[0]["reservations"]
    assert len(states[1]["reservations"]) == 2
    interrupted_task = states[0]["reservations"][0]["task_id"]
    assert list(states[1]["lineage_calls"].values()) == [2]

    monkeypatch.setattr(execution_service, "_persist_model_call_state", original)
    store, replacement = _claim(db, run_id)
    execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), replacement)
    run = store.get_owned(run_id, "analyst")
    assert run.execution_status == "failed"
    assert run.error["code"] == "ANALYSIS_INCOMPLETE"
    assert adapter.calls and all(task != interrupted_task for task, _stage in adapter.calls)
    state = execution_service._restore_model_call_state(
        db, store, run, final_fingerprint=run.run_fingerprint,
    )
    assert max(state["lineage_calls"].values()) <= 2
    checkpoint = execution_service._restore_recognition_checkpoint(
        db, store, run, final_fingerprint=run.run_fingerprint,
    )
    assert checkpoint.model_call_state == state
    assert checkpoint.progress.model_calls == len(adapter.calls)
    assert checkpoint.progress.records_incomplete > 0
    assert sum(state["lineage_calls"].values()) == len(adapter.calls) + 2


def test_failed_online_reservation_stops_before_request(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="model-call-persistence-failure"
    )
    monkeypatch.setattr(settings, "semantic_ranking_enabled", False)
    adapter = TwoStageAdapter()
    monkeypatch.setattr(execution_service, "configured_model_adapter", lambda: adapter)

    def fail(*_args, **_kwargs):
        raise RuntimeError("reservation storage unavailable")

    monkeypatch.setattr(execution_service, "_persist_model_call_state", fail)
    store, token = _claim(db, run_id)
    with pytest.raises(ModelCallPersistenceFailure):
        execution_service._execute_claimed(db, store, store.get_owned(run_id, "analyst"), token)
    assert adapter.calls == []
    assert store.get_artifact(run_id, "analyst", "recognition-model-calls") is None
    assert store.get_artifact(run_id, "analyst", "recognition_checkpoint") is None


@pytest.mark.parametrize("control", ["cancel", "replace"])
def test_cancelled_model_cannot_write_after_control_revokes_worker(
    client, db, analyst_headers, tmp_path, monkeypatch, control,
):
    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key=f"cancelled-model-{control}"
    )
    store, token = _claim(db, run_id)
    monkeypatch.setattr(execution_service, "_LeaseKeeper", lambda **_kwargs: SimpleNamespace(
        start=lambda: None, stop=lambda: None, raise_if_lost=lambda: None, should_stop=lambda: True,
    ))
    waterline = []

    def revoke_then_interrupt(*_args, **_kwargs):
        if control == "cancel":
            current = store.get_owned(run_id, "analyst")
            store.request_control(
                run_id, "analyst", action="cancel", expected_revision=current.revision,
                request_key="cancel-model-inflight",
            )
            db.commit()
        else:
            _expire_lease(db, run_id)
            _claim(db, run_id)
        current = store.get_owned(run_id, "analyst")
        waterline.append((current.revision, current.event_head, current.artifact_revision,
                          current.execution_status, current.error))
        raise ModelCancelled()

    monkeypatch.setattr(execution_service, "_execute_claimed", revoke_then_interrupt)
    execution_service._execute_dispatched_run(db, store, store.get_owned(run_id, "analyst"), token)
    current = store.get_owned(run_id, "analyst")
    assert (current.revision, current.event_head, current.artifact_revision,
            current.execution_status, current.error) == waterline[0]
    with pytest.raises(FenceViolation):
        store.assert_fence(run_id, "analyst", token)
