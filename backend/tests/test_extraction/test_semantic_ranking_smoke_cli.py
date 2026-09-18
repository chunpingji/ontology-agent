"""CPU smoke CLI isolation and faithful reporting; no production model weights."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.model_request import LocalModelRequest
from app.services.llm.model_runtime import runtime
from app.services.llm.model_scheduler import RequestTicket
from scripts import smoke_semantic_ranking as smoke


def _arguments(tmp_path):
    arguments = ["--output-dir", str(tmp_path / "result")]
    for kind in ("embedding", "reranker"):
        arguments.extend([f"--{kind}-path", str(tmp_path / kind),
                          f"--{kind}-manifest-path", str(tmp_path / f"{kind}.sha256")])
    return arguments


class ControlledRanking:
    """Deterministic domain port, still charging the actual isolated SQL scheduler."""
    identity = {"adapter": "controlled-smoke-test", "score_semantics": "raw_single_logit"}

    def __init__(self, config):
        self.config = config
        self.observations = []

    def _observe(self, operation, result, inputs):
        bind = runtime.get()["bind"]
        assert Path(bind.url.database).name == "scheduler.sqlite3"
        ticket = RequestTicket("http://smoke-controlled:80", uuid4().hex, 0, 30, capacity=1)
        assert ticket.admit()
        ticket.start()
        tokens = 0 if operation == "count_tokens" else sum(len(str(item)) for item in inputs)
        ticket.finish("completed", operation=operation, input_tokens=tokens)
        self.observations.append({"operation": operation, "status": "completed",
                                  "input_tokens": tokens})
        return result

    def count_tokens(self, text):
        return self._observe("count_tokens", len(text), [text])

    def embed(self, texts):
        return self._observe("embed", [[1.0, 0.0] for _ in texts], texts)

    def score_pairs(self, pairs):
        return self._observe("score_pairs", [-3.25 for _ in pairs], pairs)

    def close(self):
        pass


def test_smoke_uses_only_fresh_scheduler_and_replays_without_calls(tmp_path, db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///must-never-be-used.sqlite3")
    args = smoke.parser().parse_args(_arguments(tmp_path))
    report = smoke.run_smoke(args, model_factory=ControlledRanking)
    assert report["status"] == "passed", report.get("failure")
    assert report["scheduler_tables"] == ["local_model_pools", "local_model_requests"]
    assert report["restore"]["extra_scheduler_requests"] == 0
    assert report["ranking"]["actual_ranking_mode"] == "semantic"
    assert report["ranking"]["degraded"] is False
    assert report["ranking"]["records"] == 5
    assert report["ranking"]["observations"] == 10
    assert report["observed_costs"]["measured_input_tokens"] > 0
    assert report["observed_costs"]["unknown_request_count"] == 0
    assert report["probe"]["raw_reranker_scores"] == [-3.25, -3.25]
    assert db.scalar(select(func.count()).select_from(LocalModelRequest)) == 0
    assert os.environ["DATABASE_URL"] == "sqlite:///must-never-be-used.sqlite3"
    assert not Path("must-never-be-used.sqlite3").exists()
    assert (args.output_dir / "synthetic.docx").is_file()
    assert json.loads((args.output_dir / "report.json").read_text())["status"] == "passed"
    with pytest.raises(FileExistsError):
        smoke.run_smoke(args, model_factory=ControlledRanking)


def test_smoke_failure_retains_observed_costs_and_never_reports_success(tmp_path):
    class InvalidVectors(ControlledRanking):
        def embed(self, texts):
            return self._observe("embed", [[2.0, 0.0] for _ in texts], texts)

    args = smoke.parser().parse_args(_arguments(tmp_path))
    report = smoke.run_smoke(args, model_factory=InvalidVectors)
    assert report["status"] == "failed"
    assert report["failure"]["reason"] == "embedding_not_l2_normalized"
    assert report["observed_costs"]["measured_input_tokens"] > 0
    assert "ranking" not in report
    assert "scheduler_requests.json" in report["artifact_sha256"]


def test_cli_help_and_argument_validation_do_not_import_application_or_start_models(tmp_path):
    script = Path(smoke.__file__)
    environment = {**os.environ, "DATABASE_URL": "invalid://must-not-import-app-db"}
    help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True,
                                 text=True, check=False, env=environment)
    assert help_result.returncode == 0
    assert "--long-record-repetitions" in help_result.stdout
    invalid = subprocess.run([sys.executable, str(script), "--output-dir", "relative"],
                             capture_output=True, text=True, check=False, env=environment)
    assert invalid.returncode == 2
    assert "absolute path is required" in invalid.stderr
    assert not (tmp_path / "result").exists()


def test_cli_missing_model_writes_failed_report_in_isolated_directory(tmp_path):
    result = subprocess.run([sys.executable, smoke.__file__, *_arguments(tmp_path)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "failed"
    assert response["failure"]["reason"] == "ranking_artifact_missing"
    report = json.loads(Path(response["report"]).read_text())
    assert report["observed_costs"]["scheduler_requests"] == 0
    assert report["scope"]["gold_quality_evaluation"] is False


def test_length_boundaries_separate_expected_rejections_and_verify_worker_recovery(tmp_path):
    from app.services.llm.semantic_ranking import RankingModelUnavailable

    class LengthLimitedRanking(ControlledRanking):
        def _reject_oversize(self, operation, length):
            if length <= self.config["max_tokens_per_pair"]:
                return
            ticket = RequestTicket("http://smoke-controlled:80", uuid4().hex, 0, 30, capacity=1)
            assert ticket.admit()
            ticket.start()
            ticket.finish("failed", operation=operation, input_tokens=None)
            self.observations.append({"operation": operation, "status": "failed",
                                      "input_tokens": None})
            raise RankingModelUnavailable("ranking_input_too_long")

        def embed(self, texts):
            self._reject_oversize("embed", max(map(len, texts)))
            return super().embed(texts)

        def score_pairs(self, pairs):
            self._reject_oversize(
                "score_pairs", max(len(left) + len(right) for left, right in pairs))
            return super().score_pairs(pairs)

    args = smoke.parser().parse_args([*_arguments(tmp_path), "--length-boundaries"])
    report = smoke.run_smoke(args, model_factory=LengthLimitedRanking)
    assert report["status"] == "passed", report.get("failure")
    boundary = report["length_boundaries"]
    assert boundary["near_limit_tokenizer_max"] >= args.max_tokens_per_pair * 0.95
    assert boundary["oversized_tokenizer_max"] > args.max_tokens_per_pair
    assert len(boundary["expected_rejections"]) == 2
    assert all(item["worker_cleared"] for item in boundary["expected_rejections"])
    assert boundary["recovery"] == [{"operation": "embed", "status": "completed"},
                                     {"operation": "score_pairs", "status": "completed"}]
    assert report["observed_costs"]["expected_rejection_requests"] == 2
    assert report["observed_costs"]["unknown_request_count"] == 2
    assert report["observed_costs"]["unexpected_unknown_request_count"] == 0
    assert report["child_memory"]["is_sum_of_process_memory"] is False
