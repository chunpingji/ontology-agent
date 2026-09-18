"""Offline artifact and actual process/scheduler boundaries without real weights."""

from __future__ import annotations

import hashlib
import sys
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.llm.model_runtime import ModelCancelled, model_scope
from app.services.llm.semantic_ranking import (
    LocalSemanticRanking,
    RankingModelUnavailable,
    _worker_operation,
    configured_semantic_ranking,
    verify_artifact,
)


def _artifact(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    for filename in ("model.safetensors", "tokenizer.json", "config.json"):
        (root / filename).write_text("{}", encoding="utf-8")
    manifest = tmp_path / f"{name}.sha256"
    manifest.write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(root.iterdir())
    ), encoding="utf-8")
    return str(root), str(manifest)


def _config(tmp_path):
    embedding, embedding_manifest = _artifact(tmp_path, "embedding")
    reranker, reranker_manifest = _artifact(tmp_path, "reranker")
    return {"enabled": True, "mode": "rerank", "embedding_path": embedding,
            "embedding_manifest_path": embedding_manifest, "reranker_path": reranker,
            "reranker_manifest_path": reranker_manifest, "batch_size": 2,
            "max_tokens_per_pair": 32, "timeout_seconds": 10}


def _controlled_worker(connection, config):
    try:
        while True:
            operation, inputs = connection.recv()
            if inputs == "wait":
                time.sleep(20)
            if inputs == "returned_error":
                connection.send({"error": "ranking_input_too_long"})
                continue
            result = len(inputs) if operation == "count_tokens" else [-2.0] * len(inputs)
            connection.send({"result": result, "input_tokens": 3})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()
        if config.get("shutdown_marker"):
            from pathlib import Path

            Path(config["shutdown_marker"]).write_text("worker_cleanup_completed", encoding="utf-8")


def test_factory_is_disabled_without_imports_and_never_loads_weights(tmp_path):
    assert configured_semantic_ranking(None) is None
    assert configured_semantic_ranking({"enabled": False}) is None
    model = configured_semantic_ranking(_config(tmp_path))
    assert model._process is None
    assert model.identity["artifacts"]["reranker"]["tokenizer_sha256"]
    assert model.identity["score_semantics"] == "raw_single_logit"


def test_artifacts_require_complete_hashes_and_reject_mutation_and_symlinks(tmp_path):
    root, manifest = _artifact(tmp_path, "model")
    identity = verify_artifact(root, manifest)
    assert len(identity["manifest_sha256"]) == 64
    from pathlib import Path

    Path(root, "config.json").write_text("changed", encoding="utf-8")
    with pytest.raises(RankingModelUnavailable, match="artifact_invalid"):
        verify_artifact(root, manifest)
    Path(manifest).write_text("", encoding="utf-8")
    with pytest.raises(RankingModelUnavailable, match="artifact_invalid"):
        verify_artifact(root, manifest)
    Path(root, "escape").symlink_to(tmp_path)
    with pytest.raises(RankingModelUnavailable, match="artifact_invalid"):
        verify_artifact(root, manifest)


@pytest.mark.parametrize("field,value", [("timeout_seconds", float("inf")),
                                        ("batch_size", 0), ("max_tokens_per_pair", None)])
def test_unbounded_model_configuration_is_rejected(tmp_path, field, value):
    config = _config(tmp_path)
    config[field] = value
    with pytest.raises(RankingModelUnavailable, match="unbounded_configuration"):
        configured_semantic_ranking(config)


def test_worker_disables_remote_loading_and_preserves_raw_pair_scores(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config["frozen_artifact_identities"] = {
        kind: verify_artifact(config[f"{kind}_path"], config[f"{kind}_manifest_path"])
        for kind in ("embedding", "reranker")
    }
    seen = []

    class Tokenizer:
        model_max_length = 32

        def __call__(self, *inputs, **kwargs):
            assert kwargs == {"truncation": False, "add_special_tokens": True}
            return {"input_ids": list(range(sum(map(len, inputs)) + 2))}

    def tokenizer_loader(path, **kwargs):
        seen.append(("tokenizer", path, kwargs))
        return Tokenizer()

    class Encoder:
        max_seq_length = 32

        def __init__(self, path, **kwargs):
            seen.append(("model", path, kwargs))

        def predict(self, inputs, **kwargs):
            assert kwargs["activation_fn"] == "identity"
            assert kwargs["apply_softmax"] is False
            return SimpleNamespace(tolist=lambda: [-2.5, 1.5])

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer_loader)))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=Encoder, CrossEncoder=Encoder))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        nn=SimpleNamespace(Identity=lambda: "identity")))
    scores, tokens = _worker_operation("score_pairs", [("q", "a"), ("q", "bb")],
                                       config, {}, {})
    assert scores == [-2.5, 1.5] and tokens == 9
    assert all(kwargs["local_files_only"] and not kwargs["trust_remote_code"]
               for _, _, kwargs in seen)
    with pytest.raises(RankingModelUnavailable, match="ranking_input_too_long"):
        _worker_operation("score_pairs", [("q", "a" * 40)], config, {}, {})


