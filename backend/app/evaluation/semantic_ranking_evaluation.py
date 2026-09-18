"""Reference-isolated retrieval, evidence-closure, cost and ablation diagnostics.

This module accepts frozen artifacts only. It never instantiates a recognition
runner or passes expert query/record annotations into online discovery.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from app.services.extraction.evidence_identity import canonical_json, evidence_hash


def _unique(values):
    return list(dict.fromkeys(values))


def _recall(expected, observed):
    return len(expected & set(observed)) / len(expected) if expected else None


def score_retrieval_query(reference: dict, observation: dict, *, k: int) -> dict:
    """Use original record IDs; windows/intent duplicates earn no extra credit."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("retrieval K must be a positive frozen integer")
    for key in ("query_id", "document_hash", "query_content_hash"):
        if not reference.get(key) or observation.get(key) != reference[key]:
            raise ValueError(f"retrieval reference/run {key} mismatch")
    records = reference.get("records", [])
    grades = {item["record_id"]: item["grade"] for item in records}
    if len(grades) != len(records) or any(
        not isinstance(grade, int) or isinstance(grade, bool) or grade not in range(4)
        for grade in grades.values()
    ):
        raise ValueError("invalid or duplicate record grades")
    if any(item.get("role") not in ("support", "counterevidence", "conditional", "context")
           for item in records):
        raise ValueError("record relevance role must be explicitly annotated")
    raw_ranking = observation.get("ranking_record_ids", [])
    ranking = _unique(raw_ranking)
    pool = set(observation.get("pool_record_ids", []))
    if not set(ranking).issubset(pool):
        raise ValueError("ranking contains records outside its frozen pool")
    if set(ranking) != pool:
        raise ValueError("ranking does not cover its complete frozen pool")
    useful = {record_id for record_id, grade in grades.items() if grade >= 2}
    unscored = set(ranking) - grades.keys()
    top = ranking[:k]
    gain = {0: 0, 1: 0, 2: 1, 3: 3}
    dcg = sum(gain[grades.get(record_id, 0)] / math.log2(rank + 2)
              for rank, record_id in enumerate(top))
    ideal = sum(gain[grade] / math.log2(rank + 2)
                for rank, grade in enumerate(sorted(grades.values(), reverse=True)[:k]))
    first = next((index + 1 for index, record_id in enumerate(ranking)
                  if record_id in useful), None)
    facets = {}
    for role in ("support", "counterevidence", "conditional", "context"):
        relevant = {item["record_id"] for item in records
                    if item.get("role") == role and item["grade"] >= 2}
        facets[role] = {"relevant": len(relevant), "recall_at_k": _recall(relevant, top)}
    sets = reference.get("assertions", [])
    closure = {"pool": 0, "dispatched": 0, "assembled": 0, "denominator": len(sets)}
    for assertion in sets:
        alternatives = assertion.get("equivalent_evidence_sets", [])
        if not alternatives or any(not option.get("target_record_ids") for option in alternatives):
            raise ValueError("evidence closure requires nonempty equivalent target sets")
        for stage, observed in (
            ("pool", pool),
            ("dispatched", set(observation.get("dispatch_record_ids", []))),
            ("assembled", set(observation.get("assembled_record_ids", []))),
        ):
            closure[stage] += any(
                set(option["target_record_ids"]).issubset(observed)
                and (stage != "assembled" or set(option.get("binding_source_ids", []))
                     .issubset(set(observation.get("assembled_source_ids", []))))
                for option in alternatives
            )
    complete = reference.get("annotation_complete") is True and not unscored
    return {
        "query_id": reference["query_id"], "k": k, "unit": "original_record_id",
        "useful_threshold": 2, "gain_policy": gain,
        "relevant_records": len(useful), "no_relevant_records": not useful,
        "recall_at_pool": _recall(useful, pool),
        "recall_at_k": _recall(useful, top),
        "ndcg_at_k": dcg / ideal if ideal else None,
        "mrr": 1 / first if first else (0.0 if useful else None),
        "roles": facets, "evidence_closure": closure,
        "duplicate_ranking_entries": len(raw_ranking) - len(ranking),
        "unscored_records": sorted(unscored),
        "annotation_status": "complete" if complete else "partial_observation_only",
        "formal_quality_gate": "not_configured" if complete else "blocked_incomplete_annotation",
    }


def validate_ablation_pair(left: dict, right: dict, *, allowed_factor_changes: list[str],
                           fixed_pool: bool = True) -> dict:
    """Only externally preregistered factors may differ; never infer permission."""
    required = ("document_hash", "ontology_hash", "core_hash", "metadata_hash", "scope",
                "recognition_model_identity", "budget", "query_policy_hash", "view_policy_hash")
    for name, manifest in (("left", left), ("right", right)):
        if manifest.get("schema_version") != "semantic-ranking-ablation-v1":
            raise ValueError(f"{name} ablation schema mismatch")
        if any(key not in manifest.get("shared", {}) for key in required):
            raise ValueError(f"{name} ablation shared manifest incomplete")
        if not isinstance(manifest.get("factors"), dict):
            raise ValueError(f"{name} ablation factors missing")
    if canonical_json(left["shared"]) != canonical_json(right["shared"]):
        raise ValueError("ablation changed a shared input, model, scope or budget")
    changes = {
        key for key in left["factors"].keys() | right["factors"].keys()
        if canonical_json([key in left["factors"], left["factors"].get(key)]) !=
        canonical_json([key in right["factors"], right["factors"].get(key)])
    }
    if not changes.issubset(set(allowed_factor_changes)):
        raise ValueError("ablation contains unregistered factor changes")
    if fixed_pool:
        for manifest in (left, right):
            pool = manifest.get("fixed_pool", {})
            records = pool.get("pool_record_ids", [])
            views = pool.get("view_hashes", {})
            if (not isinstance(records, list) or len(records) != len(set(records))
                    or not isinstance(views, dict) or set(views) != set(records)):
                raise ValueError("fixed-pool view mapping must cover each unique pool record")
        for key in ("query_content_hash", "pool_hash", "view_hashes", "pool_record_ids"):
            if not left.get("fixed_pool", {}).get(key) or not right.get("fixed_pool", {}).get(key):
                raise ValueError("fixed-pool ablation identity missing")
            if canonical_json(left["fixed_pool"][key]) != canonical_json(right["fixed_pool"][key]):
                raise ValueError(f"fixed-pool ablation {key} mismatch")
    return {"valid": True, "changed_factors": sorted(changes), "fixed_pool_checked": fixed_pool,
            "quality_improvement": "requires_independent_paired_quality_protocol"}


