"""CUDA selection, runtime drift and cleanup without requiring a GPU in CI."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from app.services.llm import semantic_ranking as ranking
from app.services.llm.model_runtime import ModelCancelled, model_scope
from tests.test_extraction.test_semantic_ranking_adapter import _config


def _cuda_config(tmp_path):
    return {**_config(tmp_path), "device": "cuda:2", "dtype": "float16", "cuda_version": "12.6"}


@pytest.mark.parametrize("overrides,reason", [
    ({"device": "cuda"}, "ranking_invalid_device"),
    ({"device": "cuda:-1"}, "ranking_invalid_device"),
    ({"device": "mps"}, "ranking_invalid_device"),
    ({"device": "cpu", "dtype": "float16"}, "ranking_invalid_dtype"),
    ({"device": "cuda:0", "dtype": "bfloat16"}, "ranking_invalid_dtype"),
    ({"device": "cuda:0", "cuda_version": "13.0"}, "ranking_invalid_cuda_version"),
    ({"device": "cuda:0", "cuda_version": "12.8"}, "ranking_invalid_cuda_version"),
])
def test_invalid_numeric_configuration_never_probes_or_loads(
    tmp_path, monkeypatch, overrides, reason,
):
    monkeypatch.setattr(ranking, "_probe_device", lambda config: pytest.fail("CUDA probe"))
    with pytest.raises(ranking.RankingModelUnavailable, match=f"^{reason}$"):
        ranking.configured_semantic_ranking({**_config(tmp_path), **overrides})


def test_cpu_default_stays_lazy_and_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(ranking, "_probe_device", lambda config: pytest.fail("CUDA probe"))
    model = ranking.configured_semantic_ranking(_config(tmp_path))
    assert model.identity["device"] == "cpu"
    assert model.identity["dtype"] == "float32"
    assert model.identity["cuda_version"] is None
    assert model._process is None


@pytest.mark.parametrize("field,value", [
    ("device", "cuda:1"), ("dtype", "float32"), ("cuda_version", "12.8"),
    ("numeric_environment", {"device_uuid": "different-GPU"}),
    ("packages", {"torch": "different-build"}),
])
def test_recovery_rejects_numeric_environment_changes(tmp_path, monkeypatch, field, value):
    monkeypatch.setattr(ranking, "_probe_device", lambda config: {"device_uuid": "GPU-original"})
    config = _cuda_config(tmp_path)
    model = ranking.configured_semantic_ranking(config)
    assert model.identity["cuda_version"] == "12.6" and model._process is None
    state = RankingService(RankingPolicy(), model).snapshot()
    restored_model = ranking.configured_semantic_ranking(config)
    restored_model.identity[field] = value
    with pytest.raises(ValueError, match="model identity changed"):
        RankingService(RankingPolicy(), restored_model, state=state)


def test_runtime_configuration_cannot_change_after_freeze(tmp_path):
    model = ranking.configured_semantic_ranking(_config(tmp_path))
    model.config["device"] = "cuda:0"
    with pytest.raises(ranking.RankingModelUnavailable, match="ranking_configuration_changed"):
        model.count_tokens("never sent to model")
    assert model._process is None


def _fake_torch(*, version="12.6", available=True, architectures=("sm_60",), kernel_value=4096):
    class Tensor:
        def __matmul__(self, other):
            return self

        def sum(self):
            return self

        def item(self):
            return kernel_value

    return SimpleNamespace(
        version=SimpleNamespace(cuda=version), float16="float16", float32="float32",
        ones=lambda *args, **kwargs: Tensor(),
        cuda=SimpleNamespace(
            is_available=lambda: available, device_count=lambda: 4,
            set_device=lambda index: None, get_arch_list=lambda: list(architectures),
            get_device_properties=lambda index: SimpleNamespace(
                major=6, minor=0, name="P100", uuid="GPU-original", total_memory=16 * 1024**3),
            synchronize=lambda index: None, empty_cache=lambda: None,
        ),
        backends=SimpleNamespace(
            cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
            cudnn=SimpleNamespace(allow_tf32=True, benchmark=True, version=lambda: 90501),
        ),
    )


@pytest.mark.parametrize("torch_options,reason", [
    ({"version": None}, "ranking_cuda_version_mismatch"),
    ({"version": "13.0"}, "ranking_cuda_version_mismatch"),
    ({"available": False}, "ranking_cuda_unavailable"),
    ({"architectures": ("sm_70", "sm_80")}, "ranking_cuda_architecture_unsupported"),
    ({"kernel_value": 0}, "ranking_cuda_kernel_failed"),
])
def test_cuda_availability_alone_is_not_a_compatibility_check(monkeypatch, torch_options, reason):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(**torch_options))
    with pytest.raises(ranking.RankingModelUnavailable, match=f"^{reason}$"):
        ranking._cuda_identity({"device": "cuda:2", "dtype": "float16", "cuda_version": "12.6"})


def test_cuda_probe_records_hardware_and_disables_implicit_precision_changes(monkeypatch):
    torch = _fake_torch()
    monkeypatch.setitem(sys.modules, "torch", torch)
    identity = ranking._cuda_identity(
        {"device": "cuda:2", "dtype": "float16", "cuda_version": "12.6"})
    assert identity["device_uuid"] == "GPU-original"
    assert identity["capability"] == [6, 0] and identity["cuda"] == "12.6"
    assert identity["attention"] == "eager"
    assert torch.backends.cuda.matmul.allow_tf32 is False
    assert torch.backends.cudnn.allow_tf32 is False and torch.backends.cudnn.benchmark is False
    with pytest.raises(ranking.RankingModelUnavailable, match="ranking_cuda_device_missing"):
        ranking._cuda_identity(
            {"device": "cuda:4", "dtype": "float16", "cuda_version": "12.6"})


def _waiting_probe(connection, config):
    time.sleep(20)


def test_probe_deadline_and_cancellation_stop_disposable_process():
    config = {"timeout_seconds": 0.1}
    started = time.monotonic()
    with pytest.raises(ranking.RankingModelUnavailable, match="ranking_cuda_probe_timeout"):
        ranking._probe_device(config, target=_waiting_probe)
    assert time.monotonic() - started < 3
    started = time.monotonic()
    with model_scope(should_stop=lambda: time.monotonic() - started > 0.1):
        with pytest.raises(ModelCancelled):
            ranking._probe_device({"timeout_seconds": 30}, target=_waiting_probe)
    assert time.monotonic() - started < 3


def _oom_worker(connection, config):
    connection.recv()
    connection.send({"error": "ranking_cuda_out_of_memory"})
    try:
        connection.recv()
    except EOFError:
        pass
    finally:
        connection.close()


def test_gpu_oom_is_failed_cleans_worker_and_releases_scheduler(
    tmp_path, monkeypatch, isolated_model_scheduler,
):
    monkeypatch.setattr(ranking, "_probe_device", lambda config: {"cuda": "12.6"})
    model = ranking.LocalSemanticRanking(_cuda_config(tmp_path), worker_target=_oom_worker)
    bind = isolated_model_scheduler()
    with model_scope(bind=bind):
        with pytest.raises(ranking.RankingModelUnavailable, match="ranking_cuda_out_of_memory"):
            model.score_pairs([("query", "record")])
    assert model._process is None and model._connection is None
    assert model.identity["device"] == "cuda:2"
    with Session(bind) as db:
        row = db.scalar(select(LocalModelRequest))
        assert row.status == "failed" and row.finished_at is not None


def test_cuda_error_vocabulary_does_not_export_provider_text(monkeypatch):
    class OutOfMemoryError(RuntimeError):
        pass

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(OutOfMemoryError=OutOfMemoryError)))
    assert ranking._execution_error(OutOfMemoryError("private source and paths")) == (
        "ranking_cuda_out_of_memory")
    assert ranking._execution_error(RuntimeError("private source and paths")) == (
        "ranking_model_execution_failed")


@pytest.mark.parametrize("actual_device", ["cuda:2", "cpu"])
def test_gpu_model_load_is_explicit_and_wrong_placement_cannot_return_scores(
    tmp_path, monkeypatch, actual_device,
):
    config = _cuda_config(tmp_path)
    config["frozen_artifact_identities"] = {
        kind: ranking.verify_artifact(config[f"{kind}_path"], config[f"{kind}_manifest_path"])
        for kind in ("embedding", "reranker")
    }
    seen = []

    class Encoder:
        max_length = 32

        def __init__(self, path, **kwargs):
            assert kwargs["device"] == "cuda:2"
            assert kwargs["model_kwargs"] == {
                "torch_dtype": "float16", "attn_implementation": "eager"}
            assert kwargs["local_files_only"] and not kwargs["trust_remote_code"]

        def parameters(self):
            return [SimpleNamespace(device=actual_device, dtype="torch.float16")]

        def predict(self, inputs, **kwargs):
            assert kwargs["activation_fn"] == "identity" and not kwargs["apply_softmax"]
            seen.append(inputs)
            return SimpleNamespace(tolist=lambda: [-2.5])

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=Encoder, CrossEncoder=Encoder))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        nn=SimpleNamespace(Identity=lambda: "identity")))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda *args, **kwargs:
                                     lambda *args, **kwargs: {"input_ids": [1, 2, 3]})))
    if actual_device == "cpu":
        with pytest.raises(
            ranking.RankingModelUnavailable, match="ranking_model_placement_mismatch",
        ):
            ranking._worker_operation("score_pairs", [("query", "record")], config, {}, {})
        assert seen == []
    else:
        assert ranking._worker_operation(
            "score_pairs", [("query", "record")], config, {}, {}) == ([-2.5], 3)
        assert config["_request_metrics"]["actual_device"] == "cuda:2"
        assert config["_request_metrics"]["actual_dtype"] == "float16"


@pytest.mark.parametrize("failure_policy", ["pause", "deterministic"])
def test_gpu_failure_keeps_reason_and_never_publishes_partial_semantic_pool(
    tmp_path, failure_policy,
):
    from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot

    class CudaFailure(RankingModel):
        identity = {"device": "cuda:2", "dtype": "float16"}

        def score_pairs(self, pairs):
            raise ranking.RankingModelUnavailable("ranking_cuda_out_of_memory")

    args = setup_slot(tmp_path)
    model = CudaFailure()
    service = RankingService(RankingPolicy(
        mode="semantic", failure_policy=failure_policy, technical_retry_limit=0), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.reason == "ranking_cuda_out_of_memory"
    assert epoch.actual_ranking_mode == "deterministic" and epoch.degraded
    assert all(item["raw_rerank_score"] is None for item in epoch.observations)
    assert epoch.costs["model_calls"] > 0 and epoch.costs["tokens"] > 0
    assert all(item.coverage_state == "unattempted" for item in args["plan"].ledger.values())
    if failure_policy == "pause":
        assert epoch.status == "paused" and not service.epochs
    else:
        assert epoch.ordered_record_ids == epoch.record_ids
        assert service.commit_epoch(epoch).actual_ranking_mode == "deterministic"


def test_environment_change_before_spawn_cannot_use_frozen_identity(tmp_path, monkeypatch):
    model = ranking.configured_semantic_ranking(_config(tmp_path))
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    with pytest.raises(ranking.RankingModelUnavailable, match="ranking_environment_changed"):
        model.count_tokens("must not run")
    assert model._process is None


def test_legacy_alignment_does_not_auto_select_ranking_gpu(monkeypatch):
    from app.services.extraction.semantic import SentenceTransformerEmbedder

    executed_on = []

    class AutoDeviceEncoder:
        def __init__(self, name, *, device=None):
            self.device = device or "cuda:0"

        def encode(self, texts, **kwargs):
            executed_on.append(self.device)
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=AutoDeviceEncoder))
    embedder = SentenceTransformerEmbedder("local-alignment-model")
    assert embedder.is_available()
    assert embedder.embed("合成实体") == [1.0, 0.0]
    assert executed_on == ["cpu"]
