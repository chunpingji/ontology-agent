"""Immutable CPU/GPU observation comparisons; no model or scheduler execution."""

from __future__ import annotations

import copy
import json

import pytest

from scripts import compare_semantic_ranking_runs as comparison


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _seal(directory):
    report = json.loads((directory / "report.json").read_text())
    report["artifact_sha256"] = {
        path.name: comparison._digest(path)
        for path in directory.iterdir()
        if path.name != "report.json"
    }
    _write(directory / "report.json", report)


def _mutate(directory, name, update, *, reseal=True):
    path = directory / name
    data = json.loads(path.read_text())
    update(data)
    _write(path, data)
    if reseal:
        _seal(directory)


def _run(
    tmp_path,
    name,
    *,
    device="cpu",
    query_suffix="",
    text_suffix="",
    package="2.7.1+cu126",
    pair_delta=0,
):
    prepared = tmp_path / "prepared"
    if not prepared.exists():
        prepared.mkdir()
        for filename in ("source.docx", "ir.json", "ontology_snapshot.json"):
            (prepared / filename).write_text(f"frozen test {filename}")
        _write(
            prepared / "manifest.json",
            {
                "document_hash": comparison._digest(prepared / "source.docx"),
                "ir_hash": comparison._digest(prepared / "ir.json"),
                "ontology_snapshot_file_hash": comparison._digest(
                    prepared / "ontology_snapshot.json"
                ),
                "analysis_id": "analysis",
                "ontology_snapshot_id": "ontology-snapshot",
                "ontology_semantic_hash": "ontology-semantic",
                "class_iri": "urn:Report",
            },
        )
    manifest = json.loads((prepared / "manifest.json").read_text())
    directory = tmp_path / name
    directory.mkdir()
    gpu = device.startswith("cuda:")
    identity = {
        "adapter": "offline-semantic-ranking-v2",
        "device": device,
        "dtype": "float16" if gpu else "float32",
        "cuda_version": "12.6" if gpu else None,
        "numeric_environment": {"attention": "eager" if gpu else "default"},
        "normalization": "l2",
        "score_semantics": "raw_single_logit",
        "input_policy": "complete-no-truncation-v1",
        "execution": {"batch_size": 4},
        "artifacts": {
            kind: {"manifest_sha256": kind + "-manifest", "tokenizer_sha256": kind + "-tokenizer"}
            for kind in ("embedding", "reranker")
        },
        "packages": {"torch": package, "transformers": "4.51.3", "sentence-transformers": "4.1.0"},
    }
    ids = [f"record-{i:02}" for i in range(12)]
    queries = [
        {
            "query_id": f"{name}-{intent}",
            "query_version": "subject-slot-query-v1",
            "run_fingerprint": name,
            "subject_ref": {"entity_id": name},
            "retrieval_intent": intent,
            "predicate_iri": "urn:usesEquipment",
            "model_text": f"synthetic query {intent}{query_suffix}",
        }
        for intent in ("discover", "counterevidence")
    ]
    views = [
        {
            "record_id": rid,
            "retrieval_view_hash": f"view-{rid}{text_suffix}",
            "source_snapshot_hash": manifest["document_hash"],
            "model_text": f"synthetic source {rid}{text_suffix}",
            "structure_text": "heading",
            "metadata_text": "",
            "source_refs": [],
            "binding_refs": [],
            "omitted_refs": [],
            "token_count": i + 10,
            "status": "complete",
            "authority": "retrieval_only",
        }
        for i, rid in enumerate(ids)
    ]
    epoch = {
        "epoch_id": name + "-epoch",
        "permission_scope_hash": name + "-scope",
        "record_ids": ids,
        "ordered_record_ids": ids,
        "queries": queries,
        "retrieval_views": views,
        "model_identity": identity,
        "observations": [],
        "status": "committed",
        "actual_ranking_mode": "semantic",
        "degraded": False,
        "costs": {"technical_retries": 0},
    }
    for query in queries:
        for rank, view in enumerate(views, 1):
            epoch["observations"].append(
                {
                    "record_id": view["record_id"],
                    "retrieval_intent": query["retrieval_intent"],
                    "query_id": query["query_id"],
                    "model_input_hash": comparison._content_hash(
                        [query["model_text"], view["model_text"]]
                    ),
                    "retrieval_view_hash": view["retrieval_view_hash"],
                    "source_snapshot_hash": view["source_snapshot_hash"],
                    "view_status": "complete",
                    "omitted_refs": [],
                    "raw_rerank_score": -rank + (0.01 if gpu else 0),
                    "intent_rank": rank,
                    "pool_rank": rank,
                    "score_status": "scored",
                }
            )
    token_cache = {}
    for item in [*queries, *views]:
        key = comparison._content_hash(
            [epoch["permission_scope_hash"], identity, item["model_text"], "tokens"]
        )
        token_cache[key] = item.get("token_count", 8)
    observations = []
    for operation, batches in (
        ("embed", [[10] * 4]),
        ("score_pairs", [[30 + i + pair_delta] * 4 for i in range(6)]),
    ):
        for i, counts in enumerate(batches):
            worker = {
                "actual_device": device,
                "actual_dtype": identity["dtype"],
                "input_token_counts": counts,
                "inference_seconds": 0.1 if gpu else 1.0,
            }
            if i == 0:
                worker["model_load_seconds"] = 0.5
            if gpu:
                worker.update(cuda_peak_allocated_bytes=1024, cuda_peak_reserved_bytes=2048)
            observations.append(
                {
                    "operation": operation,
                    "status": "completed",
                    "input_count": len(counts),
                    "input_tokens": sum(counts),
                    "worker_metrics": worker,
                    "request_seconds": 0.2 if gpu else 2.0,
                    "queue_seconds": 0.01,
                }
            )
    requests = [
        {
            "request_id": f"{name}-request-{i}",
            "run_id": name,
            "status": "completed",
            "metrics": {key: value for key, value in row.items() if key != "status"},
        }
        for i, row in enumerate(observations)
    ]
    report = {
        "schema_version": "document-semantic-ranking-check-v2",
        "status": "passed",
        "run_id": name,
        "elapsed_seconds": 2.0 if gpu else 20.0,
        "factory_seconds": 0.5,
        "scope": {
            "input_origin": "frozen_user_document",
            "main_llm_called": False,
            "gold_quality_evaluation": False,
        },
        "input": {
            "prepared": str(prepared),
            "manifest_sha256": comparison._digest(prepared / "manifest.json"),
            "document_hash": manifest["document_hash"],
            "analysis_id": "analysis",
            "ontology_snapshot_id": "ontology-snapshot",
            "ontology_hash": "ontology-semantic",
            "root_class_iri": "urn:Report",
            "predicate_iri": "urn:usesEquipment",
            "predicate_kind": "relationship",
            "records": 12,
            "evidence_units": 12,
            "execution_runtime_hash": "same-runtime",
        },
        "coverage": {
            "total_records": 12,
            "ranking_eligible": 12,
            "ranked_records": 12,
            "ledger_unattempted": 12,
            "long_records_excluded_from_ranking": 0,
            "eligible_not_ranked": 0,
            "recognition_records_examined": 0,
            "full_universe_preserved": True,
            "ranking_did_not_change_coverage": True,
        },
        "model_identity": identity,
        "policy": {"pool_size": 12, "batch_size": 4, "max_tokens_per_pair": 4096},
        "epochs": [
            {
                "epoch_id": epoch["epoch_id"],
                "temperature": "cold_worker_and_models",
                "elapsed_seconds": 1.0 if gpu else 10.0,
            }
        ],
        "restore": {
            "extra_scheduler_requests": 0,
            "state_unchanged": True,
            "validated_epochs": 1,
            "elapsed_seconds": 0.02,
        },
        "harness_sha256": "same-harness",
        "observed_costs": {"scheduler_requests": len(requests)},
        "hardware": {"python": "3.11.9", "machine": "x86_64"},
    }
    plan = {"frozen_record_ids": ids, "ledger": {rid: "unattempted" for rid in ids}}
    files = {
        "epoch_1_committed.json": epoch,
        "model_observations.json": observations,
        "scheduler_requests.json": requests,
        "record_inventory.json": [{**view, "ranking_eligible": True} for view in views],
        "ranking_state.json": {"model_identity": identity, "token_cache": token_cache},
        "metadata.json": {
            "analysis_id": "analysis",
            "document_hash": manifest["document_hash"],
            "structure_hash": "same-structure",
        },
        "retrieval_plan_initial.json": plan,
        "retrieval_plan_final.json": plan,
        "report.json": report,
    }
    for filename, payload in files.items():
        _write(directory / filename, payload)
    _seal(directory)
    return directory