def build_ablation_manifest(measured: dict, run: Any, *, epoch_id: str | None = None) -> dict:
    """Machine-comparable dynamic run or explicit frozen epoch subexperiment."""
    identity = measured["ranking_identity"]
    policy = identity["policy"]
    model = identity.get("model_identity") or {}
    factors = {key: policy[key] for key in
               ("mode", "enable_dense", "enable_reranker", "phase_interleaving")}
    factors["embedding_identity"] = model.get("artifacts", {}).get("embedding")
    factors["reranker_identity"] = model.get("artifacts", {}).get("reranker")
    factors["numeric_runtime"] = {key: model.get(key) for key in
                                   ("device", "dtype", "cuda_version", "numeric_environment",
                                    "normalization", "packages", "input_policy")}
    factors["numeric_runtime"]["cpu_threads"] = model.get("execution", {}).get("cpu_threads")
    manifest = {
        "schema_version": "semantic-ranking-ablation-v1",
        "group": measured.get("ranking_ablation_group"),
        "run_fingerprint": run.run_fingerprint,
        "shared": {
            "document_hash": measured["document_hash"],
            "ontology_hash": measured["ontology_semantic_hash"],
            "core_hash": measured["input_hashes"]["runtime"],
            "metadata_hash": measured["metadata_dependency_hash"],
            "scope": measured["scope"],
            "recognition_model_identity": run.model_identity,
            "budget": measured["execution_limits"],
            "query_policy_hash": evidence_hash("subject-slot-query-v1"),
            "view_policy_hash": evidence_hash("complete-record-view-v1"),
            "common_ranking_policy": {key: value for key, value in policy.items()
                                      if key not in factors},
            "ranking_numeric_configuration": {
                key: identity.get("configuration", {}).get(key, default)
                for key, default in (
                    ("device", "cpu"), ("dtype", "float32"), ("cuda_version", "12.6"),
                )
            },
            "recognition_settings": {key: value for key, value in
                                     measured["effective_settings"].items()
                                     if not key.startswith("semantic_ranking_")},
        },
        "factors": factors,
        "execution": {"completion": measured["completion"],
                      "unavailable_reason": identity.get("unavailable_reason")},
    }
    if epoch_id is not None:
        epochs = run.ranking.get("service", {}).get("epochs", [])
        epoch = next((item for item in epochs if item["epoch_id"] == epoch_id), None)
        if epoch is None:
            raise ValueError("fixed-pool epoch does not exist in the selected run")
        manifest["fixed_pool"] = {
            "query_content_hash": evidence_hash([
                {"intent": item["retrieval_intent"], "text": item["model_text"]}
                for item in epoch["queries"]
            ]),
            "pool_hash": epoch["pool_hash"], "pool_record_ids": epoch["record_ids"],
            "view_hashes": {item["record_id"]: item["retrieval_view_hash"]
                            for item in epoch.get("retrieval_views", [])},
        }
    return manifest


def summarize_costs(run: Any) -> dict:
    """Missing measurements stay unknown; inspection calls are not LLM calls."""
    ranking = getattr(run, "ranking", {}) or {}
    observations = ranking.get("model_observations", [])
    groups = {}
    for stage in ("count_tokens", "embed", "score_pairs"):
        rows = [row for row in observations if row.get("operation") == stage]
        groups[stage] = {
            "calls": len(rows),
            "failed_calls": sum(row.get("status") != "completed" for row in rows),
            "inputs": sum(row.get("input_count", 0) for row in rows),
            "tokens": (sum(row["input_tokens"] for row in rows)
                       if rows and all(row.get("input_tokens") is not None for row in rows)
                       else None),
            "elapsed_seconds": sum(row.get("elapsed_seconds", 0) for row in rows),
            "queue_seconds": (sum(row["queue_seconds"] for row in rows)
                              if rows and all("queue_seconds" in row for row in rows) else None),
            "compute_seconds": (sum(row["request_seconds"] for row in rows)
                                if rows and all("request_seconds" in row for row in rows)
                                else None),
        }
    return {
        "ranking": groups,
        "inspection_calls": len(run.adapter_calls),
        "recognition_model_calls": run.graph.progress.model_calls,
        "inspection_elapsed_seconds": sum(call.elapsed_seconds for call in run.adapter_calls),
        "summary_seconds": ranking.get("summary_seconds"),
        "peak_resources": ranking.get("peak_resources"),
        "cache_state": ranking.get("cache_state", "not_measured"),
        "ranking_measurement_status": "observed" if observations else "not_measured",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--k", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))  # noqa: E731
    result = score_retrieval_query(read(args.reference), read(args.observation), k=args.k)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
