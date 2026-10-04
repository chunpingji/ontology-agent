"""Two model transports share one fenced coordinator, never a business Session."""

from copy import deepcopy
from threading import Barrier, Event, get_ident

import pytest
from sqlalchemy.orm import Session

from app.services.document_harness import model
from app.services.document_harness.protocols import stage_schema
from app.services.document_harness.runtime import HarnessCallFailed, Repository, get_row
from app.services.llm.local_client import StructuredModelError
from app.services.llm.model_runtime import model_scope, runtime
from tests.test_extraction.test_document_harness_runtime import _discovery, _run
from tests.test_extraction.test_document_run_execution_postgresql import pg_engine as pg_engine


def prepare(repo, window, text):
    return repo.prepare_batch("discover", {"sources": [{"source_id": "S1", "text": text}]},
                              stage_schema("discover", source_ids=["S1"], field_ids=[]), {
        "window_id": window, "step": "plain", "targets": [{"domain": "windows", "id": window,
        "dependency_hash": "input"}], "alias_bindings": {},
        "source_bindings": {"S1": [window, 0, len(text)]},
    })


def repository(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    repo.save({"cursor": {"main": {"phase": "discovery", "active_batches": {},
                                    "stage": "discover"}}})
    return repo


def result():
    return {"output": _discovery(), "error": None, "usage": {
        "input_tokens": 11, "output_tokens": 7,
    }, "raw_response": {}}


def exercise_coordinator(db, monkeypatch):
    repo, gate = repository(db), Barrier(2)
    coordinator, worker_ids, contexts = get_ident(), [], []

    def transport(stage, payload, schema, policy):
        worker_ids.append(get_ident())
        contexts.append(runtime.get().copy())
        assert get_ident() != coordinator
        gate.wait(timeout=5)
        return result()

    monkeypatch.setattr(model, "call_model", transport)
    original = repo.finish_attempt

    def finish(*args):
        assert get_ident() == coordinator
        return original(*args)

    monkeypatch.setattr(repo, "finish_attempt", finish)
    batches = [prepare(repo, "a", "alpha"), prepare(repo, "b", "beta")]
    lost = Event()
    with model_scope(run_id=str(repo.run.recognition_run_id), bind=db.get_bind(),
                     should_stop=lost.is_set):
        for batch in batches:
            repo.submit_prepared(batch)
        outcomes = []
        while len(outcomes) < 2:
            remaining = [b for b in batches if b["batch_id"] not in {
                row[0]["batch_id"] for row in outcomes
            }]
            outcomes.extend(repo.collect_prepared(remaining))
    assert len(set(worker_ids)) == 2
    assert all(c["stage"] == "discover" and c["run_id"] == str(repo.run.recognition_run_id)
               and c["bind"] is db.get_bind() and c["should_stop"] == lost.is_set for c in contexts)
    assert all(output == _discovery() and error is None for _, output, error in outcomes)
    assert len(repo.load()["cursor"]["main"]["active_batches"]) == 2
    metrics = deepcopy(repo.metrics())
    for batch in batches:
        record = repo.get_call_record(batch["call_key"])
        assert record["attempts"] == 1 and record["status"] == "completed"
        # Duplicate completion events cannot account the same attempt twice.
        repo.finish_attempt(batch["call_key"], 1, result(), 100)
    assert repo.metrics() == metrics
    assert metrics["completed_calls"] == 2
    repo.close()


def test_two_transports_save_usage_on_the_coordinator(db, monkeypatch):
    exercise_coordinator(db, monkeypatch)


def test_postgresql_two_transports_save_usage_on_the_coordinator(pg_engine, monkeypatch):
    with Session(pg_engine, expire_on_commit=False) as db:
        exercise_coordinator(db, monkeypatch)


def test_same_request_uses_one_transport_and_two_application_targets(db, monkeypatch):
    repo, calls = repository(db), []
    monkeypatch.setattr(model, "call_model", lambda *args: calls.append(args) or result())
    a, b = prepare(repo, "a", "same"), prepare(repo, "b", "same")
    assert a["call_key"] == b["call_key"] and a["batch_id"] != b["batch_id"]
    repo.submit_prepared(a)
    repo.submit_prepared(b)
    outcomes = repo.collect_prepared([a, b])
    assert len(outcomes) == 2 and len(calls) == 1
    cursor = repo.load()["cursor"]["main"]
    del cursor["active_batches"][a["batch_id"]]
    repo.save({"cursor": {"main": cursor}})
    assert repo.load()["cursor"]["main"]["active_batches"] == {b["batch_id"]: b}
    assert repo.invoke_prepared(b) == _discovery() and len(calls) == 1
    assert repo.metrics()["completed_calls"] == 1
    repo.close()


def test_failure_is_saved_and_sibling_is_collected_before_reporting(db, monkeypatch):
    repo, gate = repository(db), Barrier(2)

    def transport(stage, payload, schema, policy):
        gate.wait(timeout=5)
        answer = result()
        if payload["sources"][0]["text"] == "bad":
            answer["output"] = {"invalid": True}
        return answer

    monkeypatch.setattr(model, "call_model", transport)
    batches = [prepare(repo, "a", "bad"), prepare(repo, "b", "valid")]
    for batch in batches:
        repo.submit_prepared(batch)
    repo.settle_prepared()
    assert not repo._flights
    assert repo.call_result(batches[1]["call_key"]) == _discovery()
    with pytest.raises(HarnessCallFailed):
        repo.call_result(batches[0]["call_key"])
    assert all(repo.get_call_record(b["call_key"])["attempts"] == 1 for b in batches)
    assert len(repo.load()["cursor"]["main"]["active_batches"]) == 2
    repo.close()


def test_pause_prevents_transport_retry_but_retains_started_attempt(db, monkeypatch):
    repo, pause = repository(db), Event()

    def transport(*args):
        pause.set()
        raise StructuredModelError("model_request_failed")

    monkeypatch.setattr(model, "call_model", transport)
    monkeypatch.setattr(repo, "should_stop", pause.is_set)
    batch = prepare(repo, "a", "retry")
    repo.submit_prepared(batch)
    outcomes = repo.collect_prepared([batch])
    assert isinstance(outcomes[0][2], StructuredModelError)
    assert repo.get_call_record(batch["call_key"])["attempts"] == 1
    assert get_row(db, repo.run, "cursor", "main")["active_batches"]
    repo.close()


def test_async_transport_retains_three_attempt_limit_and_unknown_usage(db, monkeypatch):
    repo, calls = repository(db), []

    def transport(*args):
        calls.append(args)
        if len(calls) < 3:
            raise StructuredModelError("model_stream_incomplete")
        return result()

    monkeypatch.setattr(model, "call_model", transport)
    batch = prepare(repo, "a", "retry")
    repo.submit_prepared(batch)
    outcomes = []
    while not outcomes:
        outcomes = repo.collect_prepared([batch])
    assert len(calls) == 3 and outcomes[0][2] is None
    record = repo.get_call_record(batch["call_key"])
    assert record["attempts"] == 3 and record["status"] == "completed"
    assert record["unknown_input"] == record["unknown_output"] == 2
    repo.close()


def test_old_execution_policy_requires_a_new_run_without_state_conversion(db):
    from app.models.document_analysis import DocumentAnalysisArtifact
    from app.services.document_harness.application import HarnessError, require_current_flow

    run, token = _run(db)
    artifact = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    artifact.payload = {**artifact.payload, "policy": {"protocol": "document-harness-v4"}}
    db.flush()
    with pytest.raises(HarnessError, match="请新建运行"):
        require_current_flow(db, run)
    assert artifact.payload["policy"] == {"protocol": "document-harness-v4"}

    from app.services.document_harness.runtime import execute_claimed

    db.commit()
    execute_claimed(db, run, token)
    db.refresh(run)
    assert run.execution_status == "failed"
    assert run.error["code"] == "HARNESS_NEW_RUN_REQUIRED"
    assert "请新建运行" in run.error["message"]
    assert run.error["retryable"] is False


def test_local_publication_and_exact_batch_ack_roll_back_together(db, monkeypatch):
    repo = repository(db)
    a, b = prepare(repo, "a", "alpha"), prepare(repo, "b", "beta")
    cursor = repo.load()["cursor"]["main"]
    del cursor["active_batches"][a["batch_id"]]

    def fail():
        raise RuntimeError("display_save_failed")

    monkeypatch.setattr(repo, "_write_display", fail)
    with pytest.raises(RuntimeError, match="display_save_failed"):
        repo.save({"cursor": {"main": cursor}, "observations": {"local": {
            "id": "local", "label": "原字段", "kind": "field", "evidence": [], "reason": "待核验",
        }}})
    assert set(repo.load()["cursor"]["main"]["active_batches"]) == {a["batch_id"], b["batch_id"]}
    assert not repo.load().get("observations")
    repo.close()


def test_graph_get_during_two_running_calls_is_read_only(db, client, analyst_headers, monkeypatch):
    repo, started, release, calls = repository(db), Barrier(3), Event(), []
    cursor = repo.load()["cursor"]["main"]
    repo.save({"cursor": {"main": {**cursor, "reading_windows": {
        "total": 2, "saved": 0, "complete": 0, "incomplete": 0, "active": 0,
    }}}})

    def transport(*args):
        calls.append(args)
        started.wait(timeout=5)
        assert release.wait(5)
        return result()

    monkeypatch.setattr(model, "call_model", transport)
    batches = [prepare(repo, "a", "alpha"), prepare(repo, "b", "beta")]
    try:
        for batch in batches:
            repo.submit_prepared(batch)
        started.wait(timeout=5)
        version = repo.run.revision
        endpoint = f"/api/document-analysis/runs/{repo.run.recognition_run_id}/harness-graph"
        for _ in range(3):
            response = client.get(endpoint, headers=analyst_headers)
            assert response.status_code == 200, response.text
            assert response.json()["progress"]["reading_windows"]["active"] == 2
            assert response.json()["progress"]["phase"] == "discovery"
        assert repo.current().revision == version and len(calls) == 2
        repo.db.commit()
    finally:
        release.set()
        repo.settle_prepared()
        repo.close()


def test_graph_new_run_required_is_a_public_conflict(db, client, analyst_headers, monkeypatch):
    from app.services.document_harness import projection
    from app.services.document_harness.application import HarnessError

    repo = repository(db)
    revision = repo.run.revision

    def require_new_run(*_):
        raise HarnessError("HARNESS_NEW_RUN_REQUIRED", "该运行使用旧阅读协议，请新建运行")

    monkeypatch.setattr(projection, "graph_response", require_new_run)
    try:
        response = client.get(
            f"/api/document-analysis/runs/{repo.run.recognition_run_id}/harness-graph",
            headers=analyst_headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "HARNESS_NEW_RUN_REQUIRED"
        assert repo.current().revision == revision
    finally:
        repo.close()
