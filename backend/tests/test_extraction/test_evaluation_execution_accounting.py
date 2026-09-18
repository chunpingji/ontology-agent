"""Offline execution preserves costs and ownership across interrupted model calls."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect

from app.evaluation import cmc_benchmark
from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.executor import ModelCallPersistenceFailure
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from app.services.llm import model_scheduler, semantic_ranking
from app.services.llm.model_runtime import ModelCancelled, model_scope, runtime
from app.services.llm.model_scheduler import RequestTicket
from tests.test_extraction.test_ontology_guided_core import REPORT, FakeAdapter, ontology, sample
from tests.test_extraction.test_semantic_ranking import RankingModel


def _benchmark(tmp_path, monkeypatch, adapter, ranking=None):
    analysis = sample(tmp_path)
    snapshot = ontology()
    prepared, output = tmp_path / "prepared", tmp_path / "output"
    prepared.mkdir()
    output.mkdir()
    cmc_benchmark.write_json(prepared / "ontology_snapshot.json", snapshot.model_dump(mode="json"))
    cmc_benchmark.write_json(prepared / "ir.json", analysis.ir.model_dump(mode="json"))
    cmc_benchmark.write_json(prepared / "manifest.json", {"fixture": True})
    manifest = {
        "source_filename": "source.docx", "document_hash": analysis.ir.document_hash,
        "ontology_snapshot_id": snapshot.snapshot_id, "class_iri": REPORT,
        "ontology_semantic_hash": snapshot.ontology_hash,
        "ontology_snapshot_file_hash": cmc_benchmark.digest_file(
            prepared / "ontology_snapshot.json"
        ),
        "ir_hash": cmc_benchmark.digest_file(prepared / "ir.json"),
        "runtime_hash": "frozen-runtime",
        "settings": {"word_tree_summary_prompt_version": "test-summary-v1"},
    }
    args = SimpleNamespace(
        mode="quality_guided", timeout=5, timeout_retries=0, deadline_seconds=None,
        pause_after=None, stop_file=None, run_id="private-evaluation", focus_path=None,
        max_sections_per_predicate=1,
    )
    monkeypatch.setattr(cmc_benchmark, "slot_snapshot", lambda: [])
    monkeypatch.setattr(cmc_benchmark, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(model_adapter, "configured_model_adapter", lambda: adapter)
    service = ranking or RankingService(RankingPolicy())
    monkeypatch.setattr(semantic_ranking, "configured_ranking_service", lambda *args, **kwargs: (
        service, {"policy": service.policy.model_dump(mode="json"),
                  "model_identity": service.model_identity},
    ))

    def forbid_shared_database():
        pytest.fail("active evaluation reached the shared scheduler database")

    monkeypatch.setattr(model_scheduler, "default_bind", forbid_shared_database)

    def execute():
        return cmc_benchmark._run_ontology_guided_segment(
            args, prepared, manifest, output, [], analysis, time.perf_counter(), 0.01,
        )

    return output, execute


class ScheduledAdapter(FakeAdapter):
    def __init__(self, output, failure=None):
        self.output, self.failure = output, failure
        self.dispatched = []

    def inspect(self, task, context, predicate, menu):
        for ordinal, stage in enumerate(("propose", "verify"), 1):
            context.before_model_call(stage, ordinal)
            persisted = cmc_benchmark.read_json(self.output / "model_call_state.json")
            reservation = persisted["reservations"][-1]
            assert reservation["task_id"] == task.task_id
            assert reservation["stage"] == stage
            assert runtime.get()["task_id"] == task.task_id
            with model_scope(stage=stage, **({"input_tokens": 13} if ordinal == 1 else {})):
                ticket = RequestTicket("http://isolated.test:8080/v1", task.task_id, ordinal, 5)
                assert ticket.admit()
                ticket.start()
                self.dispatched.append(ticket.request_id)
                if self.failure is not None and ordinal == 2:
                    ticket.finish("cancelled")
                    raise self.failure
                ticket.finish("completed")
        return super().inspect(task, context, predicate, menu).model_copy(update={"model_calls": 2})


def test_active_scheduler_is_private_and_each_request_has_real_task_and_reserved_costs(
    tmp_path, monkeypatch,
):
    adapter = ScheduledAdapter(tmp_path / "output")
    output, execute = _benchmark(tmp_path, monkeypatch, adapter)
    execute()

    requests = cmc_benchmark.read_json(output / "scheduler_requests.json")
    assert len(requests) == len(adapter.dispatched) > 0
    assert {row["run_id"] for row in requests} == {"private-evaluation"}
    assert {row["stage"] for row in requests} == {"propose", "verify"}
    assert all(not row["task_id"].startswith("evaluation:") for row in requests)
    costs = cmc_benchmark.read_json(output / "costs.json")
    assert len(costs["recognition_reserved"]["reservations"]) == len(requests)
    assert costs["scheduler"]["unknown_token_requests"] == len(requests) // 2
    assert costs["scheduler"]["measured_input_tokens"] == len(requests) // 2 * 13
    assert costs["scheduler"]["unattributed_requests"] == 0
    bind = create_engine(f"sqlite:///{output / 'scheduler.sqlite3'}")
    try:
        assert set(inspect(bind).get_table_names()) == {"local_model_pools", "local_model_requests"}
    finally:
        bind.dispose()
    manifest = cmc_benchmark.read_json(output / "result.json")
    assert manifest["execution_status"] == "finished"
    assert manifest["execution_limits"]["max_model_calls_per_record"] >= 2
    for name, frozen_hash in manifest["output_hashes"].items():
        assert cmc_benchmark.digest_file(output / name) == frozen_hash, name


@pytest.mark.parametrize("failure", [ModelCancelled(), KeyboardInterrupt()])
def test_cancellation_and_interrupt_preserve_prepaid_calls_and_do_not_formally_score(
    tmp_path, monkeypatch, failure,
):
    adapter = ScheduledAdapter(tmp_path / "output", failure=failure)
    output, execute = _benchmark(tmp_path, monkeypatch, adapter)
    with pytest.raises(type(failure)):
        execute()

    status = cmc_benchmark.read_json(output / "execution_status.json")
    assert status["status"] == "failed"
    assert status["failure"]["type"] == type(failure).__name__
    costs = cmc_benchmark.read_json(output / "costs.json")
    assert len(costs["recognition_reserved"]["reservations"]) == 2
    assert costs["recognition_model_calls"] is None
    assert costs["scheduler"]["status_counts"] == {"completed": 1, "cancelled": 1}
    assert costs["scheduler"]["unknown_token_requests"] == 1
    assert cmc_benchmark.read_json(output / "result.json")["quality_gate"]["formal_score"] == (
        "not_run"
    )
    cmc_benchmark.score(SimpleNamespace(prepared=str(tmp_path / "prepared"),
                                        run=str(output), reference="missing-reference.json"))
    metrics = cmc_benchmark.read_json(output / "metrics.json")
    assert metrics["status"] == "failed_execution_not_scored"


def test_model_reservation_write_failure_prevents_dispatch(tmp_path, monkeypatch):
    adapter = ScheduledAdapter(tmp_path / "output")
    output, execute = _benchmark(tmp_path, monkeypatch, adapter)
    write_json = cmc_benchmark.write_json

    def fail_reservation(path, value):
        if path.name == "model_call_state.json" and value.get("reservations"):
            raise OSError("test write barrier unavailable")
        return write_json(path, value)

    monkeypatch.setattr(cmc_benchmark, "write_json", fail_reservation)
    with pytest.raises(ModelCallPersistenceFailure):
        execute()
    assert adapter.dispatched == []
    assert cmc_benchmark.read_json(output / "scheduler_requests.json") == []
    assert cmc_benchmark.read_json(output / "model_call_state.json") == {}
    assert cmc_benchmark.read_json(output / "execution_status.json")["status"] == "failed"


def test_ranking_reservation_write_failure_prevents_dispatch(tmp_path, monkeypatch):
    model = RankingModel()
    ranking = RankingService(RankingPolicy(mode="semantic"), model)
    output, execute = _benchmark(tmp_path, monkeypatch, FakeAdapter(), ranking)
    write_json = cmc_benchmark.write_json

    def fail_reservation(path, value):
        if path.name == "ranking_state.json" and value["service"]["costs"]["tokens"]:
            raise OSError("test ranking write barrier unavailable")
        return write_json(path, value)

    monkeypatch.setattr(cmc_benchmark, "write_json", fail_reservation)
    with pytest.raises(OSError, match="test ranking write barrier unavailable"):
        execute()
    assert model.calls == []
    assert cmc_benchmark.read_json(output / "scheduler_requests.json") == []
    state = cmc_benchmark.read_json(output / "ranking.json")
    assert state.get("service", {}).get("costs", {}).get("tokens", 0) == 0
    assert cmc_benchmark.read_json(output / "execution_status.json")["status"] == "failed"


@pytest.mark.parametrize("cancelled", [False, True])
def test_model_cleanup_failure_is_recorded_without_replacing_original_cancellation(
    tmp_path, monkeypatch, cancelled,
):
    class CloseFailure(RankingModel):
        closed = 0

        def close(self):
            self.closed += 1
            raise OSError("test close failure")

    model = CloseFailure()
    adapter = ScheduledAdapter(tmp_path / "output", ModelCancelled() if cancelled else None)
    ranking = RankingService(RankingPolicy(), model)
    output, execute = _benchmark(tmp_path, monkeypatch, adapter, ranking)
    with pytest.raises(ModelCancelled if cancelled else OSError):
        execute()
    assert model.closed == 1
    status = cmc_benchmark.read_json(output / "execution_status.json")
    assert status["status"] == "failed"
    assert status["failure"]["type"] == ("ModelCancelled" if cancelled else "OSError")
    assert cmc_benchmark.read_json(output / "costs.json")["scheduler"]["requests"] > 0
    assert cmc_benchmark.read_json(output / "result.json")["quality_gate"]["formal_score"] == (
        "not_run"
    )


def test_ranking_dispatch_observes_durable_reservation_and_cancellation_keeps_it(
    tmp_path, monkeypatch,
):
    class CancelRanking(RankingModel):
        def embed(self, texts):
            state = cmc_benchmark.read_json(tmp_path / "output" / "ranking_state.json")
            assert state["service"]["costs"]["tokens"] > 0
            with model_scope(stage="semantic_embedding"):
                ticket = RequestTicket("http://isolated.test:8080/v1", "embedding", 1, 5)
                assert ticket.admit()
                ticket.start()
                ticket.finish("cancelled")
            raise ModelCancelled()

    ranking = RankingService(RankingPolicy(mode="semantic"), CancelRanking())
    output, execute = _benchmark(tmp_path, monkeypatch, FakeAdapter(), ranking)
    with pytest.raises(ModelCancelled):
        execute()
    ranking_state = cmc_benchmark.read_json(output / "ranking.json")
    assert ranking_state["service"]["costs"]["tokens"] > 0
    requests = cmc_benchmark.read_json(output / "scheduler_requests.json")
    assert len(requests) == 1
    assert requests[0]["run_id"] == "private-evaluation"
    assert requests[0]["task_id"]
    assert cmc_benchmark.read_json(output / "execution_status.json")["status"] == "failed"


def test_record_call_budget_is_frozen_in_runner_fingerprint(tmp_path):
    analysis = sample(tmp_path)
    metadata = prepare_metadata(analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
                                summary_version="evaluation-accounting")
    fingerprints = []
    for budget in (2, 4):
        runner = build_quality_guided_variant(ontology=ontology(), adapter=FakeAdapter(),
                                              max_model_calls_per_record=budget)
        assert runner.executor.max_model_calls_per_record == budget
        result = runner.run(recognition_run_id="same-run", ir=analysis.ir, metadata=metadata,
                            root_class_iri=REPORT, root_class_label="报告", filename="source.docx")
        fingerprints.append(result.run_fingerprint)
    assert fingerprints[0] != fingerprints[1]


def test_chat_prompt_and_completion_usage_stay_separate_from_ranking_inputs():
    totals = cmc_benchmark.scheduler_token_totals([
        {"metrics": {"input_tokens": 41398}},
        {"metrics": {"prompt_tokens": 4484, "completion_tokens": 12}},
        {"metrics": {"input_tokens": None, "prompt_tokens": 100, "completion_tokens": 7}},
        {"metrics": {"input_tokens": 0, "prompt_tokens": 999, "output_tokens": 0,
                     "completion_tokens": 999}},
        {"metrics": {}},
    ])
    assert totals == {
        "measured_input_tokens": 45982, "unknown_token_requests": 1,
        "measured_output_tokens": 19, "unknown_output_token_requests": 2,
    }


def test_active_run_rejects_preparation_missing_current_frozen_settings(tmp_path, monkeypatch):
    cmc_benchmark.write_json(tmp_path / "manifest.json", {"class_iri": REPORT})
    monkeypatch.setattr(cmc_benchmark, "restore_settings", lambda manifest: [
        "document_analysis_max_model_calls_per_record",
    ])
    args = SimpleNamespace(mode="quality_guided", prepared=str(tmp_path))
    with pytest.raises(ValueError, match="all current settings frozen"):
        cmc_benchmark.run(args)