def test_comparison_binds_inputs_but_allows_independent_gpu_execution_identity(tmp_path):
    left = _run(tmp_path, "cpu")
    right = _run(tmp_path, "gpu", device="cuda:0")
    before = {p: comparison._digest(p) for root in (left, right) for p in root.iterdir()}
    output = tmp_path / "comparison.json"
    result = comparison.write_comparison(left, right, output, expected_records=12)
    assert result["status"] == "compared"
    assert result["records"] == 12 and result["scored_pairs"] == 24
    assert result["graph_quality_gate"] == "not_evaluated"
    assert result["quality_improvement_claimed"] is False
    assert result["graph_end_to_end_performance_measured"] is False
    assert result["performance"]["cold_total_left_over_right"] == 10
    assert result["performance"]["right"]["worker_inference_seconds"][
        "measured_sum"
    ] == pytest.approx(0.7)
    assert result["performance"]["right"]["worker_model_load_seconds"]["measured_sum"] == 1
    assert result["performance"]["right"]["cuda_memory_peaks"]["cuda_peak_allocated_bytes"] == 1024
    assert result["ranking"]["intents"]["discover"]["spearman_rho"] == 1
    assert result["ranking"]["intents"]["counterevidence"]["top_k_overlap"] == 10
    assert result["ranking"]["max_absolute_raw_score_difference"] == pytest.approx(0.01)
    assert result["ranking"]["mean_absolute_raw_score_difference"] == pytest.approx(0.01)
    assert result["environment_differences"]["dtype"] == {"left": "float32", "right": "float16"}
    assert "synthetic source" not in output.read_text()
    assert all(comparison._digest(path) == digest for path, digest in before.items())
    with pytest.raises(comparison.ComparisonRejected, match="output_already_exists"):
        comparison.write_comparison(left, right, output, expected_records=12)


