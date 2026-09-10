"""Frozen-epoch A–D ranking experiments, separate from dynamic graph recognition.

The reference is accepted only by ``aggregate``. A single fixed pool cannot
measure phase interleaving, entity discovery, or final graph quality.
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import re
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.evaluation.cmc_benchmark import (
    digest_file,
    read_json,
    scheduler_token_totals,
    tree_digest,
    write_json,
)
from app.evaluation.quality_guided_variant import OntologyGuidedEvaluationResult
from app.evaluation.semantic_ranking_evaluation import (
    build_ablation_manifest,
    score_retrieval_query,
    validate_ablation_pair,
)
from app.models.model_request import LocalModelPool, LocalModelRequest
from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.evidence_identity import canonical_json, evidence_hash
from app.services.extraction.ontology_guided.retrieval_fusion import (
    rank_scores,
    reciprocal_rank_fusion,
)
from app.services.extraction.ontology_guided.retrieval_query import SubjectSlotQuery
from app.services.extraction.ontology_guided.retrieval_views import RetrievalView
from app.services.extraction.ontology_guided.semantic_reranker import (
    RankingEpoch,
    RankingPersistenceError,
    RankingPolicy,
)
from app.services.extraction.ontology_guided.semantic_retrieval import channel_orders, cosine_scores
from app.services.llm.model_runtime import ModelCancelled, check_cancelled, model_scope
from app.services.llm.semantic_ranking import configured_semantic_ranking

GROUPS = {
    group: {"mode": "deterministic" if group == "A" else "semantic",
            "enable_dense": group != "A", "enable_reranker": group in "CD",
            "phase_interleaving": group == "D"}
    for group in "ABCD"
}
METRICS = ("ndcg_at_k", "recall_at_k", "mrr")


def _seal(value):
    return {**value, "content_hash": evidence_hash(value)}


def _validate_seal(value, expected_hash, schema):
    if value.get("schema_version") != schema:
        raise ValueError("frozen artifact schema mismatch")
    content = {key: item for key, item in value.items() if key != "content_hash"}
    if value.get("content_hash") != expected_hash or evidence_hash(content) != expected_hash:
        raise ValueError("frozen artifact content hash mismatch")


def _frozen_model_configuration(pool):
    raw, identity = pool["model_configuration"], pool["model_identity"]
    defaults = {"device": "cpu", "dtype": "float32", "cuda_version": "12.6"}
    if set(defaults) - raw.keys() and identity.get("device", "cpu") != "cpu":
        raise ValueError("GPU fixed pool requires all numeric configuration fields frozen")
    config = {**defaults, **raw}
    device, dtype = config["device"], config["dtype"]
    if (not isinstance(device, str) or not re.fullmatch(r"cpu|cuda:(0|[1-9][0-9]*)", device)
            or dtype not in ("float32", "float16")
            or (device == "cpu" and dtype != "float32") or config["cuda_version"] != "12.6"):
        raise ValueError("invalid fixed-pool numeric configuration")
    expected = {"device": device, "dtype": dtype,
                "cuda_version": None if device == "cpu" else config["cuda_version"]}
    if device != "cpu" and (
        set(expected) - identity.keys() or not identity.get("numeric_environment")
    ):
        raise ValueError("GPU fixed pool requires a frozen numeric runtime inventory")
    if any(key in identity and identity[key] != value for key, value in expected.items()):
        raise ValueError("fixed-pool numeric configuration differs from frozen model inventory")
    return deepcopy(config)


def validate_pool(pool, expected_hash):
    _validate_seal(pool, expected_hash, "semantic-fixed-pool-v1")
    epoch = RankingEpoch.model_validate(pool["epoch"], strict=True)
    views = [RetrievalView.model_validate(item, strict=True) for item in epoch.retrieval_views]
    queries = [SubjectSlotQuery.model_validate(item, strict=True) for item in epoch.queries]
    if epoch.status != "committed" or epoch.degraded or epoch.reason:
        raise ValueError("fixed pool requires a complete committed nondegraded epoch")
    if not epoch.record_ids or [view.record_id for view in views] != epoch.record_ids:
        raise ValueError("fixed pool must retain every unique epoch record and view in order")
    if any(view.status != "complete" or view.omitted_refs or not view.source_refs
           or view.source_snapshot_hash != pool["manifest"]["shared"]["document_hash"]
           for view in views):
        raise ValueError("fixed pool contains incomplete or mismatched original views")
    if [query.retrieval_intent for query in queries] != ["discover", "counterevidence"]:
        raise ValueError("fixed pool must freeze both ordered query intents")
    if any(query.subject_ref.model_dump(mode="json") != epoch.subject_ref for query in queries):
        raise ValueError("fixed pool query subject differs from its frozen epoch")
    identity = pool["manifest"]["fixed_pool"]
    expected = {
        "query_content_hash": evidence_hash([
            {"intent": query.retrieval_intent, "text": query.model_text} for query in queries
        ]),
        "pool_hash": evidence_hash([(view.record_id, view.retrieval_view_hash) for view in views]),
        "pool_record_ids": epoch.record_ids,
        "view_hashes": {view.record_id: view.retrieval_view_hash for view in views},
    }
    if identity != expected or epoch.pool_hash != expected["pool_hash"]:
        raise ValueError("fixed pool query, content, or record identity drifted")
    if epoch.model_identity != pool["model_identity"]:
        raise ValueError("frozen epoch and model inventory differ")
    _frozen_model_configuration(pool)
    policy = RankingPolicy.model_validate(pool["policy"], strict=True)
    if epoch.policy_hash != evidence_hash(policy):
        raise ValueError("frozen epoch policy differs from the execution policy")
    return epoch, queries, {view.record_id: view for view in views}, policy


def export_pool(run_dir, epoch_id, output):
    """Export original query/view content only from a hash-verified active artifact."""
    run_dir, output = Path(run_dir), Path(output)
    measured = read_json(run_dir / "result.json")
    if measured.get("execution_status") != "finished":
        raise ValueError("fixed-pool source execution must have finalized successfully")
    if measured.get("output_hashes", {}).get("run.json") != digest_file(run_dir / "run.json"):
        raise ValueError("source run differs from its finalized output hash")
    run = OntologyGuidedEvaluationResult.model_validate(
        read_json(run_dir / "run.json"), strict=True,
    )
    if run.run_fingerprint != measured.get("run_fingerprint"):
        raise ValueError("source run fingerprint mismatch")
    epoch = next((item for item in run.ranking["service"]["epochs"]
                  if item["epoch_id"] == epoch_id), None)
    if epoch is None:
        raise ValueError("selected epoch is absent from the frozen run")
    identity = measured["ranking_identity"]
    if identity.get("unavailable_reason") or not identity.get("model_identity"):
        raise ValueError("fixed-pool export requires a verified semantic model inventory")
    value = _seal({
        "schema_version": "semantic-fixed-pool-v1", "epoch": deepcopy(epoch),
        "manifest": build_ablation_manifest(measured, run, epoch_id=epoch_id),
        "model_identity": identity["model_identity"],
        "model_configuration": identity["configuration"], "policy": identity["policy"],
        "source_run_hash": digest_file(run_dir / "run.json"),
        "query_id": f"fixed-pool:{evidence_hash([q['query_id'] for q in epoch['queries']])}",
        "authority": "retrieval_only", "reference_is_ranking_input": False,
    })
    validate_pool(value, value["content_hash"])
    if output.exists():
        raise FileExistsError("use a new frozen pool path")
    write_json(output, value)
    return value


def validate_protocol(protocol, expected_hash):
    _validate_seal(protocol, expected_hash, "semantic-fixed-pool-protocol-v1")
    rounds = protocol.get("rounds", [])
    if len(rounds) < 3 or not protocol.get("pool_hash") or not protocol.get("protocol_id"):
        raise ValueError("fixed-pool protocol requires an identity and at least three rounds")
    ids, run_ids = [], []
    for item in rounds:
        ids.append(item["round_id"])
        if set(item.get("run_ids", {})) != set(GROUPS):
            raise ValueError("every preregistered round requires groups A–D")
        run_ids.extend(item["run_ids"].values())
    if (not all(isinstance(item, str) and item for item in [*ids, *run_ids])
            or len(ids) != len(set(ids)) or len(run_ids) != len(set(run_ids))):
        raise ValueError("round and execution identities must be unique and nonempty")
    if type(protocol.get("k")) is not int or protocol["k"] < 1:
        raise ValueError("protocol K must be a positive integer")
    for pair in protocol.get("comparisons", []):
        if (pair.get("left") not in GROUPS or pair.get("right") not in GROUPS
                or pair["left"] == pair["right"] or pair.get("metric") not in METRICS
                or type(pair.get("minimum_mean_delta")) not in (int, float)
                or not math.isfinite(pair["minimum_mean_delta"])):
            raise ValueError("invalid preregistered paired comparison")
    for name, value in protocol.get("cost_limits", {}).items():
        if (name not in ("reserved_tokens", "elapsed_seconds")
                or type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("invalid preregistered cost limit")
    return {(item["round_id"], group): run_id for item in rounds
            for group, run_id in item["run_ids"].items()}


class _Execution:
    """Durable reservations plus a private scheduler for a single ranking run."""

    def __init__(self, output, policy):
        self.output, self.policy = output, policy
        self.model, self.tokens, self.reservations = None, {}, []
        self.model_identity = None
        self.started = time.monotonic()
        self.reserved_tokens = 0
        self.record_calls = {}
        self.bind = create_engine(f"sqlite:///{output / 'scheduler.sqlite3'}")
        LocalModelPool.__table__.create(self.bind)
        LocalModelRequest.__table__.create(self.bind)

    def snapshot(self):
        return {"reservations": deepcopy(self.reservations),
                "reserved_tokens": self.reserved_tokens,
                "elapsed_seconds": time.monotonic() - self.started,
                "model_observations": deepcopy(getattr(self.model, "observations", []))}

    def _check(self):
        check_cancelled()
        if self.model is not None and self.model.identity != self.model_identity:
            raise ValueError("runtime model identity changed during the fixed-pool experiment")
        if time.monotonic() - self.started >= self.policy.ranking_timeout:
            raise TimeoutError("fixed-pool ranking deadline exhausted")

    def _persist(self):
        try:
            write_json(self.output / "cost_state.json", self.snapshot())
        except (ModelCancelled, ExecutionLost):
            raise
        except Exception as exc:
            raise RankingPersistenceError("fixed-pool cost persistence failed") from exc

    def _request(self, operation, values, tokens, record_ids=()):
        self._check()
        if (tokens is not None and self.reserved_tokens + tokens > min(
                self.policy.max_ranking_tokens_per_run, self.policy.max_ranking_tokens_per_slot)):
            raise ValueError("fixed-pool ranking token budget exhausted")
        if any(self.record_calls.get(rid, 0) >= self.policy.max_model_calls_per_record
               for rid in record_ids):
            raise ValueError("fixed-pool record call budget exhausted")
        for rid in record_ids:
            self.record_calls[rid] = self.record_calls.get(rid, 0) + 1
        self.reserved_tokens += tokens or 0
        item = {"sequence": len(self.reservations) + 1, "operation": operation,
                "input_hash": evidence_hash(values),
                "input_count": 1 if operation == "count_tokens" else len(values),
                "input_tokens": tokens, "record_ids": list(record_ids), "status": "reserved"}
        self.reservations.append(item)
        # Failure here prevents dispatch; a failed request retains its prior charge.
        self._persist()
        try:
            result = getattr(self.model, operation)(values)
            self._check()
            if operation != "count_tokens" and len(result) != len(values):
                raise ValueError("fixed-pool model returned an incomplete batch")
            if operation == "embed":
                for vector in result:
                    cosine_scores(vector, {"self": vector})
            if operation == "score_pairs" and any(
                type(score) not in (int, float) or not math.isfinite(score) for score in result
            ):
                raise ValueError("fixed-pool model returned a nonfinite or invalid score")
            item["status"] = "completed"
            return result
        except BaseException as exc:
            item["status"], item["error_type"] = "failed", type(exc).__name__
            raise
        finally:
            self._persist()

    def token_count(self, text):
        if text not in self.tokens:
            value = self._request("count_tokens", text, None)
            if type(value) is not int or value < 0:
                raise ValueError("fixed-pool invalid token count")
            self.tokens[text] = value
        return self.tokens[text]

    def call(self, operation, values, record_ids=()):
        sizes = [sum(self.token_count(text) for text in pair)
                 for pair in values] if operation == "score_pairs" else [
                     self.token_count(text) for text in values]
        if any(size > self.policy.max_tokens_per_pair for size in sizes):
            raise ValueError("fixed-pool input exceeds the frozen complete-input limit")
        for attempt in range(self.policy.technical_retry_limit + 1):
            try:
                return self._request(operation, values, sum(sizes), record_ids)
            except (ModelCancelled, ExecutionLost, RankingPersistenceError,
                    KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                if attempt == self.policy.technical_retry_limit:
                    raise

    def finish(self):
        try:
            with Session(self.bind) as db:
                rows = [{"request_id": row.request_id, "run_id": row.run_id,
                         "task_id": row.task_id, "stage": row.stage, "status": row.status,
                         "metrics": row.metrics or {}}
                        for row in db.scalars(select(LocalModelRequest).order_by(
                            LocalModelRequest.sequence))]
        finally:
            self.bind.dispose()
        costs = self.snapshot()
        costs["scheduler"] = {
            "requests": len(rows),
            **scheduler_token_totals(rows),
        }
        write_json(self.output / "scheduler_requests.json", rows)
        write_json(self.output / "costs.json", costs)
        return costs


def _rank(pool, group, queries, views, execution):
    execution._check()
    ids, policy = pool["epoch"]["record_ids"], execution.policy
    query_vectors, vectors = {}, {}
    if group != "A":
        for offset in range(0, len(ids), policy.batch_size):
            batch = ids[offset:offset + policy.batch_size]
            vectors.update(zip(batch, execution.call(
                "embed", [views[rid].model_text for rid in batch]), strict=True))
        query_vectors = dict(zip([query.retrieval_intent for query in queries],
            execution.call("embed", [query.model_text for query in queries]), strict=True))
    ranks, observations = {}, []
    for query in queries:
        execution._check()
        intent = query.retrieval_intent
        if group in "CD":
            scores = {}
            for offset in range(0, len(ids), policy.batch_size):
                batch = ids[offset:offset + policy.batch_size]
                scores.update(zip(batch, execution.call("score_pairs", [
                    (query.model_text, views[rid].model_text) for rid in batch], batch),
                    strict=True))
        else:
            channels, _ = channel_orders(query, views, ids, dense_scores=(
                cosine_scores(query_vectors[intent], vectors) if group == "B" else None))
            channel_ranks = {name: {rid: rank for rank, rid in enumerate(order, 1)}
                             for name, order in channels.items()}
            _, scores = reciprocal_rank_fusion(channel_ranks, ids, smoothing=policy.rrf_smoothing)
        ranks[intent] = rank_scores(scores, ids)
        observations.extend({"intent": intent, "record_id": rid, "score": scores[rid],
                             "rank": ranks[intent][rid]} for rid in ids)
    ordered, fused = reciprocal_rank_fusion(ranks, ids, weights=policy.intent_weights,
                                            smoothing=policy.rrf_smoothing, require_complete=True)
    execution._check()
    return {"query_id": pool["query_id"],
            "document_hash": pool["manifest"]["shared"]["document_hash"],
            "query_content_hash": pool["manifest"]["fixed_pool"]["query_content_hash"],
            "pool_record_ids": ids, "ranking_record_ids": ordered,
            "intent_observations": observations, "fused_scores": fused,
            "execution_scope": "ranking_only", "authority": "retrieval_only",
            "dispatch_and_assembly": "not_executed"}


def run_group(pool, protocol, protocol_hash, round_id, group, output, *,
              model_factory=configured_semantic_ranking):
    planned = validate_protocol(protocol, protocol_hash)
    if (round_id, group) not in planned:
        raise ValueError("run group or round was not preregistered")
    _, queries, views, policy = validate_pool(pool, protocol["pool_hash"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    execution = _Execution(output, policy)
    manifest = deepcopy(pool["manifest"])
    manifest.update({"group": group, "round_id": round_id, "run_id": planned[round_id, group],
                     "execution_nonce": str(uuid4()), "protocol_hash": protocol_hash,
                     "pool_content_hash": pool["content_hash"]})
    manifest["factors"] = GROUPS[group]
    manifest["shared"]["experiment_code_hash"] = tree_digest(Path(__file__).parents[1], ".py")
    manifest["shared"]["fixed_model_inventory"] = pool["model_identity"]
    manifest["shared"]["numeric_environment"] = {
        "machine": platform.machine(), "python": platform.python_version(),
        **{key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                                               "OPENBLAS_NUM_THREADS", "TOKENIZERS_PARALLELISM",
                                               "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
                                               "CUBLAS_WORKSPACE_CONFIG")},
    }
    manifest["numeric_fingerprint_version"] = 1
    manifest["source_run_fingerprint"] = manifest["run_fingerprint"]
    manifest["run_fingerprint"] = evidence_hash([
        protocol_hash, manifest["run_id"], pool["content_hash"], manifest["factors"],
        manifest["shared"]["experiment_code_hash"],
        manifest["shared"]["numeric_environment"],
    ])
    manifest["execution"] = {"status": "running", "scope": "fixed_pool_ranking_only",
                             "phase_interleaving": "not_exercised_in_single_pool",
                             "failure_policy": "fail_complete_pool_without_fallback",
                             "graph_quality_gate": "not_evaluated"}
    write_json(output / "manifest.json", manifest)
    error = None
    try:
        with model_scope(bind=execution.bind, run_id=manifest["run_id"],
                         task_id=pool["epoch"]["epoch_id"], stage="fixed_pool_ranking"):
            execution._check()
            if group != "A":
                execution.model = model_factory(_frozen_model_configuration(pool))
                if execution.model is None or execution.model.identity != pool["model_identity"]:
                    raise ValueError("runtime model differs from the common frozen inventory")
                execution.model_identity = deepcopy(pool["model_identity"])
                deadline = getattr(execution.model, "set_deadline", None)
                if callable(deadline):
                    deadline(execution.started + policy.ranking_timeout)
            observation = _rank(pool, group, queries, views, execution)
            if execution.model is not None and execution.model.identity != pool["model_identity"]:
                raise ValueError("runtime model identity changed during the fixed-pool experiment")
            execution._check()
            write_json(output / "observation.json", observation)
    except BaseException as exc:
        error = exc
    finally:
        close = getattr(execution.model, "close", None)
        if callable(close):
            try:
                close()
            except BaseException as exc:
                error = error or exc
        try:
            execution.finish()
        except BaseException as exc:
            error = error or exc
        manifest["execution"]["status"] = "failed" if error else "finished"
        if error:
            manifest["execution"]["failure_type"] = type(error).__name__
        manifest["output_hashes"] = {
            path.name: digest_file(path) for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
            and not path.name.endswith((".tmp", "-journal", "-wal", "-shm"))
        }
        write_json(output / "manifest.json", manifest)
    if error:
        raise error
    return manifest


def aggregate(protocol, protocol_hash, run_dirs, *, pool, reference=None):
    planned = validate_protocol(protocol, protocol_hash)
    validate_pool(pool, protocol["pool_hash"])
    runs, nonces, request_ids = {}, set(), set()
    baseline = None
    for directory in map(Path, run_dirs):
        manifest = read_json(directory / "manifest.json")
        key = (manifest.get("round_id"), manifest.get("group"))
        if key not in planned or key in runs or manifest.get("run_id") != planned[key]:
            raise ValueError("duplicate, unregistered, or mismatched run identity")
        if (manifest.get("protocol_hash") != protocol_hash
                or manifest.get("pool_content_hash") != protocol["pool_hash"]):
            raise ValueError("experiment changed the preregistered protocol or pool")
        if (manifest.get("fixed_pool") != pool["manifest"]["fixed_pool"]
                or manifest.get("source_run_fingerprint") != pool["manifest"]["run_fingerprint"]
                or manifest.get("shared", {}).get("fixed_model_inventory") != pool["model_identity"]
                or any(canonical_json(manifest.get("shared", {}).get(name)) != canonical_json(value)
                       for name, value in pool["manifest"]["shared"].items())):
            raise ValueError("run changed a shared input frozen in the registered pool")
        if manifest.get("factors") != GROUPS[key[1]]:
            raise ValueError("group factors differ from their registered meaning")
        fingerprint_inputs = [
            protocol_hash, manifest["run_id"], pool["content_hash"], manifest["factors"],
            manifest["shared"]["experiment_code_hash"],
        ]
        numeric_version = manifest.get("numeric_fingerprint_version", 0)
        if type(numeric_version) is not int or numeric_version not in (0, 1):
            raise ValueError("unknown numeric runtime fingerprint version")
        if numeric_version:
            fingerprint_inputs.append(manifest["shared"]["numeric_environment"])
        elif _frozen_model_configuration(pool)["device"] != "cpu":
            raise ValueError("GPU fixed-pool execution omitted its numeric runtime fingerprint")
        expected_fingerprint = evidence_hash(fingerprint_inputs)
        if manifest.get("run_fingerprint") != expected_fingerprint:
            raise ValueError("run fingerprint no longer matches its registered execution")
        nonce = manifest.get("execution_nonce")
        if not nonce or nonce in nonces:
            raise ValueError("reused execution cannot count as an independent round")
        nonces.add(nonce)
        validate_ablation_pair(baseline or manifest, manifest,
                               allowed_factor_changes=list(GROUPS["A"]))
        baseline = baseline or manifest
        for name in ("costs.json", "scheduler_requests.json"):
            if name not in manifest.get("output_hashes", {}):
                raise ValueError("run omitted mandatory execution accounting")
        for name, frozen in manifest.get("output_hashes", {}).items():
            if Path(name).name != name or digest_file(directory / name) != frozen:
                raise ValueError("run output differs from its finalized artifact hash")
        requests = read_json(directory / "scheduler_requests.json")
        for request in requests:
            if (request["request_id"] in request_ids or request["run_id"] != manifest["run_id"]
                    or not request["task_id"]):
                raise ValueError("reused or unattributed scheduler requests")
            request_ids.add(request["request_id"])
        if manifest["execution"]["status"] == "finished" and (
            "observation.json" not in manifest.get("output_hashes", {})
        ):
            raise ValueError("completed execution omitted the frozen ranking observation")
        observation = (read_json(directory / "observation.json")
                       if manifest["execution"]["status"] == "finished" else None)
        costs = read_json(directory / "costs.json")
        if observation is not None:
            fixed = manifest["fixed_pool"]
            if (observation["pool_record_ids"] != fixed["pool_record_ids"]
                    or observation["query_id"] != pool["query_id"]
                    or observation["query_content_hash"] != fixed["query_content_hash"]
                    or observation["document_hash"] != manifest["shared"]["document_hash"]
                    or len(observation["ranking_record_ids"]) != len(fixed["pool_record_ids"])
                    or set(observation["ranking_record_ids"]) != set(fixed["pool_record_ids"])):
                raise ValueError("ranking omitted or changed the common query or pool")
            if key[1] != "A":
                operations = {row["operation"] for row in costs.get("reservations", [])
                              if row["status"] == "completed"}
                required = {"count_tokens", "embed"} | (
                    {"score_pairs"} if key[1] in "CD" else set())
                if not requests or not required.issubset(operations):
                    raise ValueError("semantic group omitted required model execution accounting")
        runs[key] = (manifest, costs, observation)
    if set(runs) != set(planned):
        raise ValueError("protocol has missing rounds or groups")
    reasons, scores = [], {}
    failed = [list(key) for key, (manifest, _, _) in runs.items()
              if manifest["execution"]["status"] != "finished"]
    if failed:
        reasons.append("failed_or_unfinished_executions")
    if reference is None:
        reasons.append("missing_expert_reference")
    else:
        if not protocol.get("reference_sha256") or digest_file(reference) != (
            protocol["reference_sha256"]
        ):
            raise ValueError("expert reference differs from the preregistered hash")
        reference = read_json(reference)
        review = reference.get("expert_review", {})
        if not all(review.get(key) for key in ("reviewer", "reviewed_at")) or (
                review.get("status") != "approved"):
            reasons.append("unapproved_expert_reference")
        for key, (_, _, observation) in runs.items():
            if observation is not None:
                scores[key] = score_retrieval_query(reference, observation, k=protocol["k"])
                if scores[key]["annotation_status"] != "complete":
                    reasons.append("incomplete_or_unresolved_annotation")
    if not protocol.get("comparisons") or not protocol.get("cost_limits"):
        reasons.append("quality_or_cost_thresholds_not_preregistered")
    comparisons = []
    for pair in protocol.get("comparisons", []):
        deltas = []
        for item in protocol["rounds"]:
            left = scores.get((item["round_id"], pair["left"]), {}).get(pair["metric"])
            right = scores.get((item["round_id"], pair["right"]), {}).get(pair["metric"])
            deltas.append(right - left if left is not None and right is not None else None)
        mean = sum(deltas) / len(deltas) if all(value is not None for value in deltas) else None
        if mean is None:
            reasons.append("undefined_or_unscored_paired_metric")
        elif mean < pair["minimum_mean_delta"]:
            reasons.append("paired_quality_threshold_not_met")
        comparisons.append({**pair, "round_deltas": deltas, "mean_delta": mean})
    exceeded = []
    for key, (_, costs, _) in runs.items():
        for name, limit in protocol.get("cost_limits", {}).items():
            value = costs.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or value > limit:
                exceeded.append({"round_id": key[0], "group": key[1], "metric": name,
                                 "measured": value, "limit": limit})
    if exceeded:
        reasons.append("cost_threshold_exceeded_or_unmeasured")
    return {"schema_version": "semantic-fixed-pool-protocol-result-v1",
            "protocol_hash": protocol_hash, "pool_hash": protocol["pool_hash"],
            "rounds": len(protocol["rounds"]), "runs": len(runs), "failed_runs": failed,
            "comparisons": comparisons, "cost_violations": exceeded,
            "scores": [{"round_id": key[0], "group": key[1], **value}
                       for key, value in scores.items()],
            "costs": [{"round_id": key[0], "group": key[1],
                       "reserved_tokens": costs.get("reserved_tokens"),
                       "elapsed_seconds": costs.get("elapsed_seconds"),
                       "scheduler": costs.get("scheduler"),
                       "execution_status": manifest["execution"]["status"]}
                      for key, (manifest, costs, _) in runs.items()],
            "retrieval_protocol_gate": "blocked_or_failed" if reasons else "pass",
            "gate_reasons": sorted(set(reasons)), "graph_quality_gate": "not_evaluated",
            "phase_interleaving_effect": "requires_separate_dynamic_frontier_protocol"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Freeze one complete active ranking epoch")
    export.add_argument("--run-dir", required=True)
    export.add_argument("--epoch-id", required=True)
    export.add_argument("--output", required=True)
    seal = commands.add_parser(
        "freeze-protocol", help="Validate and seal explicit round registration",
    )
    seal.add_argument("--input", required=True)
    seal.add_argument("--output", required=True)
    run = commands.add_parser("run", help="Execute one preregistered fixed-pool group")
    run.add_argument("--round-id", required=True)
    run.add_argument("--group", choices=list(GROUPS), required=True)
    summary = commands.add_parser(
        "aggregate", help="Audit all rounds and separately score reference",
    )
    summary.add_argument("--runs", nargs="+", required=True)
    summary.add_argument("--reference")
    for command in (run, summary):
        command.add_argument("--pool", required=True)
        command.add_argument("--protocol", required=True)
        command.add_argument("--protocol-hash", required=True)
        command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export_pool(args.run_dir, args.epoch_id, args.output)
    elif args.command == "freeze-protocol":
        result = _seal(read_json(args.input))
        validate_protocol(result, result["content_hash"])
        if Path(args.output).exists():
            raise FileExistsError("use a new frozen protocol path")
        write_json(args.output, result)
    elif args.command == "run":
        result = run_group(read_json(args.pool), read_json(args.protocol), args.protocol_hash,
                           args.round_id, args.group, args.output)
    else:
        result = aggregate(read_json(args.protocol), args.protocol_hash, args.runs,
                            pool=read_json(args.pool), reference=args.reference)
        if Path(args.output).exists():
            raise FileExistsError("use a new aggregate result path")
        write_json(args.output, result)
    print(canonical_json({key: result[key] for key in
                          ("content_hash", "run_id", "retrieval_protocol_gate") if key in result}))


if __name__ == "__main__":
    main()
