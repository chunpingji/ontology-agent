"""Numeric ranking controls are validated and frozen without loading GPU models."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.evaluation import cmc_benchmark
from app.evaluation.semantic_ranking_evaluation import (
    build_ablation_manifest,
    validate_ablation_pair,
)

NUMERIC_DEFAULTS = {
    "semantic_ranking_device": "cpu",
    "semantic_ranking_dtype": "float32",
    "semantic_ranking_cuda_version": "12.6",
}


def test_application_defaults_select_gpu_with_bounded_capacity(monkeypatch):
    for key in (
        *NUMERIC_DEFAULTS, "semantic_ranking_batch_size", "semantic_ranking_timeout_seconds",
        "semantic_ranking_failure_policy", "document_analysis_dispatch_concurrency",
    ):
        monkeypatch.delenv(key.upper(), raising=False)
    configured = Settings(_env_file=None)
    assert configured.semantic_ranking_device == "cuda:0"
    assert configured.semantic_ranking_dtype == "float16"
    assert configured.semantic_ranking_cuda_version == "12.6"
    assert configured.semantic_ranking_batch_size == 4
    assert configured.semantic_ranking_timeout_seconds == 1200
    assert configured.semantic_ranking_failure_policy == "pause"
    assert configured.document_analysis_dispatch_concurrency == 1


@pytest.mark.parametrize("device,dtype", [
    ("cpu", "float32"), ("cuda:0", "float32"), ("cuda:1", "float16"),
])
def test_settings_accept_explicit_supported_numeric_configuration(device, dtype):
    configured = Settings(
        _env_file=None, semantic_ranking_device=device, semantic_ranking_dtype=dtype,
        semantic_ranking_cuda_version="12.6",
    )
    assert configured.semantic_ranking_device == device
    assert configured.semantic_ranking_dtype == dtype
    assert configured.semantic_ranking_cuda_version == "12.6"


@pytest.mark.parametrize("changed", [
    {"semantic_ranking_device": "cuda"}, {"semantic_ranking_device": "cuda:-1"},
    {"semantic_ranking_device": "cuda:00"}, {"semantic_ranking_device": "mps"},
    {"semantic_ranking_device": "cuda:0 "}, {"semantic_ranking_device": "CUDA:0"},
    {"semantic_ranking_dtype": "float16"}, {"semantic_ranking_dtype": "float64"},
    {"semantic_ranking_cuda_version": "12.8"},
])
def test_settings_reject_unpinned_or_incompatible_numeric_configuration(changed):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**NUMERIC_DEFAULTS, **changed})


def test_numeric_environment_values_are_frozen_at_preparation(monkeypatch):
    for key, value in {
        "semantic_ranking_device": "cuda:1", "semantic_ranking_dtype": "float16",
        "semantic_ranking_cuda_version": "12.6",
    }.items():
        monkeypatch.setenv(key.upper(), value)
    configured = Settings(_env_file=None)
    for key in NUMERIC_DEFAULTS:
        monkeypatch.setattr(settings, key, getattr(configured, key))
    frozen = cmc_benchmark.settings_snapshot()
    assert {key: frozen[key] for key in NUMERIC_DEFAULTS} == {
        "semantic_ranking_device": "cuda:1", "semantic_ranking_dtype": "float16",
        "semantic_ranking_cuda_version": "12.6",
    }


def test_legacy_missing_numeric_settings_never_inherit_current_gpu(monkeypatch):
    frozen = cmc_benchmark.settings_snapshot()
    for key in NUMERIC_DEFAULTS:
        frozen.pop(key)
        monkeypatch.setattr(settings, key, getattr(settings, key))
    monkeypatch.setattr(settings, "semantic_ranking_device", "cuda:1")
    monkeypatch.setattr(settings, "semantic_ranking_dtype", "float16")
    missing = cmc_benchmark.restore_settings({"settings": frozen})
    assert set(missing) == set(NUMERIC_DEFAULTS)
    assert {key: getattr(settings, key) for key in NUMERIC_DEFAULTS} == NUMERIC_DEFAULTS


def test_active_evaluation_rejects_legacy_numeric_preparation_before_calls(tmp_path, monkeypatch):
    frozen = cmc_benchmark.settings_snapshot()
    for key in NUMERIC_DEFAULTS:
        frozen.pop(key)
        monkeypatch.setattr(settings, key, getattr(settings, key))
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    cmc_benchmark.write_json(prepared / "manifest.json", {"settings": frozen})
    output = tmp_path / "run"
    args = cmc_benchmark.parser().parse_args([
        "run", "--prepared", str(prepared), "--output", str(output),
        "--mode", "quality_guided",
    ])
    monkeypatch.setattr(cmc_benchmark, "validate_prepared", lambda *a, **k: pytest.fail(
        "unfrozen numeric settings reached execution preparation"
    ))
    with pytest.raises(ValueError, match="all current settings frozen"):
        cmc_benchmark.run(args)
    assert not output.exists()


def test_ablation_numeric_configuration_and_runtime_are_preserved():
    from types import SimpleNamespace

    model = {
        "device": "cuda:0", "dtype": "float16", "cuda_version": "12.6",
        "numeric_environment": {"device_uuid": "controlled-gpu", "driver": "test-driver"},
    }
    measured = {
        "ranking_identity": {
            "configuration": {"device": "cuda:0", "dtype": "float16", "cuda_version": "12.6"},
            "model_identity": model,
            "policy": {"mode": "semantic", "enable_dense": True,
                       "enable_reranker": True, "phase_interleaving": True},
        },
        "document_hash": "doc", "ontology_semantic_hash": "ontology",
        "input_hashes": {"runtime": "core"}, "metadata_dependency_hash": "metadata",
        "scope": {"mode": "document_graph"}, "execution_limits": {},
        "effective_settings": {}, "completion": "incomplete",
    }
    result = build_ablation_manifest(measured, SimpleNamespace(
        run_fingerprint="controlled-run", model_identity="recognition-model",
    ))
    assert result["shared"]["ranking_numeric_configuration"] == {
        "device": "cuda:0", "dtype": "float16", "cuda_version": "12.6",
    }
    assert all(result["factors"]["numeric_runtime"][key] == value for key, value in model.items())
    model["execution"] = {"cpu_threads": {"OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"}}
    threaded = build_ablation_manifest(measured, SimpleNamespace(
        run_fingerprint="controlled-run", model_identity="recognition-model",
    ))
    assert threaded["factors"]["numeric_runtime"]["cpu_threads"] == {
        "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
    }
    with pytest.raises(ValueError, match="unregistered factor"):
        validate_ablation_pair(result, threaded, allowed_factor_changes=[], fixed_pool=False)
    changed = deepcopy(result)
    changed["shared"]["ranking_numeric_configuration"]["dtype"] = "float32"
    with pytest.raises(ValueError, match="shared input"):
        validate_ablation_pair(result, changed, allowed_factor_changes=[], fixed_pool=False)
    changed = deepcopy(result)
    changed["factors"]["numeric_runtime"]["numeric_environment"]["driver"] = "changed"
    with pytest.raises(ValueError, match="unregistered factor"):
        validate_ablation_pair(result, changed, allowed_factor_changes=[], fixed_pool=False)
