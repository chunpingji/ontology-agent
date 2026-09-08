"""Harness wiring/observation tests; never call a live model."""

import json
from unittest.mock import Mock

import pytest

from app.evaluation import cmc_benchmark
from app.services.extraction.extraction_tasks import ExtractionRun
from tests.test_extraction.test_evaluation_cmc_gliner_timing import experiment  # noqa: F401


@pytest.mark.parametrize("mode", ["root_guided", "root_guided_summary"])
def test_root_modes_use_new_scheduler_and_persist_limits_and_snapshots(
    experiment, monkeypatch, mode,  # noqa: F811
):
    from app.evaluation import root_guided_variant

    env = experiment
    env.args.mode = mode
    env.args.deadline_seconds = 600
    env.args.max_sections_per_predicate = 2

    def run(*args, **kwargs):
        value = ExtractionRun(input_id="fixture", completion="incomplete")
        kwargs["snapshot_fn"](value)
        runner.plan["visited"] = ["root"]
        return value

    runner = Mock(
        budget=Mock(model_dump=Mock(return_value={"max_tasks": 24})),
        plan={"policy_version": "fixture-root"}, variant_statistics={"menus": [1]},
        run=Mock(side_effect=run),
    )
    builder = Mock(return_value=runner)
    monkeypatch.setattr(root_guided_variant, "build_root_guided_variant", builder)
    cmc_benchmark._run_segment(env.args, env.prepared, env.manifest, env.output, [])
    result = cmc_benchmark.read_json(env.output / "result.json")
    assert result["execution_limits"]["deadline_seconds"] == 600
    assert result["execution_limits"]["pause_after"] == 24
    assert result["summary_cache"] == (
        "precomputed" if mode == "root_guided_summary" else "not_used"
    )
    assert result["summary_generation_seconds_separate"] == (
        5.0 if mode == "root_guided_summary" else 0.0
    )
    assert builder.call_args.kwargs["max_sections_per_predicate"] == 2
    assert builder.call_args.kwargs["metadata"] == {}
    assert cmc_benchmark.read_json(env.output / "plan.json")["visited"] == ["root"]
    snapshots = [
        json.loads(line) for line in (env.output / "snapshots.jsonl").read_text().splitlines()
    ]
    assert len(snapshots) == 2
    snapshot = snapshots[-1]
    assert snapshot["completion"] == "incomplete"
    assert snapshot["candidates"] == []
    assert snapshot["elapsed_seconds"] >= 0


@pytest.mark.parametrize("mode", ["root_guided", "root_guided_summary"])
def test_cli_accepts_root_modes(mode):
    args = cmc_benchmark.parser().parse_args([
        "run", "--prepared", "/prepared", "--output", "/output", "--mode", mode,
        "--max-sections-per-predicate", "2",
    ])
    assert args.mode == mode
    assert args.max_sections_per_predicate == 2
