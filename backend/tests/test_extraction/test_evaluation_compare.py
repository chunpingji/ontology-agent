"""Artifact comparison must not turn partial progress or supplied roots into wins."""

import json

import pytest

from app.evaluation.compare import compare_runs, write_report
from app.evaluation.graph_metrics import evaluate_graph
from tests.test_extraction.test_evaluation_graph_metrics import entity, reference, source_ir


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def experiment(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    manifest = {
        "document_hash": "doc",
        "runtime_hash": "runtime",
        "ontology_hash": "ontology",
        "settings": {"local_llm_model": "fixture-no-live-model"},
    }
    write(prepared / "manifest.json", manifest)
    write(prepared / "ir.json", source_ir())
    expected = reference()
    expected["entities"].append(
        {
            "id": "report",
            "class_iri": "urn:Report",
            "aliases": ["supplied report"],
            "is_document_root": True,
        }
    )
    ref = prepared / "reference.json"
    write(ref, expected)
    paths = []
    for mode, elapsed in (("baseline", 20.0), ("structure_summary", 10.0)):
        path = tmp_path / mode
        path.mkdir()
        candidates = [
            entity(
                "root", "supplied report", class_iri="urn:Report", identity={"document_root": "doc"}
            ),
            entity(),
        ]
        run = {"completion": "incomplete", "candidates": candidates}
        result = {
            **{key: manifest[key] for key in ("document_hash", "runtime_hash", "ontology_hash")},
            "model_settings": manifest["settings"],
            "mode": mode,
            "budget": {"max_tasks": 100},
            "completion": "incomplete",
            "elapsed_seconds_total": elapsed,
            "elapsed_seconds_this_segment": elapsed,
            "summary_generation_seconds_separate": 5 if mode != "baseline" else 0,
            "cold_metadata_accounted_seconds": elapsed + (5 if mode != "baseline" else 0),
            "model_calls_this_segment": 16,
            "task_count": 13,
            "candidate_counts": {"entity": 2},
            "validated_counts": {"entity": 2},
            "first_seconds_this_segment": {"validated_result": 1, "validated_relationship": 4},
        }
        write(path / "run.json", run)
        write(path / "result.json", result)
        write(path / "metrics.json", evaluate_graph(run, expected, ir=source_ir()))
        write(path / "checkpoint.json", {"attempt_count": 12, "model_calls": 16})
        paths.append(path)
    return prepared, paths, ref


def compare(experiment):
    prepared, paths, ref = experiment
    return compare_runs(prepared, paths, reference_path=ref)


@pytest.mark.parametrize(
    "field", ["document_hash", "runtime_hash", "ontology_hash", "model_settings", "budget",
              "execution_limits"]
)
def test_mixed_identities_or_budgets_are_refused(experiment, field):
    path = experiment[1][1] / "result.json"
    result = json.loads(path.read_text())
    result[field] = {"different": True} if field in {"model_settings", "budget"} else "different"
    write(path, result)
    with pytest.raises(ValueError, match="not comparable"):
        compare(experiment)


def test_incomplete_runs_allow_only_equal_attempted_budget_ratio(experiment):
    report = compare(experiment)
    ratio = report["baseline_comparisons"][0]
    assert ratio["same_task_budget_observed_elapsed_ratio"] == 2
    assert ratio["complete_run_observed_elapsed_ratio"] is None
    assert ratio["complete_run_ratio_withheld_reason"] == "one_or_both_runs_incomplete"
    assert ratio["equal_quality_speedup_established"] is False
    checkpoint = experiment[1][1] / "checkpoint.json"
    write(checkpoint, {"attempt_count": 11, "model_calls": 16})
    ratio = compare(experiment)["baseline_comparisons"][0]
    # Both result.task_count values remain 13: event counts are not attempted-task budgets.
    assert ratio["same_task_budget_observed_elapsed_ratio"] is None
    assert ratio["same_attempted_task_budget"] is False


def test_root_volume_is_separate_from_extracted_accuracy_and_true_first_relation(experiment):
    report = compare(experiment)
    row = report["rows"][0]
    assert row["raw_candidate_counts"]["entity"] == 2
    assert row["supplied_metadata_root_count"] == 1
    assert row["raw_extracted_candidate_counts"]["entity"] == 1
    assert row["validated_extracted_only_metrics"]["entity"]["tp"] == 1
    assert row["first_validated_relationship_this_segment_seconds"] == 4
    assert row["first_reference_correct_relationship_seconds"] is None


def test_tampered_metrics_are_rejected_against_run_and_frozen_reference(experiment):
    path = experiment[1][1] / "metrics.json"
    metrics = json.loads(path.read_text())
    metrics["validated"]["extracted_only"]["micro"]["precision"] = 0
    write(path, metrics)
    with pytest.raises(ValueError, match="saved metrics differ"):
        compare(experiment)


def test_report_writes_new_directory_only_and_does_not_change_inputs(experiment, tmp_path):
    report = compare(experiment)
    run_paths = [path for directory in experiment[1] for path in directory.iterdir()]
    before = {path: path.read_bytes() for path in run_paths}
    output = tmp_path / "report"
    write_report(report, output)
    assert (output / "comparison.json").is_file()
    assert "未完成组不提供完整运行时间比" in (output / "comparison.md").read_text()
    assert {path: path.read_bytes() for path in run_paths} == before
    with pytest.raises(FileExistsError):
        write_report(report, output)
