"""Fixed-pool protocol integrity and cost barriers without loading real models."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.evaluation import fixed_pool_benchmark as fixed
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingPersistenceError,
    RankingPolicy,
    RankingService,
)
from app.services.llm import model_scheduler, semantic_ranking
from app.services.llm.model_runtime import ModelCancelled, model_scope
from app.services.llm.model_scheduler import RequestTicket
from tests.test_extraction.test_evaluation_execution_accounting import _benchmark
from tests.test_extraction.test_ontology_guided_core import FakeAdapter
from tests.test_extraction.test_semantic_ranking import RankingModel


@pytest.fixture
def frozen(tmp_path, monkeypatch, request):
    service = RankingService(RankingPolicy(mode="semantic", batch_size=4), RankingModel())
    output, execute = _benchmark(tmp_path, monkeypatch, FakeAdapter(), service)
    identity = {"policy": service.policy.model_dump(mode="json"),
                "model_identity": service.model_identity, "configuration": {"fixture": True}}
    monkeypatch.setattr(semantic_ranking, "configured_ranking_service", lambda *args, **kwargs: (
        service, identity,
    ))
    execute()
    run = fixed.read_json(output / "run.json")
    epoch_id = run["ranking"]["service"]["epochs"][0]["epoch_id"]
    pool = fixed.export_pool(output, epoch_id, tmp_path / "pool.json")
    reference = {
        "query_id": pool["query_id"],
        "document_hash": pool["manifest"]["shared"]["document_hash"],
        "query_content_hash": pool["manifest"]["fixed_pool"]["query_content_hash"],
        "annotation_complete": True,
        "records": [{"record_id": rid, "grade": 3 if i == 0 else 1,
                     "role": "support" if i == 0 else "context"}
                    for i, rid in enumerate(pool["epoch"]["record_ids"])],
        "expert_review": {"status": "approved", "reviewer": "controlled-test-only",
                          "reviewed_at": "2026-09-09"},
    }
    annotation = getattr(request, "param", "complete")
    if annotation == "partial":
        reference["records"].pop()
        reference["annotation_complete"] = False
    elif annotation == "zero":
        for record in reference["records"]:
            record["grade"] = 0
    fixed.write_json(tmp_path / "reference.json", reference)
    protocol = fixed._seal({
        "schema_version": "semantic-fixed-pool-protocol-v1", "protocol_id": "test-only",
        "pool_hash": pool["content_hash"], "k": 2,
        "rounds": [{"round_id": str(i), "run_ids": {group: f"round-{i}-{group}"
                                                     for group in "ABCD"}}
                   for i in range(1, 4)],
        "reference_sha256": fixed.digest_file(tmp_path / "reference.json"),
        "comparisons": [{"left": "B", "right": "C", "metric": "ndcg_at_k",
                         "minimum_mean_delta": -1.0}],
        "cost_limits": {"reserved_tokens": 100000, "elapsed_seconds": 60},
    })
    # Parallel agents can edit unrelated runtime files during this controlled test.
    monkeypatch.setattr(fixed, "tree_digest", lambda *args: "fixed-test-code-hash")
    return pool, protocol, reference, output, epoch_id


class ObservedModel(RankingModel):
    def __init__(self, output, *, fail=None, malformed=False):
        super().__init__()
        self.output, self.fail, self.malformed = output, fail, malformed
        self.closed = False

    def _observe(self, operation, values, function):
        reservation = fixed.read_json(self.output / "cost_state.json")["reservations"][-1]
        assert reservation["operation"] == operation
        assert reservation["status"] == "reserved"
        with model_scope(stage=f"fixed_pool_{operation}"):
            ticket = RequestTicket("http://isolated-fixed.test/v1", str(uuid4()), 1, 10)
            assert ticket.admit()
            ticket.start()
            if self.fail is not None and operation == "embed":
                ticket.finish("cancelled" if isinstance(self.fail, ModelCancelled) else "failed")
                raise self.fail
            result = function(values)
            ticket.finish("completed", input_tokens=reservation["input_tokens"])
            return result

    def count_tokens(self, text):
        return self._observe("count_tokens", text, super().count_tokens)

    def embed(self, texts):
        return self._observe("embed", texts, super().embed)

    def score_pairs(self, pairs):
        result = self._observe("score_pairs", pairs, super().score_pairs)
        return result[:-1] if self.malformed else result

    def close(self):
        self.closed = True


def _run(tmp_path, pool, protocol, round_id, group, **model_args):
    output = tmp_path / f"fixed-{round_id}-{group}"
    model = ObservedModel(output, **model_args)
    fixed.run_group(pool, protocol, protocol["content_hash"], round_id, group, output,
                    model_factory=lambda config: model)
    return output, model


def test_legacy_fixed_pool_uses_explicit_cpu_defaults_despite_current_gpu(
    frozen, tmp_path, monkeypatch,
):
    pool, protocol, *_ = frozen
    monkeypatch.setattr(settings, "semantic_ranking_device", "cuda:1")
    monkeypatch.setattr(settings, "semantic_ranking_dtype", "float16")
    received = []
    output = tmp_path / "legacy-explicit-cpu"
    model = ObservedModel(output)

    def factory(config):
        received.append(config)
        return model

    fixed.run_group(pool, protocol, protocol["content_hash"], "1", "B", output,
                    model_factory=factory)
    assert received == [{"fixture": True, "device": "cpu", "dtype": "float32",
                         "cuda_version": "12.6"}]
    assert pool["model_configuration"] == {"fixture": True}


@pytest.mark.parametrize("key,value", [
    ("device", "cuda:0"), ("dtype", "float16"), ("cuda_version", "12.8"),
    ("numeric_environment", {"driver": "different-driver", "device_uuid": "different-gpu"}),
])
def test_fixed_pool_numeric_inventory_drift_is_rejected_before_requests(
    frozen, tmp_path, key, value,
):
    pool, protocol, *_ = frozen
    output = tmp_path / "numeric-drift"
    model = ObservedModel(output)
    model.identity = {**model.identity, key: value}
    with pytest.raises(ValueError, match="runtime model differs"):
        fixed.run_group(pool, protocol, protocol["content_hash"], "1", "B", output,
                        model_factory=lambda config: model)
    assert model.calls == []
    assert fixed.read_json(output / "scheduler_requests.json") == []
    assert not (output / "observation.json").exists()


def test_gpu_pool_cannot_omit_frozen_numeric_fields(frozen):
    pool, *_ = frozen
    pool = deepcopy(pool)
    pool["model_identity"] = {"device": "cuda:0", "dtype": "float16", "cuda_version": "12.6"}
    with pytest.raises(ValueError, match="all numeric configuration fields frozen"):
        fixed._frozen_model_configuration(pool)
    pool["model_configuration"].update(device="cpu", dtype="float32", cuda_version="12.6")
    with pytest.raises(ValueError, match="differs from frozen model inventory"):
        fixed._frozen_model_configuration(pool)
    pool["model_configuration"].update(device="cuda:0", dtype="float16")
    with pytest.raises(ValueError, match="frozen numeric runtime inventory"):
        fixed._frozen_model_configuration(pool)


def test_numeric_environment_is_bound_to_the_execution_fingerprint(frozen, tmp_path):
    pool, protocol, *_ = frozen
    output, _model = _run(tmp_path, pool, protocol, "1", "A")
    manifest = fixed.read_json(output / "manifest.json")
    assert manifest["numeric_fingerprint_version"] == 1
    manifest["shared"]["numeric_environment"]["CUDA_VISIBLE_DEVICES"] = "another-device"
    fixed.write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="run fingerprint"):
        fixed.aggregate(protocol, protocol["content_hash"], [output], pool=pool)


def test_export_rejects_corrupt_source_and_pool_content_or_record_drift(frozen, tmp_path):
    pool, protocol, _, source, epoch_id = frozen
    for change in ("query", "view", "pool"):
        drifted = deepcopy(pool)
        if change == "query":
            drifted["epoch"]["queries"][0]["model_text"] += " changed"
        elif change == "view":
            drifted["epoch"]["retrieval_views"][0]["model_text"] += " changed"
        else:
            drifted["epoch"]["record_ids"].pop()
        with pytest.raises(ValueError, match="content hash"):
            fixed.run_group(drifted, protocol, protocol["content_hash"], "1", "A",
                            tmp_path / "rejected")
        assert not (tmp_path / "rejected").exists()
    source_run = fixed.read_json(source / "run.json")
    source_run["run_fingerprint"] = "a" * 64
    fixed.write_json(source / "run.json", source_run)
    with pytest.raises(ValueError, match="finalized output hash"):
        fixed.export_pool(source, epoch_id, tmp_path / "corrupt-pool.json")


def test_three_explicit_rounds_use_same_pool_and_external_annotation_gate(
    frozen, tmp_path, monkeypatch,
):
    pool, protocol, reference, _, _ = frozen

    def forbid_shared():
        pytest.fail("fixed-pool experiment accessed the shared scheduler database")

    monkeypatch.setattr(model_scheduler, "default_bind", forbid_shared)
    outputs = []
    for round_id in ("1", "2", "3"):
        for group in "ABCD":
            output, model = _run(tmp_path, pool, protocol, round_id, group)
            outputs.append(output)
            manifest = fixed.read_json(output / "manifest.json")
            observation = fixed.read_json(output / "observation.json")
            assert manifest["execution"]["status"] == "finished"
            assert observation["pool_record_ids"] == pool["epoch"]["record_ids"]
            assert model.closed is (group != "A")
            for name, frozen_hash in manifest["output_hashes"].items():
                assert fixed.digest_file(output / name) == frozen_hash
        c = fixed.read_json(tmp_path / f"fixed-{round_id}-C" / "observation.json")
        d = fixed.read_json(tmp_path / f"fixed-{round_id}-D" / "observation.json")
        assert c["ranking_record_ids"] == d["ranking_record_ids"]
        assert c["dispatch_and_assembly"] == "not_executed"

    result = fixed.aggregate(protocol, protocol["content_hash"], outputs, pool=pool)
    assert result["runs"] == 12 and result["rounds"] == 3
    assert result["retrieval_protocol_gate"] == "blocked_or_failed"
    assert "missing_expert_reference" in result["gate_reasons"]
    result = fixed.aggregate(protocol, protocol["content_hash"], outputs,
                             pool=pool, reference=tmp_path / "reference.json")
    assert result["retrieval_protocol_gate"] == "pass"  # Explicit test-only threshold.
    assert result["graph_quality_gate"] == "not_evaluated"
    assert len(result["comparisons"][0]["round_deltas"]) == 3

    with pytest.raises(ValueError, match="missing rounds or groups"):
        fixed.aggregate(protocol, protocol["content_hash"], outputs[:-1], pool=pool)
    fixed.write_json(tmp_path / "changed-reference.json", {
        **reference, "annotation_complete": False,
    })
    with pytest.raises(ValueError, match="reference differs"):
        fixed.aggregate(protocol, protocol["content_hash"], outputs,
                        pool=pool, reference=tmp_path / "changed-reference.json")

    target = outputs[-1] / "manifest.json"
    original = fixed.read_json(target)
    for change, error in (("shared", "shared input"), ("nonce", "reused execution"),
                          ("group", "registered meaning"), ("fingerprint", "fingerprint")):
        changed = deepcopy(original)
        if change == "shared":
            changed["shared"]["budget"]["max_tasks"] += 1
        elif change == "nonce":
            changed["execution_nonce"] = fixed.read_json(outputs[0] / "manifest.json")[
                "execution_nonce"]
        elif change == "group":
            changed["factors"]["enable_reranker"] = False
        else:
            changed["run_fingerprint"] = "a" * 64
        fixed.write_json(target, changed)
        with pytest.raises(ValueError, match=error):
            fixed.aggregate(protocol, protocol["content_hash"], outputs, pool=pool)
    fixed.write_json(target, original)

    all_originals = [fixed.read_json(output / "manifest.json") for output in outputs]
    for output, source in zip(outputs, all_originals, strict=True):
        changed = deepcopy(source)
        changed["shared"]["ontology_hash"] = "all-groups-changed-together"
        fixed.write_json(output / "manifest.json", changed)
    with pytest.raises(ValueError, match="registered pool"):
        fixed.aggregate(protocol, protocol["content_hash"], outputs, pool=pool,
                        reference=tmp_path / "reference.json")
    for output, source in zip(outputs, all_originals, strict=True):
        fixed.write_json(output / "manifest.json", source)

    changed = deepcopy(original)
    changed["execution"]["status"] = "failed"
    fixed.write_json(target, changed)
    result = fixed.aggregate(protocol, protocol["content_hash"], outputs,
                             pool=pool, reference=tmp_path / "reference.json")
    assert result["failed_runs"] == [["3", "D"]]
    assert "failed_or_unfinished_executions" in result["gate_reasons"]


@pytest.mark.parametrize("group", ["C", "D"])
def test_reranking_groups_execute_and_charge_the_complete_dense_baseline(frozen, tmp_path, group):
    pool, protocol, *_ = frozen
    baseline_output, baseline_model = _run(tmp_path, pool, protocol, "1", "B")
    output, model = _run(tmp_path, pool, protocol, "1", group)
    baseline = fixed.read_json(baseline_output / "costs.json")
    costs = fixed.read_json(output / "costs.json")

    def completed_inputs(measured, operation):
        return [(item["input_hash"], item["input_count"], item["input_tokens"])
                for item in measured["reservations"]
                if item["operation"] == operation and item["status"] == "completed"]

    embeddings = completed_inputs(costs, "embed")
    assert embeddings == completed_inputs(baseline, "embed")
    expected_inputs = len(pool["epoch"]["record_ids"]) + len(pool["epoch"]["queries"])
    assert sum(count for _, count, _ in embeddings) == expected_inputs > 2
    assert sum(count for operation, count in model.calls if operation == "embed") == expected_inputs
    assert [call for call in model.calls if call[0] == "embed"] == baseline_model.calls

    reranker_pairs = completed_inputs(costs, "score_pairs")
    expected_pairs = len(pool["epoch"]["record_ids"]) * len(pool["epoch"]["queries"])
    assert sum(count for _, count, _ in reranker_pairs) == expected_pairs
    assert sum(count for operation, count in model.calls if operation == "score") == expected_pairs
    assert costs["reserved_tokens"] == baseline["reserved_tokens"] + sum(
        tokens for _, _, tokens in reranker_pairs
    )
    requests = fixed.read_json(output / "scheduler_requests.json")
    assert sum(row["stage"] == "fixed_pool_embed" and row["status"] == "completed"
               for row in requests) == len(embeddings)


@pytest.mark.parametrize("frozen", ["partial", "zero"], indirect=True)
def test_unresolved_or_undefined_reference_metrics_cannot_pass_a_complete_protocol(
    frozen, tmp_path,
):
    pool, protocol, reference, *_ = frozen
    outputs = [_run(tmp_path, pool, protocol, round_id, group)[0]
               for round_id in ("1", "2", "3") for group in "ABCD"]
    result = fixed.aggregate(protocol, protocol["content_hash"], outputs,
                             pool=pool, reference=tmp_path / "reference.json")
    reason = ("incomplete_or_unresolved_annotation" if not reference["annotation_complete"]
              else "undefined_or_unscored_paired_metric")
    assert reason in result["gate_reasons"]
    assert result["retrieval_protocol_gate"] == "blocked_or_failed"
    assert result["graph_quality_gate"] == "not_evaluated"


@pytest.mark.parametrize("failure", [ModelCancelled(), RuntimeError("controlled failure")])
def test_failed_model_request_keeps_reserved_tokens_and_scheduler_rows(
    frozen, tmp_path, failure,
):
    pool, protocol, *_ = frozen
    with pytest.raises(type(failure)):
        _run(tmp_path, pool, protocol, "1", "B", fail=failure)
    output = tmp_path / "fixed-1-B"
    manifest = fixed.read_json(output / "manifest.json")
    costs = fixed.read_json(output / "costs.json")
    assert manifest["execution"]["status"] == "failed"
    assert not (output / "observation.json").exists()
    assert costs["reserved_tokens"] > 0
    failed = [item for item in costs["reservations"] if item["operation"] == "embed"]
    assert len(failed) == (1 if isinstance(failure, ModelCancelled) else 2)
    assert all(item["status"] == "failed" for item in failed)
    requests = fixed.read_json(output / "scheduler_requests.json")
    assert all(row["run_id"] == "round-1-B" for row in requests)
    assert any(row["status"] in ("failed", "cancelled") for row in requests)


def test_pre_call_write_failure_prevents_retry_or_model_dispatch(frozen, tmp_path, monkeypatch):
    pool, protocol, *_ = frozen
    original = fixed.write_json

    def unavailable(path, value):
        if Path(path).name == "cost_state.json":
            raise OSError("controlled write barrier failure")
        return original(path, value)

    monkeypatch.setattr(fixed, "write_json", unavailable)
    with pytest.raises(RankingPersistenceError):
        _run(tmp_path, pool, protocol, "1", "C")
    output = tmp_path / "fixed-1-C"
    assert fixed.read_json(output / "scheduler_requests.json") == []
    costs = fixed.read_json(output / "costs.json")
    assert len(costs["reservations"]) == 1
    assert costs["reservations"][0]["status"] == "reserved"


@pytest.mark.parametrize("boundary", ["cancelled", "deadline"])
def test_deterministic_baseline_honors_cancellation_and_deadline(
    frozen, tmp_path, monkeypatch, boundary,
):
    pool, protocol, *_ = frozen
    if boundary == "deadline":
        clock = iter([0.0, 1000.0])
        monkeypatch.setattr(fixed.time, "monotonic", lambda: next(clock, 1000.0))
    with model_scope(should_stop=lambda: boundary == "cancelled"):
        with pytest.raises(ModelCancelled if boundary == "cancelled" else TimeoutError):
            _run(tmp_path, pool, protocol, "1", "A")
    output = tmp_path / "fixed-1-A"
    assert not (output / "observation.json").exists()
    assert fixed.read_json(output / "manifest.json")["execution"]["status"] == "failed"
    assert fixed.read_json(output / "scheduler_requests.json") == []


def test_incomplete_reranker_batch_fails_entire_pool_without_deterministic_fallback(
    frozen, tmp_path,
):
    pool, protocol, *_ = frozen
    with pytest.raises(ValueError, match="incomplete batch"):
        _run(tmp_path, pool, protocol, "1", "C", malformed=True)
    output = tmp_path / "fixed-1-C"
    assert fixed.read_json(output / "manifest.json")["execution"]["status"] == "failed"
    assert not (output / "observation.json").exists()


def test_protocol_requires_three_complete_rounds_and_unique_planned_run_ids(frozen):
    _, protocol, *_ = frozen
    for change in ("round", "group", "run_id"):
        raw = {key: deepcopy(value) for key, value in protocol.items() if key != "content_hash"}
        if change == "round":
            raw["rounds"].pop()
        elif change == "group":
            raw["rounds"][0]["run_ids"].pop("D")
        else:
            raw["rounds"][1]["run_ids"]["A"] = raw["rounds"][0]["run_ids"]["A"]
        invalid = fixed._seal(raw)
        with pytest.raises(ValueError):
            fixed.validate_protocol(invalid, invalid["content_hash"])


def test_cli_freezes_protocol_and_runs_baseline_without_loading_models(frozen, tmp_path, capsys):
    pool, protocol, *_ = frozen
    draft = {key: value for key, value in protocol.items() if key != "content_hash"}
    fixed.write_json(tmp_path / "protocol.draft.json", draft)
    fixed.main(["freeze-protocol", "--input", str(tmp_path / "protocol.draft.json"),
                "--output", str(tmp_path / "protocol.json")])
    assert protocol["content_hash"] in capsys.readouterr().out
    fixed.main(["run", "--pool", str(tmp_path / "pool.json"),
                "--protocol", str(tmp_path / "protocol.json"),
                "--protocol-hash", protocol["content_hash"], "--round-id", "1", "--group", "A",
                "--output", str(tmp_path / "cli-A")])
    costs = fixed.read_json(tmp_path / "cli-A" / "costs.json")
    assert costs["reserved_tokens"] == 0 and costs["scheduler"]["requests"] == 0
    assert pool["content_hash"] == fixed.read_json(tmp_path / "cli-A" / "manifest.json")[
        "pool_content_hash"]
