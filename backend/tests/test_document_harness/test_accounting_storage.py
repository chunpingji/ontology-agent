"""Atomic, bounded accounting and current display/recovery contracts."""

import pytest
from sqlalchemy import event, select

from app.models.document_analysis import (
    DocumentRunCurrentState,
    DocumentRunRequest,
    DocumentRunResult,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    HeadConflict,
    content_hash,
)
from app.services.document_harness.accounting import empty_metrics, update_business_metrics
from app.services.document_harness.application import HarnessError, source_payload
from app.services.document_harness.projection import graph_response, read_graph_bundle
from app.services.document_harness.runtime import Repository, get_row
from tests.test_extraction.test_document_harness_runtime import _run
from tests.test_extraction.test_document_run_execution_postgresql import (
    pg_engine as pg_engine,  # noqa: F401 — explicit disposable PostgreSQL only
)


def call(repo, payload=None):
    payload = payload or {"input": "example"}
    policy = source_payload(repo.db, repo.run)["policy"]
    key = content_hash({"stage": "discover", "payload": payload, "schema": {}, "policy": policy})
    return key, repo.begin_attempt(key, "discover", payload, {}, policy)


def answer(usage=None):
    return {"output": {}, "raw_response": {"id": "paid"}, "usage": usage or {}, "error": None}


