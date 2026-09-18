"""Audit three frozen runs per group and summarize costs without scoring model claims.

Only reads completed experiment artifacts. References/gold answers are deliberately
absent: the output retains candidates and unexecuted tasks for independent review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_group(paths):
    if len(paths) != 3:
        raise ValueError("exactly three independent runs are required per group")
    manifests = [read(path / "manifest.json") for path in paths]
    if len({m["run_id"] for m in manifests}) != 3:
        raise ValueError("duplicate run identity")
    for path, manifest in zip(paths, manifests):
        summary = read(path / "summary.json")
        if (summary["run_id"] != manifest["run_id"]
                or not (path / "execution-started.json").exists()):
            raise ValueError("missing or mismatched execution identity")
        if manifest.get("reference_is_recognition_input") is not False:
            raise ValueError("recognition reference isolation was not declared")
        required = {"source.docx", "model-config.json", *(
            "runtime/app/services/extraction/ontology_guided/" + name
            for name in ("executor.py", "repair_adapter.py", "task_citations.py", "verification.py")
        )}
        if (not required <= manifest["files"].keys()
                or not any(key.startswith("ontology/") for key in manifest["files"])
                or manifest["files"]["source.docx"] != manifest["source_sha256"]):
            raise ValueError("incomplete frozen inputs")
        for key in ("source_sha256", "files", "limits", "evidence_repair"):
            if manifest[key] != manifests[0][key]:
                raise ValueError(f"within-group frozen input drift: {key}")
        for relative, digest in manifest["files"].items():
            artifact = (path / relative).resolve()
            if not artifact.is_relative_to(path.resolve()) or sha256(artifact) != digest:
                raise ValueError(f"frozen file changed: {relative}")
    return manifests


def request_costs(rows):
    stages = defaultdict(lambda: {
        "requests": 0, "status_counts": Counter(), "request_seconds": 0.0,
        "queue_seconds": 0.0, "input_tokens": 0, "prompt_tokens": 0,
        "completion_tokens": 0, "prompt_ms": 0.0, "predicted_ms": 0.0,
        "cache_tokens": 0, "unknown_metrics": Counter(),
    })
    for row in rows:
        stage = stages[row["stage"]]
        stage["requests"] += 1
        stage["status_counts"][row["status"]] += 1
        metrics = row.get("metrics") or {}
        for name in ("request_seconds", "queue_seconds", "input_tokens",
                     "prompt_tokens", "completion_tokens", "prompt_ms",
                     "predicted_ms", "cache_tokens"):
            if metrics.get(name) is None:
                stage["unknown_metrics"][name] += 1
            else:
                stage[name] += metrics[name]
    for stage in stages.values():
        for name, unknown_count in stage["unknown_metrics"].items():
            if unknown_count == stage["requests"]:
                stage[name] = None
    return dict(stages)


def summarize_run(path, *, automatic):
    summary = read(path / "summary.json")
    result = {
        "directory": str(path.resolve()), "run_id": summary["run_id"],
        "recognition_seconds": summary["recognition_seconds"],
        "failure": summary.get("failure") or summary.get("exception"),
        "model_request_stages": request_costs(read(path / "model-requests.json")),
        "business_quality": "requires_independent_source_review",
    }
    if automatic:
        result.update({name: summary.get(name) for name in (
            "progress", "task_batches", "reserved_main_requests", "requested_stop_reason",
            "effective_candidate_history", "timings", "local_persistence", "search_stages",
            "sql", "model_requests_by_search_stage",
        )})
        result["tasks"] = []
        result["candidate_history"] = []
        graph = read(path / "graph/result.json").get("graph") or {}
        result["coverage"] = graph.get("coverage", [])
        result["final_nodes"] = graph.get("nodes", [])
        result["final_candidates"] = [*graph.get("edges", []), *graph.get("properties", [])]
        for file in sorted((path / "batches").glob("*.json")):
            batch = read(file)
            result["tasks"].append({
                "task": batch["task"], "elapsed_seconds": batch["elapsed_seconds"],
                "semantic_outcome": batch["outcome"]["semantic_outcome"],
                "reason_code": batch["outcome"]["reason_code"],
            })
            # Effective history alone excludes failed proposals and revisions.
            # Retain every applied candidate without treating it as a valid fact.
            result["candidate_history"].append({
                "batch": file.name,
                "task_id": batch["task"]["task_id"],
                "elapsed_seconds": batch["elapsed_seconds"],
                "nodes": batch["outcome"].get("nodes", []),
                "edges": batch["outcome"].get("edges", []),
                "properties": batch["outcome"].get("properties", []),
            })
    else:
        result["tasks"] = read(path / "task-results.json")
        result["registered_cases"] = read(path / "cases.json")
        result["blocked_children"] = [
            event for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if (event := json.loads(line)).get("event") == "children_blocked"
        ]
        result["outcomes"] = {
            file.parent.name: read(file) for file in sorted((path / "tasks").glob("*/outcome.json"))
        }
        result["stop_reason"] = summary["stop_reason"]
        result["model_reservations"] = summary["model_reservations"]
    return result


def compare_model_configs(directed, automatic):
    differences = {key: {"directed": directed.get(key), "automatic": automatic.get(key)}
                   for key in directed.keys() | automatic.keys()
                   if directed.get(key) != automatic.get(key)}
    # The directed runner does no ranking, and both preparations explicitly
    # disable it. Keep the inert pool-size difference visible rather than
    # pretending the frozen configuration files are byte-identical.
    if differences and not (
        set(differences) == {"semantic_ranking_pool_size"}
        and directed.get("semantic_ranking_enabled") is False
        and automatic.get("semantic_ranking_enabled") is False
    ):
        raise ValueError("cross-group active model configuration drift")
    return differences


def aggregate(directed, automatic):
    manifests = {"directed": validate_group(directed), "automatic": validate_group(automatic)}
    all_manifests = [*manifests["directed"], *manifests["automatic"]]
    if len({m["run_id"] for m in all_manifests}) != 6:
        raise ValueError("cross-group duplicate run identity")
    if len({m["source_sha256"] for m in all_manifests}) != 1:
        raise ValueError("cross-group source drift")
    # The runners differ, but shared recognition code, ontology and model must agree.
    frozen = manifests["directed"][0]["files"]
    compared = manifests["automatic"][0]["files"]
    inactive_config_differences = compare_model_configs(
        read(directed[0] / "model-config.json"), read(automatic[0] / "model-config.json"),
    )
    for key, digest in frozen.items():
        if key.startswith(("runtime/app/", "ontology/")):
            if compared.get(key) != digest:
                raise ValueError(f"cross-group shared input drift: {key}")
    return {
        "version": "evidence-repair-cost-audit-v1",
        "source_sha256": all_manifests[0]["source_sha256"],
        "inactive_cross_group_config_differences": inactive_config_differences,
        "group_limits": {name: values[0]["limits"] for name, values in manifests.items()},
        "directed": [summarize_run(path, automatic=False) for path in directed],
        "automatic": [summarize_run(path, automatic=True) for path in automatic],
        "timing_scope": "overlapping stage durations must not be summed as wall time",
        "quality_scope": "unscored facts and unexecuted branches remain visible; no quality pass",
        "production_postgresql_performance": "not_measured",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directed", type=Path, nargs=3, required=True)
    parser.add_argument("--automatic", type=Path, nargs=3, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(args.directed, args.automatic)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
    print(json.dumps({"output": str(args.output), "runs": 6, "quality": "not_scored"}))


if __name__ == "__main__":
    main()
