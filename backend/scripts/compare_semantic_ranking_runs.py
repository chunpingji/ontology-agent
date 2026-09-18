#!/usr/bin/env python3
"""Read-only comparison of complete, common-pool document ranking checks.

No models, database connections, recognition, or quality scoring are performed.
The output is a new JSON file; input runs and their prepared artifacts are immutable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

INTENTS = {"discover", "counterevidence"}
SCHEMAS = {"document-semantic-ranking-cpu-check-v1", "document-semantic-ranking-check-v2"}
INPUT_KEYS = (
    "document_hash",
    "analysis_id",
    "ontology_snapshot_id",
    "ontology_hash",
    "root_class_iri",
    "predicate_iri",
    "predicate_kind",
    "records",
    "evidence_units",
    "execution_runtime_hash",
)
MODEL_KEYS = (
    "adapter",
    "artifacts",
    "normalization",
    "score_semantics",
    "input_policy",
    "execution",
    "packages",
)


class ComparisonRejected(ValueError):
    """Safe, source-free reason why these runs cannot be compared."""


def _require(condition, reason):
    if not condition:
        raise ComparisonRejected(reason)


def _read(path):
    def reject_constant(_):
        raise ComparisonRejected("nonfinite_json_value")

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonRejected("unreadable_comparison_artifact") from exc


def _digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ComparisonRejected("unreadable_comparison_artifact") from exc


def _content_hash(value):
    # Identical to evidence_hash for the JSON-only values read by this tool.
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _number(value, reason, *, nonnegative=True):
    _require(type(value) in (int, float) and math.isfinite(value), reason)
    _require(not nonnegative or value >= 0, reason)
    return value


def _unique(items, key, reason):
    result = {}
    for item in items:
        identity = key(item)
        _require(identity not in result, reason)
        result[identity] = item
    return result


def _select(value, keys, reason):
    _require(all(key in value and value[key] is not None for key in keys), reason)
    return {key: value[key] for key in keys}


def _verify_artifacts(directory, report):
    hashes = report.get("artifact_sha256", {})
    required = {
        "metadata.json",
        "record_inventory.json",
        "ranking_state.json",
        "retrieval_plan_initial.json",
        "retrieval_plan_final.json",
        "scheduler_requests.json",
        "model_observations.json",
        "epoch_1_committed.json",
    }
    _require(required <= set(hashes), "mandatory_artifact_hash_missing")
    for name, expected in hashes.items():
        _require(Path(name).name == name and name not in ("", ".", ".."), "unsafe_artifact_path")
        _require(_digest(directory / name) == expected, "finalized_artifact_hash_mismatch")


def _prepared_identity(report):
    inputs = report["input"]
    directory = Path(inputs["prepared"])
    _require(
        _digest(directory / "manifest.json") == inputs["manifest_sha256"],
        "prepared_manifest_hash_mismatch",
    )
    manifest = _read(directory / "manifest.json")
    for filename, field in (
        ("source.docx", "document_hash"),
        ("ir.json", "ir_hash"),
        ("ontology_snapshot.json", "ontology_snapshot_file_hash"),
    ):
        _require(_digest(directory / filename) == manifest[field], "prepared_input_hash_mismatch")
    for local, frozen in (
        ("document_hash", "document_hash"),
        ("analysis_id", "analysis_id"),
        ("ontology_snapshot_id", "ontology_snapshot_id"),
        ("ontology_hash", "ontology_semantic_hash"),
        ("root_class_iri", "class_iri"),
    ):
        _require(inputs[local] == manifest[frozen], "prepared_identity_mismatch")
    return {
        key: manifest[key]
        for key in (
            "document_hash",
            "ir_hash",
            "ontology_snapshot_id",
            "ontology_snapshot_file_hash",
            "ontology_semantic_hash",
        )
    }


def _tokens(epoch, state, observations, identity, batch_size, limit):
    queries, ids = epoch["queries"], epoch["record_ids"]
    cached = {}
    for query in queries:
        key = _content_hash(
            [epoch["permission_scope_hash"], identity, query["model_text"], "tokens"]
        )
        count = state["token_cache"].get(key)
        _require(type(count) is int and count > 0, "query_token_measurement_missing")
        cached[query["retrieval_intent"]] = count
    reranks = [row for row in observations if row["operation"] == "score_pairs"]
    expected_batches = [
        (query["retrieval_intent"], ids[start : start + batch_size])
        for query in queries
        for start in range(0, len(ids), batch_size)
    ]
    _require(len(reranks) == len(expected_batches), "reranker_batch_count_mismatch")
    measured = {}
    for row, (intent, batch) in zip(reranks, expected_batches, strict=True):
        counts = row.get("worker_metrics", {}).get("input_token_counts")
        _require(
            isinstance(counts, list) and len(counts) == len(batch),
            "actual_pair_token_measurements_missing",
        )
        _require(row["input_count"] == len(batch), "reranker_batch_size_mismatch")
        _require(
            all(type(count) is int and 0 < count <= limit for count in counts),
            "invalid_actual_pair_tokens",
        )
        _require(sum(counts) == row["input_tokens"], "actual_pair_token_sum_mismatch")
        for rid, count in zip(batch, counts, strict=True):
            measured[(intent, rid)] = count
    return cached, measured


def _load(directory, expected_records):
    directory = Path(directory).resolve()
    report = _read(directory / "report.json")
    _require(report.get("schema_version") in SCHEMAS, "unsupported_run_schema")
    _require(report.get("status") == "passed", "run_not_passed")
    _require(
        report["scope"].get("input_origin") == "frozen_user_document"
        and report["scope"].get("main_llm_called") is False
        and report["scope"].get("gold_quality_evaluation") is False,
        "run_is_not_document_ranking_only",
    )
    _verify_artifacts(directory, report)
    frozen = _prepared_identity(report)
    coverage = report["coverage"]
    _require(
        all(
            coverage.get(key) == expected_records
            for key in ("total_records", "ranking_eligible", "ranked_records", "ledger_unattempted")
        )
        and all(
            coverage.get(key) == 0
            for key in (
                "long_records_excluded_from_ranking",
                "eligible_not_ranked",
                "recognition_records_examined",
            )
        )
        and coverage.get("full_universe_preserved") is True
        and coverage.get("ranking_did_not_change_coverage") is True
        and report["input"]["records"] == expected_records,
        "incomplete_record_universe",
    )
    _require(len(report["epochs"]) == 1, "single_complete_pool_required")
    epoch = _read(directory / "epoch_1_committed.json")
    _require(
        epoch["status"] == "committed"
        and epoch["actual_ranking_mode"] == "semantic"
        and epoch["degraded"] is False,
        "semantic_epoch_not_committed",
    )
    _require(
        report["epochs"][0]["epoch_id"] == epoch["epoch_id"]
        and report["epochs"][0]["temperature"] == "cold_worker_and_models"
        and epoch["costs"].get("technical_retries") == 0,
        "cold_epoch_identity_mismatch",
    )
    ids = epoch["record_ids"]
    _require(len(ids) == expected_records and len(set(ids)) == len(ids), "pool_records_incomplete")
    _require(
        len(epoch["ordered_record_ids"]) == len(ids)
        and set(epoch["ordered_record_ids"]) == set(ids),
        "final_ranking_incomplete",
    )
    policy = report["policy"]
    _require(policy["pool_size"] >= expected_records, "pool_budget_smaller_than_universe")
    initial = _read(directory / "retrieval_plan_initial.json")
    final = _read(directory / "retrieval_plan_final.json")
    _require(
        set(initial["frozen_record_ids"]) == set(ids)
        and len(initial["frozen_record_ids"]) == len(ids)
        and set(initial["ledger"]) == set(ids)
        and final["frozen_record_ids"] == initial["frozen_record_ids"]
        and final["ledger"] == initial["ledger"],
        "source_ledger_or_universe_changed",
    )
    metadata = _read(directory / "metadata.json")
    _require(
        metadata["document_hash"] == report["input"]["document_hash"]
        and metadata["analysis_id"] == report["input"]["analysis_id"],
        "metadata_identity_mismatch",
    )
    identity = report["model_identity"]
    _require(epoch["model_identity"] == identity, "epoch_model_identity_mismatch")
    common_model = _select(identity, MODEL_KEYS, "incomplete_model_identity")
    _require(
        common_model["score_semantics"] == "raw_single_logit"
        and common_model["normalization"] == "l2"
        and common_model["input_policy"] == "complete-no-truncation-v1",
        "unexpected_model_semantics",
    )
    _select(
        identity["packages"],
        ("torch", "transformers", "sentence-transformers"),
        "model_package_version_missing",
    )
    for kind in ("embedding", "reranker"):
        _select(
            identity["artifacts"][kind],
            ("manifest_sha256", "tokenizer_sha256"),
            "model_artifact_identity_missing",
        )
    queries = _unique(epoch["queries"], lambda item: item["retrieval_intent"], "duplicate_intent")
    _require(set(queries) == INTENTS, "dual_intent_queries_missing")
    _require(
        all(
            q["predicate_iri"] == report["input"]["predicate_iri"]
            and isinstance(q["model_text"], str)
            and q["model_text"]
            for q in queries.values()
        ),
        "query_predicate_or_text_invalid",
    )
    views = _unique(epoch["retrieval_views"], lambda item: item["record_id"], "duplicate_view")
    inventory = _unique(
        _read(directory / "record_inventory.json"),
        lambda item: item["record_id"],
        "duplicate_inventory_record",
    )
    _require(set(views) == set(inventory) == set(ids), "view_universe_incomplete")
    for rid, view in views.items():
        _require(
            view["status"] == "complete"
            and view["omitted_refs"] == []
            and view["source_snapshot_hash"] == report["input"]["document_hash"]
            and inventory[rid].get("ranking_eligible") is True
            and all(inventory[rid].get(key) == value for key, value in view.items()),
            "incomplete_or_inconsistent_view",
        )
    rows = _unique(
        epoch["observations"],
        lambda item: (item["retrieval_intent"], item["record_id"]),
        "duplicate_pair_observation",
    )
    _require(
        set(rows) == {(intent, rid) for intent in INTENTS for rid in ids},
        "dual_intent_observations_incomplete",
    )
    for (intent, rid), row in rows.items():
        _number(row["raw_rerank_score"], "nonfinite_raw_score", nonnegative=False)
        _require(
            row["score_status"] == "scored"
            and row["view_status"] == "complete"
            and row["omitted_refs"] == []
            and row["query_id"] == queries[intent]["query_id"]
            and row["retrieval_view_hash"] == views[rid]["retrieval_view_hash"]
            and row["source_snapshot_hash"] == views[rid]["source_snapshot_hash"]
            and row["model_input_hash"]
            == _content_hash([queries[intent]["model_text"], views[rid]["model_text"]]),
            "pair_input_binding_mismatch",
        )
    for intent in INTENTS:
        _require(
            sorted(rows[intent, rid]["intent_rank"] for rid in ids) == list(range(1, len(ids) + 1)),
            "intent_ranking_incomplete",
        )
        expected_order = sorted(
            ids, key=lambda rid: (-rows[intent, rid]["raw_rerank_score"], ids.index(rid))
        )
        _require(
            all(
                type(rows[intent, rid]["intent_rank"]) is int
                and rows[intent, rid]["intent_rank"] == rank
                for rank, rid in enumerate(expected_order, 1)
            ),
            "intent_ranks_disagree_with_raw_scores",
        )
    positions = {rid: rank for rank, rid in enumerate(epoch["ordered_record_ids"], 1)}
    _require(
        all(row["pool_rank"] == positions[rid] for (_, rid), row in rows.items()),
        "final_ranks_disagree_with_order",
    )
    observations = _read(directory / "model_observations.json")
    requests = _read(directory / "scheduler_requests.json")
    _require(
        len(requests) == len(observations) and len(requests) > 0, "request_accounting_incomplete"
    )
    request_ids = set()
    for request, row in zip(requests, observations, strict=True):
        _require(
            request["request_id"] not in request_ids
            and request["run_id"] == report["run_id"]
            and request["status"] == row["status"] == "completed",
            "request_not_completed_or_unattributed",
        )
        request_ids.add(request["request_id"])
        _require(
            all(
                request["metrics"].get(key) == row.get(key)
                for key in ("operation", "input_count", "input_tokens", "worker_metrics")
            ),
            "scheduler_and_worker_accounting_disagree",
        )
        _require(
            row["operation"] in ("count_tokens", "embed", "score_pairs"),
            "unexpected_model_operation",
        )
        if row["operation"] != "count_tokens":
            worker = row.get("worker_metrics", {})
            _require(
                worker.get("actual_device") == identity.get("device")
                and worker.get("actual_dtype") == identity.get("dtype"),
                "worker_actual_execution_identity_mismatch",
            )
            _number(worker.get("inference_seconds"), "worker_inference_time_missing")
    state = _read(directory / "ranking_state.json")
    _require(state["model_identity"] == identity, "state_model_identity_mismatch")
    query_tokens, pair_tokens = _tokens(
        epoch, state, observations, identity, policy["batch_size"], policy["max_tokens_per_pair"]
    )
    for view in views.values():
        key = _content_hash(
            [epoch["permission_scope_hash"], identity, view["model_text"], "tokens"]
        )
        _require(state["token_cache"].get(key) == view["token_count"], "view_token_cache_mismatch")
    _require(
        report["restore"]["extra_scheduler_requests"] == 0
        and report["restore"]["state_unchanged"] is True
        and report["restore"]["validated_epochs"] == 1,
        "restore_recalled_model",
    )
    normalized_queries = [
        {
            key: query[key]
            for key in ("retrieval_intent", "predicate_iri", "model_text", "query_version")
        }
        for query in epoch["queries"]
    ]
    shared = {
        "input": _select(report["input"], INPUT_KEYS, "input_identity_missing"),
        "prepared": frozen,
        "harness_sha256": report["harness_sha256"],
        "software_environment": _select(
            report["hardware"], ("python", "machine"), "software_environment_missing"
        ),
        "metadata": metadata,
        "model": common_model,
        "policy": policy,
        "queries": normalized_queries,
        "views": [views[rid] for rid in ids],
        "record_ids": ids,
        "query_tokens": query_tokens,
        "actual_pair_tokens": [
            [intent, rid, pair_tokens[intent, rid]] for intent in sorted(INTENTS) for rid in ids
        ],
    }
    return {
        "directory": directory,
        "report": report,
        "epoch": epoch,
        "rows": rows,
        "requests": requests,
        "observations": observations,
        "shared": shared,
        "request_ids": request_ids,
    }


def _metric(rows, key, *, worker=False):
    values = [(row.get("worker_metrics", {}) if worker else row).get(key) for row in rows]
    known = [
        _number(value, "invalid_timing_or_cost_metric") for value in values if value is not None
    ]
    return {
        "measured_sum": sum(known) if known else None,
        "measured_requests": len(known),
        "unmeasured_requests": len(values) - len(known),
    }


def _performance(run):
    groups = defaultdict(list)
    for row in run["observations"]:
        groups[row["operation"]].append(row)
    operations = {}
    for name, rows in sorted(groups.items()):
        operations[name] = {
            "requests": len(rows),
            "request_seconds": _metric(rows, "request_seconds"),
            "queue_seconds": _metric(rows, "queue_seconds"),
            "input_tokens": _metric(rows, "input_tokens"),
        }
        if name != "count_tokens":
            operations[name]["inference_seconds"] = _metric(rows, "inference_seconds", worker=True)
            load = _metric(rows, "model_load_seconds", worker=True)
            # The worker emits load time only on the first load of each model kind.
            load["omitted_request_meaning"] = "no_model_load_on_request"
            operations[name]["model_load_seconds"] = load
    report = run["report"]
    peaks = {}
    for key in ("cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes"):
        values = [row.get("worker_metrics", {}).get(key) for row in run["observations"]]
        measured = [
            _number(value, "invalid_cuda_memory_metric") for value in values if value is not None
        ]
        peaks[key] = max(measured) if measured else None
    return {
        "cold_total_elapsed_seconds": _number(report["elapsed_seconds"], "missing_total_time"),
        "cold_epoch_elapsed_seconds": _number(
            report["epochs"][0]["elapsed_seconds"], "missing_cold_epoch_time"
        ),
        "factory_seconds": report.get("factory_seconds"),
        "restore_seconds": report["restore"].get("elapsed_seconds"),
        "operations": operations,
        "cuda_memory_peaks": peaks,
        "worker_inference_seconds": _metric(
            [r for r in run["observations"] if r["operation"] != "count_tokens"],
            "inference_seconds",
            worker=True,
        ),
        "worker_model_load_seconds": _metric(
            [r for r in run["observations"] if r["operation"] != "count_tokens"],
            "model_load_seconds",
            worker=True,
        ),
        "reported_costs": report["observed_costs"],
        "temperature_scope": "new_worker_cold_load_and_ranking_only",
    }


def _ranking(left, right):
    ids = list(left)
    count = len(ids)
    squared = sum((left[rid] - right[rid]) ** 2 for rid in ids)
    k = min(10, count)
    left_top = {rid for rid in ids if left[rid] <= k}
    right_top = {rid for rid in ids if right[rid] <= k}
    return {
        "records": count,
        "spearman_rho": 1 - 6 * squared / (count * (count**2 - 1)) if count > 1 else None,
        "top_k": k,
        "top_k_overlap": len(left_top & right_top),
        "top_k_overlap_fraction": len(left_top & right_top) / k,
        "changed_rank_count": sum(left[rid] != right[rid] for rid in ids),
        "tie_policy": "reported_ordinal_ranks_including_frozen_pool_tiebreak",
    }


def compare_runs(left, right, *, expected_records=86):
    _require(
        type(expected_records) is int and expected_records > 0, "invalid_expected_record_count"
    )
    runs = [_load(left, expected_records), _load(right, expected_records)]
    a, b = runs
    _require(
        a["directory"] != b["directory"]
        and a["report"]["run_id"] != b["report"]["run_id"]
        and not (a["request_ids"] & b["request_ids"]),
        "reused_execution",
    )
    for key in a["shared"]:
        _require(a["shared"][key] == b["shared"][key], f"common_{key}_mismatch")
    ids = a["epoch"]["record_ids"]
    intent_results = {}
    all_differences = []
    for intent in sorted(INTENTS):
        differences = [
            abs(
                a["rows"][intent, rid]["raw_rerank_score"]
                - b["rows"][intent, rid]["raw_rerank_score"]
            )
            for rid in ids
        ]
        all_differences.extend(differences)
        intent_results[intent] = {
            **_ranking(
                {rid: a["rows"][intent, rid]["intent_rank"] for rid in ids},
                {rid: b["rows"][intent, rid]["intent_rank"] for rid in ids},
            ),
            "max_absolute_raw_score_difference": max(differences),
            "mean_absolute_raw_score_difference": sum(differences) / len(differences),
        }
    performance = [_performance(run) for run in runs]
    right_time = performance[1]["cold_total_elapsed_seconds"]
    environments = [
        {
            "device": run["report"]["model_identity"].get("device"),
            "dtype": run["report"]["model_identity"].get("dtype"),
            "cuda_version": run["report"]["model_identity"].get("cuda_version"),
            "numeric_environment": run["report"]["model_identity"].get("numeric_environment"),
            "hardware": run["report"].get("hardware"),
        }
        for run in runs
    ]
    return {
        "schema_version": "semantic-ranking-device-comparison-v1",
        "status": "compared",
        "created_at": datetime.now(UTC).isoformat(),
        "input_gate": "passed",
        "comparison_scope": "single_complete_pool_ranking_observation",
        "graph_quality_gate": "not_evaluated",
        "quality_improvement_claimed": False,
        "graph_end_to_end_performance_measured": False,
        "source_runs": [
            {
                "directory": str(run["directory"]),
                "run_id": run["report"]["run_id"],
                "report_sha256": _digest(run["directory"] / "report.json"),
            }
            for run in runs
        ],
        "common_input_hash": _content_hash(a["shared"]),
        "records": len(ids),
        "scored_pairs": len(all_differences),
        "common_model": a["shared"]["model"],
        "prepared_identity": a["shared"]["prepared"],
        "policy": a["shared"]["policy"],
        "numerical_environments": environments,
        "environment_differences": {
            key: {"left": environments[0][key], "right": environments[1][key]}
            for key in environments[0]
            if environments[0][key] != environments[1][key]
        },
        "performance": {
            "left": performance[0],
            "right": performance[1],
            "cold_total_left_over_right": performance[0]["cold_total_elapsed_seconds"] / right_time
            if right_time
            else None,
        },
        "ranking": {
            "intents": intent_results,
            "final": _ranking(
                {rid: i for i, rid in enumerate(a["epoch"]["ordered_record_ids"], 1)},
                {rid: i for i, rid in enumerate(b["epoch"]["ordered_record_ids"], 1)},
            ),
            "max_absolute_raw_score_difference": max(all_differences),
            "mean_absolute_raw_score_difference": sum(all_differences) / len(all_differences),
        },
        "limitations": [
            "No expert relevance reference or graph facts are scored.",
            "One cold ranking observation is not a repeated performance protocol.",
            "GPU/CPU numerical settings and hardware differences are explicit.",
            "No source text is copied into this comparison report.",
        ],
    }


def write_comparison(left, right, output, *, expected_records=86):
    output = Path(output).resolve()
    _require(not output.exists(), "output_already_exists")
    for directory in (Path(left).resolve(), Path(right).resolve()):
        _require(directory != output and directory not in output.parents, "output_inside_input_run")
        prepared = Path(_read(directory / "report.json")["input"]["prepared"]).resolve()
        _require(
            prepared != output and prepared not in output.parents, "output_inside_prepared_input"
        )
    try:
        result = compare_runs(left, right, expected_records=expected_records)
    except ComparisonRejected as exc:
        result = {
            "schema_version": "semantic-ranking-device-comparison-v1",
            "status": "rejected",
            "input_gate": "rejected",
            "reason": str(exc),
            "graph_quality_gate": "not_evaluated",
            "quality_improvement_claimed": False,
        }
    except (KeyError, TypeError, IndexError) as exc:
        raise ComparisonRejected("malformed_comparison_artifact") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSON file outside both runs."
    )
    parser.add_argument("--expected-records", type=int, default=86)
    args = parser.parse_args(argv)
    try:
        result = write_comparison(
            args.left, args.right, args.output, expected_records=args.expected_records
        )
    except (ComparisonRejected, FileExistsError) as exc:
        parser.exit(2, f"comparison rejected: {exc}\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output.resolve()),
                "reason": result.get("reason"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "compared" else 1


if __name__ == "__main__":
    raise SystemExit(main())