@pytest.mark.parametrize(
    ("option", "value", "reason"),
    [
        ("package", "2.8.0", "common_model_mismatch"),
        ("query_suffix", " changed", "common_queries_mismatch"),
        ("text_suffix", " changed", "common_views_mismatch"),
        ("pair_delta", 1, "common_actual_pair_tokens_mismatch"),
    ],
)
def test_valid_individual_runs_with_changed_comparison_inputs_are_rejected(
    tmp_path, option, value, reason
):
    left = _run(tmp_path, "left")
    right = _run(tmp_path, "right", **{option: value})
    with pytest.raises(comparison.ComparisonRejected, match=reason):
        comparison.compare_runs(left, right, expected_records=12)


@pytest.mark.parametrize(
    ("filename", "update", "reason"),
    [
        (
            "report.json",
            lambda d: d["coverage"].update(ranked_records=11),
            "incomplete_record_universe",
        ),
        (
            "report.json",
            lambda d: d["epochs"].append(copy.deepcopy(d["epochs"][0])),
            "single_complete_pool_required",
        ),
        (
            "report.json",
            lambda d: d["policy"].update(pool_size=11),
            "pool_budget_smaller_than_universe",
        ),
        (
            "epoch_1_committed.json",
            lambda d: d["observations"].pop(),
            "dual_intent_observations_incomplete",
        ),
        (
            "epoch_1_committed.json",
            lambda d: d["observations"].append(d["observations"][0]),
            "duplicate_pair_observation",
        ),
        (
            "epoch_1_committed.json",
            lambda d: d["retrieval_views"][0].update(omitted_refs=["missing"]),
            "incomplete_or_inconsistent_view",
        ),
        (
            "epoch_1_committed.json",
            lambda d: d["observations"][0].update(model_input_hash="stale"),
            "pair_input_binding_mismatch",
        ),
    ],
)
def test_resealed_but_incomplete_or_corrupted_runs_cannot_manufacture_comparability(
    tmp_path, filename, update, reason
):
    left, right = _run(tmp_path, "left"), _run(tmp_path, "right")
    _mutate(right, filename, update)
    with pytest.raises(comparison.ComparisonRejected, match=reason):
        comparison.compare_runs(left, right, expected_records=12)


def test_missing_per_pair_measurements_are_unknown_and_rejected(tmp_path):
    left, right = _run(tmp_path, "left"), _run(tmp_path, "right")
    for filename in ("model_observations.json", "scheduler_requests.json"):

        def remove(rows):
            for row in rows:
                metrics = row.get("metrics", row)
                metrics["worker_metrics"].pop("input_token_counts")

        _mutate(right, filename, remove)
    result = comparison.write_comparison(
        left, right, tmp_path / "rejected.json", expected_records=12
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "actual_pair_token_measurements_missing"
    assert "performance" not in result


def test_unsealed_artifact_and_output_inside_source_are_rejected(tmp_path):
    left, right = _run(tmp_path, "left"), _run(tmp_path, "right")
    with pytest.raises(comparison.ComparisonRejected, match="output_inside_input_run"):
        comparison.write_comparison(left, right, right / "comparison.json", expected_records=12)
    with pytest.raises(comparison.ComparisonRejected, match="output_inside_prepared_input"):
        comparison.write_comparison(
            left, right, tmp_path / "prepared" / "out.json", expected_records=12
        )
    _mutate(right, "epoch_1_committed.json", lambda d: d.update(reason="edited"), reseal=False)
    with pytest.raises(comparison.ComparisonRejected, match="finalized_artifact_hash_mismatch"):
        comparison.compare_runs(left, right, expected_records=12)


def test_rank_statistics_use_complete_ordinal_ranks_and_explicit_top_k():
    left = {str(i): i + 1 for i in range(12)}
    right = {str(i): (i + 1) % 12 + 1 for i in range(12)}
    metrics = comparison._ranking(left, right)
    assert metrics["top_k_overlap_fraction"] == 0.9
    assert metrics["spearman_rho"] == pytest.approx(7 / 13)
    assert metrics["changed_rank_count"] == 12


def test_cli_default_requires_all_86_records_and_saves_rejection(tmp_path, capsys):
    left, right = _run(tmp_path, "left"), _run(tmp_path, "right")
    output = tmp_path / "rejected.json"
    assert (
        comparison.main(["--left", str(left), "--right", str(right), "--output", str(output)]) == 1
    )
    assert json.loads(capsys.readouterr().out)["reason"] == "incomplete_record_universe"
    assert json.loads(output.read_text())["input_gate"] == "rejected"
