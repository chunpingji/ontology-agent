"""Repeated-run evidence cannot count copied or changed preparations as trials."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/summarize_evidence_repair_runs.py"
spec = importlib.util.spec_from_file_location("repair_cost_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture_runs(tmp_path):
    paths = []
    for number in range(3):
        path = tmp_path / str(number)
        path.mkdir()
        files = {}
        for name in ["source.docx", "model-config.json", "ontology/example.ttl", *(
            "runtime/app/services/extraction/ontology_guided/" + name
            for name in ("executor.py", "repair_adapter.py", "task_citations.py", "verification.py")
        )]:
            file = path / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("frozen fixture", encoding="utf-8")
            files[name] = audit.sha256(file)
        manifest = {
            "run_id": str(number), "source_sha256": files["source.docx"], "files": files,
            "limits": {"max_calls": 48}, "evidence_repair": True,
            "reference_is_recognition_input": False,
        }
        (path / "manifest.json").write_text(json.dumps(manifest))
        (path / "summary.json").write_text(json.dumps({"run_id": str(number)}))
        (path / "execution-started.json").write_text("{}")
        paths.append(path)
    return paths


@pytest.mark.parametrize("changed", ["duplicate", "file", "budget", "unexecuted"])
def test_audit_rejects_invalid_repeated_trials(tmp_path, changed):
    paths = fixture_runs(tmp_path)
    assert len(audit.validate_group(paths)) == 3
    if changed == "duplicate":
        paths[2] = paths[1]
    elif changed == "file":
        (paths[1] / "source.docx").write_text("different document")
    elif changed == "budget":
        file = paths[1] / "manifest.json"
        data = audit.read(file)
        data["limits"]["max_calls"] = 4
        file.write_text(json.dumps(data))
    else:
        (paths[1] / "execution-started.json").unlink()
    with pytest.raises(ValueError):
        audit.validate_group(paths)


def test_focus_timing_stop_does_not_mix_properties_of_different_parents():
    path = SCRIPT.with_name("benchmark_heuristic_document_run.py")
    spec = importlib.util.spec_from_file_location("repair_focus_benchmark", path)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    history = [{"predicate_path": list(path), "currently_effective": True,
                "subject_ref": {"id": path[0]}}
               for path in benchmark.REPAIR_FOCUS_PATHS]
    assert benchmark.focus_paths_observed(history)
    history[1]["subject_ref"]["id"] = "another-equipment"
    assert not benchmark.focus_paths_observed(history)
    history[1]["subject_ref"]["id"] = history[0]["subject_ref"]["id"]
    history[3]["currently_effective"] = False
    assert not benchmark.focus_paths_observed(history)


def test_audit_keeps_rejected_candidates_absent_from_effective_history(tmp_path):
    candidate = {"candidate_id": "rejected-date", "policy_eligible": False,
                 "decision_status": "unsupported", "raw_value": "2026年07月"}
    payloads = {
        "summary.json": {"run_id": "run", "recognition_seconds": 2,
                         "effective_candidate_history": []},
        "model-requests.json": [],
        "graph/result.json": {"graph": {"properties": [candidate]}},
        "batches/0001.json": {
            "task": {"task_id": "task"}, "elapsed_seconds": 2,
            "outcome": {"semantic_outcome": "unsupported", "reason_code": "wrong-date",
                        "properties": [candidate]},
        },
    }
    for name, payload in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    result = audit.summarize_run(tmp_path, automatic=True)
    assert result["effective_candidate_history"] == []
    assert result["candidate_history"][0]["properties"] == [candidate]
    assert result["final_candidates"] == [candidate]


def test_missing_costs_are_unknown_and_partial_costs_keep_their_missing_count():
    result = audit.request_costs([
        {"stage": "verification", "status": "complete", "metrics": {"prompt_tokens": 12}},
        {"stage": "verification", "status": "failed", "metrics": {}},
    ])["verification"]
    assert result["requests"] == 2
    assert result["input_tokens"] is None
    assert result["request_seconds"] is None
    assert result["unknown_metrics"]["input_tokens"] == 2
    assert result["prompt_tokens"] == 12
    assert result["unknown_metrics"]["prompt_tokens"] == 1


def test_only_explicitly_disabled_cross_group_pool_size_may_differ():
    directed = {"model_revision": "frozen", "semantic_ranking_enabled": False,
                "semantic_ranking_pool_size": 64}
    automatic = {**directed, "semantic_ranking_pool_size": 16}
    assert audit.compare_model_configs(directed, automatic) == {
        "semantic_ranking_pool_size": {"directed": 64, "automatic": 16},
    }
    for changed in ({**automatic, "semantic_ranking_enabled": True},
                    {**automatic, "model_revision": "another-model"}):
        with pytest.raises(ValueError, match="active model configuration drift"):
            audit.compare_model_configs(directed, changed)