@pytest.mark.parametrize("rewrite_manifest", [False, True])
def test_worker_rejects_weights_replaced_between_tokenization_and_lazy_load(
    tmp_path, monkeypatch, rewrite_manifest,
):
    from pathlib import Path

    config = _config(tmp_path)
    config["frozen_artifact_identities"] = {
        kind: verify_artifact(config[f"{kind}_path"], config[f"{kind}_manifest_path"])
        for kind in ("embedding", "reranker")
    }
    models, tokenizers = {}, {}
    loaded = []
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: lambda *args, **kwargs: {"input_ids": [1, 2]},
    )))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        CrossEncoder=lambda *args, **kwargs: loaded.append("reranker"), SentenceTransformer=None,
    ))
    assert _worker_operation("count_tokens", "query", config, models, tokenizers) == (2, 0)
    root = Path(config["reranker_path"])
    (root / "model.safetensors").write_text("different weights", encoding="utf-8")
    if rewrite_manifest:
        Path(config["reranker_manifest_path"]).write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(root.iterdir())
        ), encoding="utf-8")
    with pytest.raises(RankingModelUnavailable, match="ranking_artifact_"):
        _worker_operation("score_pairs", [("query", "record")], config, models, tokenizers)
    assert loaded == [] and models == {}


def test_real_scheduler_observes_process_completion_and_token_cost(tmp_path,
                                                                  isolated_model_scheduler):
    bind = isolated_model_scheduler()
    model = LocalSemanticRanking(_config(tmp_path), worker_target=_controlled_worker)
    try:
        with model_scope(bind=bind):
            assert model.score_pairs([("query", "record")]) == [-2.0]
        assert model.observations[0]["input_tokens"] == 3
        with Session(bind) as db:
            request = db.scalar(select(LocalModelRequest))
            assert request.finished_at is not None and request.started_at is not None
            assert request.metrics["input_count"] == 1
            assert request.stage == "ranking_score_pairs"
    finally:
        model.close()


def test_normal_close_runs_worker_cleanup_and_is_idempotent(tmp_path, isolated_model_scheduler):
    bind = isolated_model_scheduler()
    marker = tmp_path / "normal-shutdown.txt"
    config = {**_config(tmp_path), "shutdown_marker": str(marker)}
    model = LocalSemanticRanking(config, worker_target=_controlled_worker)
    try:
        with model_scope(bind=bind):
            assert model.count_tokens("ready") == 5
        process = model._process
        assert process.is_alive() and not marker.exists()
        model.close()
        assert process.exitcode == 0 and not process.is_alive()
        assert marker.read_text(encoding="utf-8") == "worker_cleanup_completed"
        assert model._process is None and model._connection is None
        finished_at = marker.stat().st_mtime_ns
        model.close()
        assert marker.stat().st_mtime_ns == finished_at
        assert model._process is None and model._connection is None
    finally:
        model.close()


def test_force_close_stops_immediately_without_worker_cleanup(tmp_path, isolated_model_scheduler):
    bind = isolated_model_scheduler()
    marker = tmp_path / "forced-shutdown.txt"
    config = {**_config(tmp_path), "shutdown_marker": str(marker)}
    model = LocalSemanticRanking(config, worker_target=_controlled_worker)
    try:
        with model_scope(bind=bind):
            assert model.count_tokens("ready") == 5
        process = model._process
        started = time.monotonic()
        model.close(force=True)
        assert time.monotonic() - started < 1.5
        assert not process.is_alive() and process.exitcode != 0
        assert not marker.exists()
        assert model._process is None and model._connection is None
        model.close(force=True)
        assert not marker.exists()
    finally:
        model.close()


def test_returned_worker_error_cleans_up_normally_and_preserves_failure(
    tmp_path, isolated_model_scheduler,
):
    bind = isolated_model_scheduler()
    marker = tmp_path / "error-response-shutdown.txt"
    config = {**_config(tmp_path), "shutdown_marker": str(marker)}
    model = LocalSemanticRanking(config, worker_target=_controlled_worker)
    try:
        with model_scope(bind=bind):
            assert model.count_tokens("ready") == 5
            process = model._process
            with pytest.raises(RankingModelUnavailable, match="^ranking_input_too_long$"):
                model.count_tokens("returned_error")
        assert process.exitcode == 0 and not process.is_alive()
        assert marker.read_text(encoding="utf-8") == "worker_cleanup_completed"
        assert model._process is None and model._connection is None
        assert model.observations[-1]["status"] == "failed"
        with Session(bind) as db:
            requests = list(db.scalars(select(LocalModelRequest).order_by(
                LocalModelRequest.sequence)))
            assert [request.status for request in requests] == ["completed", "failed"]
            assert all(request.finished_at is not None for request in requests)
    finally:
        model.close()


def test_cancelled_process_is_stopped_before_scheduler_slot_released(tmp_path,
                                                                    isolated_model_scheduler):
    bind = isolated_model_scheduler()
    model = LocalSemanticRanking(_config(tmp_path), worker_target=_controlled_worker)
    started = time.monotonic()
    with model_scope(bind=bind, should_stop=lambda: time.monotonic() - started > 0.2):
        with pytest.raises(ModelCancelled):
            model.count_tokens("wait")
    assert model._process is None
    with Session(bind) as db:
        request = db.scalar(select(LocalModelRequest))
        assert request.finished_at is not None


def test_timeout_kills_private_worker_and_records_failed_cost(tmp_path, isolated_model_scheduler):
    bind = isolated_model_scheduler()
    config = _config(tmp_path)
    config["timeout_seconds"] = 0.1
    model = LocalSemanticRanking(config, worker_target=_controlled_worker)
    with model_scope(bind=bind), pytest.raises(RankingModelUnavailable, match="ranking_timeout"):
        model.count_tokens("wait")
    assert model._process is None and model.observations[0]["status"] == "failed"


def test_epoch_deadline_narrows_request_timeout_without_mutating_identity(tmp_path):
    model = LocalSemanticRanking(_config(tmp_path), worker_target=_controlled_worker)
    identity = model.identity.copy()
    model.set_deadline(time.monotonic() - 1)
    with pytest.raises(RankingModelUnavailable, match="ranking_timeout"):
        model.count_tokens("text")
    assert model._process is None and model.identity == identity
    model.set_deadline(None)