def test_partial_usage_unknown_dimensions_and_duplicate_finish_are_exact(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    key, attempt = call(repo)
    cost = graph_response(db, run)["progress"]["stage_costs"][0]
    assert cost["calls"] == 1 and cost["input_tokens"] is None and cost["output_tokens"] is None
    repo.finish_attempt(key, attempt, answer({"prompt_tokens": 12, "output_tokens": True}), 250001)
    cost = graph_response(db, run)["progress"]["stage_costs"][0]
    assert cost["seconds"] == .250001 and cost["input_tokens"] == 12
    assert cost["output_tokens"] is None and cost["unmeasured_attempts"] == 0
    before = repo.metrics()
    repo.finish_attempt(key, attempt, answer({"input_tokens": 99}), 1000000)
    assert repo.metrics() == before and repo.metrics()["completed_calls"] == 1
    assert get_row(db, run, "calls", key, model=DocumentRunResult)["usage"]["prompt_tokens"] == 12


def test_retry_keeps_earlier_unknown_usage_and_counts_measured_failed_duration(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    key, attempt = call(repo)
    repo.finish_attempt(key, attempt, {"transport_failure": True, "error": "TimeoutError"}, 2000000)
    assert graph_response(db, run)["observations"][0]["reason"] == "TimeoutError"
    _, next_attempt = call(repo)
    repo.finish_attempt(key, next_attempt, answer({"input_tokens": 7, "output_tokens": 3}), 500000)
    assert graph_response(db, run)["progress"]["stage_costs"][0] == {
        "stage": "discover", "calls": 2, "seconds": 2.5, "input_tokens": None,
        "output_tokens": None, "unmeasured_attempts": 0,
    }
    assert not graph_response(db, run)["observations"]
    assert repo.metrics()["completed_calls"] == 1


def test_running_attempt_replaced_without_measurement_remains_unknown(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    key, previous = call(repo)
    _, attempt = call(repo)
    with pytest.raises(HeadConflict, match="attempt_changed"):
        repo.finish_attempt(key, previous, answer(), 1)
    repo.finish_attempt(key, attempt, answer({"input_tokens": 2, "output_tokens": 1}), 200)
    row = repo.get_call_record(key)
    assert row["attempts"] == 2 and row["unmeasured_attempts"] == 1
    assert row["unknown_input"] == row["unknown_output"] == 1


def test_business_counter_removal_and_proof_gate_do_not_count_document_root():
    metrics = empty_metrics()
    for domain, row in (("entities", {"role": "document_root", "state": "accepted"}),
                        ("relations", {"state": "accepted", "verification": {"method": "rule"}}),
                        ("properties", {"state": "unresolved", "verification": {"method": "llm"}})):
        metrics = update_business_metrics(metrics, domain, None, row)
    assert metrics["candidate_count"] == 2 and metrics["fact_count"] == 1
    assert metrics["rule_verified_count"] == 1 and metrics["llm_verified_count"] == 0
    metrics = update_business_metrics(metrics, "relations", {
        "state": "accepted", "verification": {"method": "rule"},
    }, None)
    assert metrics["candidate_count"] == 1 and metrics["rule_verified_count"] == 0


def test_failed_display_write_rolls_back_business_metrics_and_head(db, monkeypatch):
    run, token = _run(db)
    repo = Repository(db, run, token)
    revision, metrics = run.revision, repo.metrics()
    def fail():
        raise RuntimeError("disk")
    monkeypatch.setattr(repo, "_write_display", fail)
    with pytest.raises(RuntimeError, match="disk"):
        repo.save({"entities": {"e": {"id": "e", "state": "accepted"}}})
    assert get_row(db, run, "entities", "e") is None and repo.metrics() == metrics
    assert repo.store.get_owned(run.recognition_run_id, run.owner_id).revision == revision


def test_call_finish_rolls_back_result_accounting_and_display_together(db, monkeypatch):
    run, token = _run(db)
    repo = Repository(db, run, token)
    key, attempt = call(repo)
    metrics = repo.metrics()
    def fail(**_):
        raise RuntimeError("commit")
    monkeypatch.setattr(repo, "progress", fail)
    with pytest.raises(RuntimeError, match="commit"):
        repo.finish_attempt(key, attempt, answer({"input_tokens": 2}), 500)
    assert repo.get_call_record(key)["status"] == "running"
    assert get_row(db, run, "calls", key, model=DocumentRunResult) is None
    assert repo.metrics() == metrics


def test_prepared_batch_persists_exact_members_and_cached_answer_without_dispatch(db, monkeypatch):
    from app.services.document_harness import model

    run, token = _run(db)
    repo = Repository(db, run, token)
    repo.save({"work": {"w": {"id": "w", "kind": "property_alignment", "status": "ready",
                              "dependency_hash": "h"}}})
    target = {"window_id": "window", "step": "property_alignment",
              "targets": [{"domain": "work", "id": "w", "dependency_hash": "h"}],
              "alias_bindings": {"C1": "w"}, "source_bindings": {"S1": ["source", 0, 4]}}
    batch = repo.prepare_batch("discover", {"sources": [{"text": "text"}]}, {}, target)
    assert repo.get_call_record(batch["call_key"])["status"] == "prepared"
    assert repo.metrics()["stages"] == {}
    assert repo.load()["work"]["w"]["last_call_key"] == batch["call_key"]
    with pytest.raises(HeadConflict, match="capacity_exceeded"):
        repo.prepare_batch("discover", {"sources": []}, {}, target)
    policy = source_payload(db, run)["policy"]
    request = repo.batch_request(batch)
    attempt = repo.begin_attempt(batch["call_key"], batch["stage"], request["payload"], {}, policy)
    repo.finish_attempt(batch["call_key"], attempt, answer({"input_tokens": 1}), 100)
    metrics = repo.metrics()
    monkeypatch.setattr(
        model, "call_model", lambda *_: pytest.fail("paid request dispatched again"),
    )
    resumed = Repository(db, run, token)
    assert resumed.invoke_prepared(batch) == {} and resumed.metrics() == metrics
    assert resumed.load()["cursor"]["main"]["active_batches"][batch["batch_id"]] == batch
    resumed.save({"cursor": {"main": {"active_batches": {}}}, "work": {
        "w": {**resumed.load()["work"]["w"], "status": "done"},
    }})
    assert resumed.metrics()["work_counts"]["done"] == 1


def test_private_cursor_and_cost_changes_leave_display_version_unchanged(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    base = get_row(db, run, "display", "graph")
    repo.save({"cursor": {"main": {"active_batches": {}, "stage": "discover"}}})
    key, attempt = call(repo)
    repo.finish_attempt(key, attempt, answer(), 1)
    assert get_row(db, run, "display", "graph") == base
    assert run.work_version == base["work_version"]
    assert graph_response(db, run)["progress"]["completed_calls"] == 1


def test_missing_display_returns_retryable_409_without_writes(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    display = db.get(DocumentRunCurrentState,
                     (run.recognition_run_id, "harness:display", content_hash("graph")))
    db.delete(display)
    db.commit()
    revision = run.revision
    with pytest.raises(HarnessError) as failure:
        graph_response(db, run)
    assert failure.value.code == "HARNESS_DISPLAY_NOT_READY"
    assert failure.value.status_code == 409 and failure.value.retryable
    assert not db.new and not db.dirty and not db.deleted
    assert repo.store.get_owned(run.recognition_run_id, run.owner_id).revision == revision


def test_graph_bundle_is_one_select_and_call_accounting_does_not_scan_ledger(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    policy = source_payload(db, run)["policy"]
    for index in range(80):
        repo._prepared_row(str(index), "discover", {"large": "x" * 1000}, {}, policy)
    db.commit()
    queries = []
    def record(_connection, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    run_id, owner_id = run.recognition_run_id, run.owner_id
    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        read_graph_bundle(db, run_id, owner_id)
        assert len(queries) == 1
        queries.clear()
        key, attempt = call(repo)
        repo.finish_attempt(key, attempt, answer(), 1)
        calls = [query for query in queries if "FROM document_analysis_requests" in query]
        assert calls and all("request_key =" in query for query in calls)
        assert not any("count(" in query.lower() for query in queries)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)


def test_call_checksum_detects_accounting_tamper_and_stale_worker_cannot_finish(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    key, attempt = call(repo)
    row = db.scalar(select(DocumentRunRequest).where(DocumentRunRequest.domain == "harness:calls"))
    row.call_input_tokens = 99
    db.commit()
    with pytest.raises(ValueError, match="hash_mismatch"):
        repo.get_call_record(key)
    with pytest.raises(FenceViolation):
        Repository(db, run, "wrong-token").finish_attempt(key, attempt, answer(), 1)


def test_paid_batch_missing_judgment_is_saved_as_failed_without_redispatch(db, monkeypatch):
    from app.services.document_harness import model
    from app.services.document_harness.runtime import HarnessCallFailed

    run, token = _run(db)
    calls = []
    monkeypatch.setattr(model, "call_model", lambda *_: calls.append(1) or {
        **answer({"input_tokens": 3, "output_tokens": 2}),
        "output": {"judgments": {}, "type_concerns": []},
    })
    repo = Repository(db, run, token)
    payload = {"candidates": [{"id": "R1"}, {"id": "T1"}]}
    for _ in range(2):
        with pytest.raises(HarnessCallFailed, match="evidence_review_answer_set_mismatch"):
            repo.invoke("evidence_review", payload, {})
    assert calls == [1] and repo.metrics()["completed_calls"] == 0
    assert (graph_response(db, run)["observations"][0]["reason"]
            == "evidence_review_answer_set_mismatch")


def test_limited_regions_and_work_quota_counters_are_current_not_cumulative(db):
    run, token = _run(db)
    repo = Repository(db, run, token)
    observation = {"id": "region", "label": "区域", "reason": "候选范围受限",
                   "weak_pool_truncated": True, "evidence": []}
    work = {"id": "w", "kind": "property_alignment", "status": "pruned"}
    repo.save({"observations": {"region": observation}, "work": {"w": work}})
    assert graph_response(db, run)["progress"]["candidate_scope_limited"]
    repo.save({"observations": {"region": {**observation, "weak_pool_truncated": False}},
               "work": {"w": {**work, "status": "done"}}})
    assert not graph_response(db, run)["progress"]["candidate_scope_limited"]
    assert repo.metrics()["limited_scope_count"] == 0
    assert repo.metrics()["work_counts"]["done"] == 1
    repo.save({"work": {"w": None}})
    assert sum(repo.metrics()["work_counts"].values()) == 0


def test_mismatched_display_and_cross_owner_do_not_serve_cached_data(db):
    from app.services.document_analysis.run_store import RunNotFound

    run, _ = _run(db)
    with pytest.raises(RunNotFound):
        read_graph_bundle(db, run.recognition_run_id, "someone-else")
    row = db.get(DocumentRunCurrentState,
                 (run.recognition_run_id, "harness:display", content_hash("graph")))
    row.payload = {"key": "graph", "value": {**row.payload["value"], "work_version": 99}}
    row.content_hash = content_hash(row.payload)
    db.commit()
    with pytest.raises(HarnessError, match="展示结果尚未就绪"):
        graph_response(db, run)


def test_accounting_migration_preserves_old_rows_and_rejects_negative_counts():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import IntegrityError

    path = Path(__file__).parents[2] / "alembic/versions/0044_harness_call_accounting.py"
    spec = importlib.util.spec_from_file_location("harness_accounting_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE document_analysis_requests (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO document_analysis_requests (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(text(
                "SELECT call_attempts FROM document_analysis_requests WHERE id = 1"
            )).scalar_one() is None
            with pytest.raises(IntegrityError):
                connection.execute(text(
                    "UPDATE document_analysis_requests SET call_attempts = -1 WHERE id = 1"
                ))
            migration.downgrade()
        assert [column["name"] for column in inspect(connection).get_columns(
            "document_analysis_requests",
        )] == ["id"]
    engine.dispose()


def test_pair_ranking_reuses_independent_result_and_never_inflates_llm_costs(db, monkeypatch):
    from app.models.document_analysis import DocumentAnalysisArtifact
    from app.services.llm import semantic_ranking

    run, token = _run(db)
    artifact = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    payload = {**artifact.payload, "policy": {**artifact.payload["policy"], "card_ranking": {
        **artifact.payload["policy"]["card_ranking"],
        "semantic": {"enabled": True, "mode": "rerank", "batch_size": 2},
    }}}
    artifact.payload = payload
    db.commit()
    calls = []
    class Ranker:
        observations = []
        def score_pairs(self, pairs):
            calls.append(pairs)
            return [.2] * len(pairs)
        def close(self):
            pass
    monkeypatch.setattr(semantic_ranking, "configured_semantic_ranking", lambda _: Ranker())
    repo = Repository(db, run, token)
    pairs = [("first", "candidate"), ("second", "candidate"), ("third", "candidate")]
    assert repo.rank_pairs(pairs) == [.2, .2, .2]
    version = repo.run.ranking_version
    assert Repository(db, run, token).rank_pairs(pairs) == [.2, .2, .2]
    assert len(calls) == 2 and repo.run.ranking_version == version
    assert not repo.metrics()["stages"] and repo.metrics()["completed_calls"] == 0
    repo.close()


def test_foreign_source_in_paid_output_is_failed_and_never_applied_or_reissued(db, monkeypatch):
    from app.services.document_harness import model
    from app.services.document_harness.protocols import stage_schema
    from app.services.document_harness.runtime import HarnessCallFailed

    run, token = _run(db)
    count = []
    monkeypatch.setattr(model, "call_model", lambda *_: count.append(1) or {
        **answer({"input_tokens": 1, "output_tokens": 1}),
        "output": {"judgments": {"R1": {
            "verdict": "accepted", "confidence": .99, "evidence": ["S9"], "reason": "claimed",
        }}, "type_concerns": []},
    })
    schema = stage_schema("evidence_review", source_ids=["S1"], candidate_ids=["R1"])
    repo = Repository(db, run, token)
    for _ in range(2):
        with pytest.raises(HarnessCallFailed, match="harness_output_schema_mismatch"):
            repo.invoke("evidence_review", {"candidates": [{"id": "R1"}]}, schema)
    assert count == [1] and repo.metrics()["completed_calls"] == 0
    assert not repo.load().get("relations")


def test_postgresql_read_bundle_sees_atomic_metrics_and_display_publication(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(pg_engine, expire_on_commit=False) as db:
        run, token = _run(db)
        run_id, owner_id, before_revision = run.recognition_run_id, run.owner_id, run.revision
    prepared, release = Event(), Event()

    def publish():
        with Session(pg_engine, expire_on_commit=False) as db:
            run = DocumentAnalysisRunStore(db).get_owned(run_id, owner_id)
            repo = Repository(db, run, token)
            original = repo._write_display
            def blocked_display():
                original()
                prepared.set()
                assert release.wait(5)
            repo._write_display = blocked_display
            repo.save({"entities": {"e": {
                "id": "e", "label": "Example", "role": "object", "class_iri": "urn:example:Report",
                "class_label": "Report", "state": "accepted", "reason": "source", "evidence": [],
            }}})

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publish)
        try:
            assert prepared.wait(5)
            with Session(pg_engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                head, metrics, base, _ = read_graph_bundle(db, run_id, owner_id)
                assert head.revision == before_revision and head.work_version == 0
                assert metrics["candidate_count"] == metrics["fact_count"] == 0
                assert base["entities"] == []
        finally:
            release.set()
        future.result(timeout=5)
    with Session(pg_engine) as db:
        head, metrics, base, _ = read_graph_bundle(db, run_id, owner_id)
        assert head.work_version == 1 and head.revision > before_revision
        assert metrics["candidate_count"] == metrics["fact_count"] == 1
        assert base["entities"][0]["id"] == "e"


def test_postgresql_model_wait_releases_fence_and_duplicate_finish_is_noop(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.services.document_harness import model
    from app.services.document_harness.protocols import stage_schema
    from tests.test_extraction.test_document_harness_runtime import _discovery

    with Session(pg_engine, expire_on_commit=False) as db:
        run, token = _run(db)
        run_id, owner_id = run.recognition_run_id, run.owner_id
    entered, release = Event(), Event()
    calls = []
    def model_call(*_):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {**answer({"input_tokens": 8, "output_tokens": 2}), "output": _discovery()}
    monkeypatch.setattr(model, "call_model", model_call)
    schema = stage_schema("discover")
    def invoke():
        with Session(pg_engine) as db:
            run = DocumentAnalysisRunStore(db).get_owned(run_id, owner_id)
            Repository(db, run, token).invoke("discover", {}, schema)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        try:
            assert entered.wait(5)
            with Session(pg_engine) as db:
                db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                DocumentAnalysisRunStore(db).heartbeat(run_id, owner_id, token)
                db.commit()
                _, metrics, _, _ = read_graph_bundle(db, run_id, owner_id)
                assert metrics["completed_calls"] == 0
                assert metrics["stages"]["discover"]["attempts"] == 1
                assert metrics["stages"]["discover"]["unknown_input"] == 1
        finally:
            release.set()
        future.result(timeout=5)
    with Session(pg_engine) as db:
        run = DocumentAnalysisRunStore(db).get_owned(run_id, owner_id)
        repo = Repository(db, run, token)
        policy = source_payload(db, run)["policy"]
        key = content_hash({"stage": "discover", "payload": {}, "schema": schema, "policy": policy})
        before, version = repo.metrics(), run.request_version
        repo.finish_attempt(key, 1, answer({"input_tokens": 1000}), 1000000)
        assert repo.metrics() == before and run.request_version == version
        assert before["completed_calls"] == 1
        assert before["stages"]["discover"]["input_tokens"] == 8
        assert repo.get_call_record(key)["status"] == "completed"
    assert calls == [1]


def test_postgresql_concurrent_finish_accounts_one_attempt_once(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    with Session(pg_engine, expire_on_commit=False) as db:
        run, token = _run(db)
        run_id, owner_id = run.recognition_run_id, run.owner_id
        repo = Repository(db, run, token)
        key, attempt = call(repo)
        request_version = repo.run.request_version
    gate = Barrier(2)
    def finish():
        with Session(pg_engine) as db:
            run = DocumentAnalysisRunStore(db).get_owned(run_id, owner_id)
            gate.wait(timeout=5)
            Repository(db, run, token).finish_attempt(
                key, attempt, answer({"input_tokens": 5, "output_tokens": 2}), 200,
            )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    with Session(pg_engine) as db:
        run = DocumentAnalysisRunStore(db).get_owned(run_id, owner_id)
        metrics = Repository(db, run, token).metrics()
        assert run.request_version == request_version + 1
        assert metrics["completed_calls"] == 1
        assert metrics["stages"]["discover"]["duration_us"] == 200
        assert metrics["stages"]["discover"]["input_tokens"] == 5
