"""Cold artifact revalidation keeps admission ownership before worker startup."""

from __future__ import annotations

import hashlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.llm import model_scheduler
from app.services.llm import semantic_ranking as ranking
from app.services.llm.model_runtime import ModelCancelled, model_scope
from tests.test_extraction.test_semantic_ranking_adapter import _config, _controlled_worker


def _large_config(tmp_path):
    config = {**_config(tmp_path), "timeout_seconds": 120}
    for kind in ("embedding", "reranker"):
        root = Path(config[f"{kind}_path"])
        (root / "model.safetensors").write_bytes(b"complete local weights\n" * 200_000)
        Path(config[f"{kind}_manifest_path"]).write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(root.iterdir())
        ), encoding="utf-8")
    return config


def _slow_hash_reads(monkeypatch, *, seconds_per_chunk):
    clock = [0.0]
    stamp = model_scheduler.now()
    consumed = Counter()
    original_open = Path.open

    class SlowRead:
        def __init__(self, source, path):
            self.source, self.path = source, path

        def __enter__(self):
            self.source.__enter__()
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def read(self, size):
            data = self.source.read(size)
            if data:
                consumed[str(self.path)] += len(data)
                clock[0] += seconds_per_chunk
            return data

    def open_path(path, *args, **kwargs):
        source = original_open(path, *args, **kwargs)
        if path.name == "model.safetensors" and args and args[0] == "rb":
            return SlowRead(source, path)
        return source

    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr(ranking.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(model_scheduler, "now", lambda: stamp + timedelta(seconds=clock[0]))
    return clock, consumed


def test_background_cold_hashing_longer_than_lease_renews_before_spawn(
    tmp_path, monkeypatch, isolated_model_scheduler,
):
    config = _large_config(tmp_path)
    model = ranking.LocalSemanticRanking(config, worker_target=_controlled_worker)
    identity = deepcopy(model.identity)
    bind = isolated_model_scheduler()
    clock, consumed = _slow_hash_reads(monkeypatch, seconds_per_chunk=8)
    renewals = []
    original_heartbeat = model_scheduler.RequestTicket.heartbeat

    def heartbeat(ticket):
        original_heartbeat(ticket)
        renewals.append((clock[0], model._process is None))

    monkeypatch.setattr(model_scheduler.RequestTicket, "heartbeat", heartbeat)
    try:
        with model_scope(bind=bind), ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(copy_context().run, model.count_tokens, "ready").result(timeout=10)
        assert result == 5
        assert clock[0] > model_scheduler.LEASE_SECONDS
        assert len([stamp for stamp, before_spawn in renewals if before_spawn]) >= 5
        for kind in ("embedding", "reranker"):
            path = Path(config[f"{kind}_path"]) / "model.safetensors"
            assert consumed[str(path)] == path.stat().st_size
        assert model.identity == identity
        with Session(bind) as db:
            request = db.scalar(select(LocalModelRequest))
            assert request.status == "completed"
            assert request.stage == "ranking_count_tokens"
            assert request.finished_at <= request.lease_expires_at
    finally:
        model.close()


@pytest.mark.parametrize("interruption", ["cancel", "deadline", "lease_lost"])
def test_cold_hashing_interrupts_before_spawn_and_preserves_failed_request(
    tmp_path, monkeypatch, isolated_model_scheduler, interruption,
):
    config = _large_config(tmp_path)
    if interruption == "deadline":
        config["timeout_seconds"] = 20
    model = ranking.LocalSemanticRanking(config, worker_target=_controlled_worker)
    identity = deepcopy(model.identity)
    bind = isolated_model_scheduler()
    clock, consumed = _slow_hash_reads(
        monkeypatch, seconds_per_chunk=31 if interruption == "lease_lost" else 8,
    )
    exception = {
        "cancel": ModelCancelled,
        "deadline": ranking.RankingModelUnavailable,
        "lease_lost": model_scheduler.ModelSlotLost,
    }[interruption]
    with model_scope(
        bind=bind, should_stop=lambda: interruption == "cancel" and clock[0] >= 16,
    ):
        with pytest.raises(exception) as raised:
            model.count_tokens("never sent")
    if interruption == "deadline":
        assert str(raised.value) == "ranking_timeout"
    assert model._process is None and model._connection is None
    assert model.identity == identity
    assert 0 < sum(consumed.values()) < sum(
        (Path(config[f"{kind}_path"]) / "model.safetensors").stat().st_size
        for kind in ("embedding", "reranker")
    )
    with Session(bind) as db:
        request = db.scalar(select(LocalModelRequest))
        assert request.status == "failed" and request.finished_at is not None
        assert request.metrics["input_tokens"] is None
        assert request.metrics["worker_metrics"] == {}


def test_progress_callback_does_not_weaken_complete_artifact_validation(tmp_path):
    config = _large_config(tmp_path)
    root = Path(config["embedding_path"])
    manifest = config["embedding_manifest_path"]
    original = ranking.verify_artifact(str(root), manifest)
    checkpoints = []
    assert ranking.verify_artifact(
        str(root), manifest, progress_hook=lambda: checkpoints.append(True),
    ) == original
    assert len(checkpoints) > 5
    with (root / "model.safetensors").open("r+b") as source:
        source.seek(-1, 2)
        source.write(b"changed final byte")
    with pytest.raises(ranking.RankingModelUnavailable, match="ranking_artifact_invalid"):
        ranking.verify_artifact(str(root), manifest, progress_hook=lambda: None)


def test_spawn_failure_releases_pipe_and_ticket_without_masking_original_error(
    tmp_path, monkeypatch, isolated_model_scheduler,
):
    model = ranking.LocalSemanticRanking(_config(tmp_path), worker_target=_controlled_worker)
    context = ranking.multiprocessing.get_context("spawn")
    pipes = []

    class FailedStart:
        pid = None

        def start(self):
            raise RuntimeError("controlled spawn failure")

        def join(self, **_kwargs):
            pytest.fail("an unstarted process cannot be joined")

    def pipe():
        pair = context.Pipe()
        pipes.extend(pair)
        return pair

    monkeypatch.setattr(ranking.multiprocessing, "get_context", lambda _method: SimpleNamespace(
        Pipe=pipe, Process=lambda **_kwargs: FailedStart(),
    ))
    bind = isolated_model_scheduler()
    with model_scope(bind=bind), pytest.raises(RuntimeError, match="^controlled spawn failure$"):
        model.count_tokens("never sent")
    assert model._process is None and model._connection is None
    assert all(connection.closed for connection in pipes)
    with Session(bind) as db:
        request = db.scalar(select(LocalModelRequest))
        assert request.status == "failed" and request.finished_at is not None
