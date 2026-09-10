"""Bounded whole-pool semantic ranking with durable, permission-scoped replay."""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import Field, model_serializer, model_validator

from app.schemas.evidence import EvidenceModel
from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    GraphNode,
    MetadataSnapshot,
    RetrievalPlan,
    VersionedRef,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.retrieval_fusion import (
    rank_scores,
    reciprocal_rank_fusion,
)
from app.services.extraction.ontology_guided.retrieval_query import build_subject_queries
from app.services.extraction.ontology_guided.retrieval_views import (
    VIEW_POLICY_VERSION,
    build_retrieval_views,
)
from app.services.extraction.ontology_guided.semantic_retrieval import (
    channel_orders,
    cosine_scores,
    select_candidate_pool,
)
from app.services.llm.model_runtime import ModelCancelled


class SemanticRankingModel(Protocol):
    identity: dict[str, Any]

    def count_tokens(self, text: str) -> int: ...
    def count_tokens_batch(self, texts: list[str]) -> list[int]: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class RankingPolicy(EvidenceModel):
    mode: Literal["deterministic", "semantic"] = "deterministic"
    enable_dense: bool = True
    enable_reranker: bool = True
    phase_interleaving: bool = True
    failure_policy: Literal["deterministic", "pause"] = "deterministic"
    pool_size: int = Field(default=64, ge=1)
    batch_size: int = Field(default=16, ge=1)
    protected_quota: int = Field(default=8, ge=0)
    exploration_quota: int = Field(default=8, ge=0)
    structure_quota: int = Field(default=16, ge=0)
    dense_quota: int = Field(default=16, ge=0)
    metadata_quota: int = Field(default=16, ge=0)
    fill_candidate_pool: bool = True
    max_query_variants: int = Field(default=2, ge=2, le=2)
    max_windows_per_record: int = Field(default=1, ge=1, le=1)
    max_tokens_per_pair: int = Field(default=4096, ge=1)
    max_ranking_tokens_per_slot: int = Field(default=262144, ge=1)
    max_ranking_tokens_per_run: int = Field(default=2097152, ge=1)
    ranking_timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    technical_retry_limit: int = Field(default=1, ge=0, le=3)
    max_model_calls_per_record: int = Field(default=2, ge=1)
    rrf_smoothing: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    intent_weights: dict[str, float] = Field(
        default_factory=lambda: {"discover": 0.5, "counterevidence": 0.5}
    )
    policy_version: Literal["semantic-ranking-v1", "semantic-ranking-v2"] = "semantic-ranking-v1"

    @model_serializer(mode="wrap")
    def serialize_policy(self, handler):
        # Default runs retain their pre-experiment checkpoint and hash identity.
        # The experimental opt-out is explicit and participates in every hash.
        result = handler(self)
        if result.get("fill_candidate_pool") is True:
            result.pop("fill_candidate_pool")
        return result

    @model_validator(mode="before")
    @classmethod
    def versioned_record_limit(cls, value):
        # Absent versions remain historical v1. Only explicitly created v2 runs
        # receive two base intents, each with its own finite technical retries.
        if isinstance(value, dict) and value.get("policy_version") == "semantic-ranking-v2":
            value = dict(value)
            try:
                retry_limit = int(value.get("technical_retry_limit", 1))
            except (TypeError, ValueError):
                return value  # The normal field validator supplies the error.
            value.setdefault("max_model_calls_per_record", 2 * (1 + retry_limit))
        return value

    @model_validator(mode="after")
    def coherent_weights(self):
        if (
            set(self.intent_weights) != {"discover", "counterevidence"}
            or not all(math.isfinite(value) and value > 0 for value in self.intent_weights.values())
            or not math.isclose(sum(self.intent_weights.values()), 1.0)
        ):
            raise ValueError("intent weights must be positive and sum to one")
        return self


class RankingEpoch(EvidenceModel):
    epoch_id: str
    epoch_seq: int = Field(ge=1)
    plan_id: str
    subject_ref: dict
    query_dependency_hash: str
    ranking_dependency_hash: str
    pool_hash: str
    record_ids: list[str]
    ordered_record_ids: list[str]
    queries: list[dict]
    retrieval_views: list[dict] = Field(default_factory=list)
    observations: list[dict]
    model_identity: dict[str, Any]
    policy_hash: str
    permission_scope_hash: str
    actual_ranking_mode: Literal["deterministic", "semantic"]
    degraded: bool = False
    reason: str | None = None
    status: Literal["ready", "degraded", "paused", "committed"] = "ready"
    costs: dict = Field(default_factory=dict)
    authority: Literal["retrieval_only"] = "retrieval_only"

    @model_validator(mode="after")
    def exact_pool(self):
        if len(self.record_ids) != len(set(self.record_ids)) or (
            set(self.record_ids) != set(self.ordered_record_ids)
            or len(self.record_ids) != len(self.ordered_record_ids)
        ):
            raise ValueError("epoch order must cover its complete unique pool")
        return self


class RankingPaused(RuntimeError):
    pass


class RankingPersistenceError(RuntimeError):
    """A write barrier failed; model execution and technical fallback must stop."""


class RankingDispatchReceipt(EvidenceModel):
    """Append-only disposition of one already charged, exact request reservation."""

    sequence: int = Field(ge=1)
    request_key: str
    reservation_attempt: int = Field(ge=1)
    reservation_identity: str
    status: Literal["not_dispatched", "dispatch_claimed"]
    reason: Literal["ranking_timeout"] | None = None


def _zero_costs():
    return {
        "input_pairs": 0,
        "embedding_inputs": 0,
        "tokens": 0,
        "model_calls": 0,
        "technical_retries": 0,
        "cache_hits": 0,
        "token_cache_hits": 0,
        "elapsed_ms": 0.0,
    }


def _failure_reason(exc: Exception) -> str:
    # Third-party exceptions can contain local paths, request URLs or source text.
    allowed = {
        "ranking_timeout",
        "ranking_token_budget_exhausted",
        "ranking_call_budget_exhausted",
        "ranking_model_returned_incomplete_batch",
        "ranking_model_returned_nonfinite_score",
        "ranking_model_identity_changed",
        "ranking_input_too_long",
        "ranking_cuda_version_mismatch",
        "ranking_cuda_unavailable",
        "ranking_cuda_device_missing",
        "ranking_cuda_architecture_unsupported",
        "ranking_cuda_kernel_failed",
        "ranking_cuda_precision_override",
        "ranking_cuda_out_of_memory",
        "ranking_cuda_environment_changed",
        "ranking_cuda_probe_timeout",
        "ranking_cuda_probe_failed",
        "ranking_model_placement_mismatch",
        "ranking_configuration_changed",
        "ranking_environment_changed",
        "semantic_ranking_model_unavailable",
        "incomplete_view",
    }
    return str(exc) if str(exc) in allowed else f"ranking_technical_failure:{type(exc).__name__}"


def _immutable_mutation(*args, **kwargs):
    raise TypeError("ranking snapshot data is immutable")


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable_mutation
    __ior__ = _immutable_mutation

    def __deepcopy__(self, memo):
        return self


class _FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = _immutable_mutation
    remove = reverse = sort = __iadd__ = __imul__ = _immutable_mutation

    def __deepcopy__(self, memo):
        return self


def _freeze(value):
    """Detach once, then share JSON-compatible immutable state across snapshots."""
    if isinstance(value, (_FrozenDict, _FrozenList)):
        return value
    if isinstance(value, dict):
        return _FrozenDict((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return _FrozenList(_freeze(item) for item in value)
    return value


class RankingService:
    def __init__(
        self,
        policy: RankingPolicy,
        model=None,
        state: dict | None = None,
        before_model_hook: Callable[[dict], None] | None = None,
        budget_enabled: bool | None = None,
    ):
        self.policy = policy
        self.model = model
        self.before_model_hook = before_model_hook
        self._budget_enabled = (
            (state or {}).get("budget_enabled", True)
            if budget_enabled is None else budget_enabled
        )
        if type(self.budget_enabled) is not bool:
            raise ValueError("ranking budget control must be boolean")
        self._model_observation_start = len(getattr(model, "observations", [])) if state else 0
        self.model_identity = copy.deepcopy(getattr(model, "identity", {}))
        self.epochs: list[RankingEpoch] = []
        self.costs = _zero_costs()
        self.slot_costs: dict[str, int] = {}
        self._cache: dict[str, list[float]] = {}
        self._score_cache: dict[str, list[float]] = {}
        self._token_cache: dict[str, int] = {}
        self._record_call_counts: dict[str, int] = {}
        self._record_intent_call_counts: dict[str, int] = {}
        self._request_attempts: dict[str, int] = {}
        self._dispatch_receipts: list[RankingDispatchReceipt] = []
        self._pending: dict[str, RankingEpoch] = {}
        self._paused_attempts: list[RankingEpoch] = []
        self._retryable_pending: set[str] = set()
        self._restored_model_observations = []
        self._cache_snapshots: dict[str, tuple[int, dict]] = {}
        self._epoch_snapshots: list[dict] = []
        self._paused_snapshots: list[dict] = []
        self._retrieval_view_cache: dict[str, dict] = {}
        if state:
            if state.get("policy") != policy.model_dump(mode="json"):
                raise ValueError("ranking policy changed during recovery")
            if state.get("model_identity") != self.model_identity:
                raise ValueError("ranking model identity changed during recovery")
            self.epochs = [RankingEpoch.model_validate(item) for item in state.get("epochs", [])]
            self.costs = {**_zero_costs(), **state.get("costs", {})}
            self.slot_costs = dict(state.get("slot_costs", {}))
            self._cache = {key: _freeze(value) for key, value in state.get("cache", {}).items()}
            self._score_cache = {
                key: _freeze(value) for key, value in state.get("score_cache", {}).items()
            }
            self._token_cache = dict(state.get("token_cache", {}))
            self._record_call_counts = dict(state.get("record_call_counts", {}))
            self._record_intent_call_counts = dict(state.get("record_intent_call_counts", {}))
            self._request_attempts = dict(state.get("request_attempts", {}))
            self._dispatch_receipts = [
                RankingDispatchReceipt.model_validate(item)
                for item in state.get("dispatch_receipts", [])
            ]
            previous_receipts: dict[str, RankingDispatchReceipt] = {}
            for sequence, receipt in enumerate(self._dispatch_receipts, 1):
                previous_receipt = previous_receipts.get(receipt.request_key)
                same_reservation = (
                    previous_receipt is not None
                    and previous_receipt.reservation_attempt == receipt.reservation_attempt
                    and previous_receipt.reservation_identity == receipt.reservation_identity
                )
                if receipt.status == "dispatch_claimed":
                    valid_transition = (
                        same_reservation and previous_receipt.status == "not_dispatched"
                    )
                else:
                    valid_transition = (
                        previous_receipt is None
                        or receipt.reservation_attempt > previous_receipt.reservation_attempt
                        or (same_reservation and previous_receipt.status == "dispatch_claimed")
                    )
                if (
                    receipt.sequence != sequence
                    or receipt.reservation_attempt
                    > self._request_attempts.get(receipt.request_key, 0)
                    or (receipt.status == "not_dispatched") != (receipt.reason is not None)
                    or not valid_transition
                ):
                    raise ValueError("ranking dispatch receipt has no matching reservation")
                previous_receipts[receipt.request_key] = receipt
            self._restored_model_observations = list(state.get("model_observations", []))
            self._pending = {
                item.plan_id: item
                for item in (
                    RankingEpoch.model_validate(raw) for raw in state.get("pending_epochs", [])
                )
            }
            self._paused_attempts = [
                RankingEpoch.model_validate(raw) for raw in state.get("paused_attempts", [])
            ]
            self._retryable_pending = {
                key for key, epoch in self._pending.items() if epoch.status == "paused"
            }
            self._epoch_snapshots = [_freeze(item) for item in state.get("epochs", [])]
            self._paused_snapshots = [_freeze(item) for item in state.get("paused_attempts", [])]

    @property
    def budget_enabled(self) -> bool:
        return self._budget_enabled

    def snapshot(self) -> dict:
        # Large immutable payloads are detached only at insertion. Snapshotting
        # a reservation copies the small mutable ledgers, never all vectors or
        # serializes every previously committed epoch again.
        caches = {}
        for name, values in (
            ("cache", self._cache), ("score_cache", self._score_cache),
            ("token_cache", self._token_cache),
        ):
            previous = self._cache_snapshots.get(name)
            if previous is None or previous[0] != len(values):
                previous = (len(values), _FrozenDict(values))
                self._cache_snapshots[name] = previous
            caches[name] = previous[1]
        state = {
            "policy": self.policy.model_dump(mode="json"),
            "budget_enabled": self.budget_enabled,
            "model_identity": self.model_identity,
            "pending_epochs": [
                epoch.model_dump(mode="json") for epoch in self._pending.values()
            ],
            "costs": self.costs,
            "slot_costs": self.slot_costs,
            "record_call_counts": self._record_call_counts,
            "request_attempts": self._request_attempts,
            "dispatch_receipts": [
                receipt.model_dump(mode="json") for receipt in self._dispatch_receipts
            ],
            "model_observations": [
                *self._restored_model_observations,
                *(getattr(self.model, "observations", [])[self._model_observation_start :]
                  if self.budget_enabled else []),
            ],
        }
        if self.policy.policy_version == "semantic-ranking-v2":
            state["record_intent_call_counts"] = self._record_intent_call_counts
        result = copy.deepcopy(state)
        result.update(caches)
        result["epochs"] = _FrozenList(self._epoch_snapshots)
        result["paused_attempts"] = _FrozenList(self._paused_snapshots)
        return result

    def fork(self, *, before_model_hook=None):
        """Private mutable bookkeeping; only frozen snapshot payloads are shared."""
        result = RankingService(
            self.policy.model_copy(deep=True), self.model, state=self.snapshot(),
            before_model_hook=before_model_hook,
        )
        result._retrieval_view_cache = copy.deepcopy(self._retrieval_view_cache)
        return result

    def _views(self, index, metadata, *, scope_hash, count_tokens, count_tokens_batch=None):
        key = evidence_hash([
            index.ir.analysis_id, index.ir.document_hash, index.ir.structure_hash,
            index.ir.ir_version, index.ir.parser_version, index.ir.structure_policy_version,
            metadata, VIEW_POLICY_VERSION, self.policy.max_tokens_per_pair,
            scope_hash, self.model_identity,
            "validation" if count_tokens is len else self.policy.mode,
            "max-tokenizers-special-true-padding-false-truncation-false-v1",
        ])
        if key not in self._retrieval_view_cache:
            self._retrieval_view_cache[key] = build_retrieval_views(
                index, metadata, count_tokens=count_tokens,
                count_tokens_batch=count_tokens_batch,
                max_record_tokens=self.policy.max_tokens_per_pair,
            )
        return self._retrieval_view_cache[key]

    def commit_epoch(self, epoch: RankingEpoch) -> RankingEpoch:
        existing = next((item for item in self.epochs if item.epoch_id == epoch.epoch_id), None)
        if existing:
            if existing.model_copy(update={"status": epoch.status}) != epoch:
                raise ValueError("committed ranking epoch cannot change")
            return existing
        if epoch.status == "paused":
            raise RankingPaused(epoch.reason or "ranking unavailable")
        if epoch.policy_hash != evidence_hash(self.policy) or epoch.model_identity != (
            self.model_identity
        ):
            raise ValueError("ranking epoch policy or model mismatch")
        prepared = self._pending.get(epoch.plan_id)
        if prepared != epoch:
            raise ValueError("ranking epoch is not the exact prepared result")
        committed = epoch.model_copy(update={"status": "committed"})
        self.epochs.append(committed)
        self._epoch_snapshots.append(_freeze(committed.model_dump(mode="json")))
        self._pending.pop(epoch.plan_id, None)
        return committed

    def validate_epoch(
        self,
        epoch: RankingEpoch,
        *,
        plan: RetrievalPlan,
        index: RecordIndex,
        metadata: MetadataSnapshot,
        subject_node: GraphNode,
        predicate,
        run_fingerprint: str,
        root_ref: VersionedRef,
        root_class_iri: str,
        permission_scope: str,
        mentions=None,
        dependency_refs=None,
        candidate_record_ids: list[str] | None = None,
    ) -> None:
        """Recheck a durable result against current dependencies without model execution."""
        from app.services.extraction.ontology_guided.retrieval import validate_record_universe

        validate_record_universe(plan, index)
        if candidate_record_ids is not None and (
            len(candidate_record_ids) != len(set(candidate_record_ids))
            or not set(candidate_record_ids).issubset(plan.frozen_record_ids)
        ):
            raise ValueError("candidate scope is outside the frozen record universe")
        queries = build_subject_queries(
            subject=plan.subject,
            subject_node=subject_node,
            predicate=predicate,
            index=index,
            run_fingerprint=run_fingerprint,
            root_ref=root_ref,
            root_class_iri=root_class_iri,
            mentions=mentions,
            dependency_refs=dependency_refs,
        )
        views = self._views(
            index, metadata, scope_hash=evidence_hash(permission_scope), count_tokens=len,
        )
        if (
            epoch.plan_id != plan.plan_id
            or epoch.subject_ref != plan.subject.model_dump(mode="json")
            or epoch.query_dependency_hash != queries[0].query_dependency_hash
            or epoch.queries != [query.model_dump(mode="json") for query in queries]
            or epoch.policy_hash != evidence_hash(self.policy)
            or epoch.model_identity != self.model_identity
            or epoch.permission_scope_hash != evidence_hash(permission_scope)
            or not set(epoch.record_ids).issubset(views)
            or (
                candidate_record_ids is not None
                and not set(epoch.record_ids).issubset(candidate_record_ids)
            )
            or epoch.pool_hash
            != evidence_hash([(rid, views[rid].retrieval_view_hash) for rid in epoch.record_ids])
        ):
            raise ValueError("ranking epoch dependencies or permission scope changed")

    def prepare_next_epoch(
        self,
        *,
        plan: RetrievalPlan,
        index: RecordIndex,
        metadata: MetadataSnapshot,
        subject_node: GraphNode,
        predicate,
        run_fingerprint: str,
        root_ref: VersionedRef,
        root_class_iri: str,
        permission_scope: str,
        required_record_ids: list[str] | None = None,
        mentions=None,
        dependency_refs=None,
        candidate_record_ids: list[str] | None = None,
    ) -> RankingEpoch | None:
        from app.services.extraction.ontology_guided.retrieval import validate_record_universe

        validate_record_universe(plan, index)
        candidate_scope = set(candidate_record_ids) if candidate_record_ids is not None else None
        if candidate_scope is not None and (
            len(candidate_record_ids) != len(candidate_scope)
            or not candidate_scope.issubset(plan.frozen_record_ids)
        ):
            raise ValueError("candidate scope is outside the frozen record universe")
        scope_hash = evidence_hash(permission_scope)
        previous = [item for item in self.epochs if item.plan_id == plan.plan_id]
        pending = self._pending.get(plan.plan_id)
        for epoch in [*previous, *([pending] if pending else [])]:
            self.validate_epoch(
                epoch,
                plan=plan,
                index=index,
                metadata=metadata,
                subject_node=subject_node,
                predicate=predicate,
                run_fingerprint=run_fingerprint,
                root_ref=root_ref,
                root_class_iri=root_class_iri,
                permission_scope=permission_scope,
                mentions=mentions,
                dependency_refs=dependency_refs,
            )
        if pending:
            if (
                plan.plan_id not in self._retryable_pending
                or (self.budget_enabled and pending.reason in {
                    "ranking_token_budget_exhausted", "ranking_call_budget_exhausted",
                })
                or (pending.reason == "semantic_ranking_model_unavailable" and self.model is None)
            ):
                return pending
            # A restored pause is an incomplete attempt, not a reusable result.
            # Preserve its evidence and every reservation before retrying under
            # the same cumulative limits. Repeated prepares in this service do
            # not implicitly retry another failure; a new restore is required.
            self._paused_attempts.append(pending)
            self._paused_snapshots.append(_freeze(pending.model_dump(mode="json")))
            self._pending.pop(plan.plan_id)
            self._retryable_pending.discard(plan.plan_id)
        used = {rid for epoch in previous for rid in epoch.record_ids}
        available = [
            item.record_id
            for item in plan.records
            if item.record_id not in used
            and plan.ledger[item.record_id].coverage_state == "unattempted"
            and (candidate_scope is None or item.record_id in candidate_scope)
        ]
        if not available:
            return None
        queries = build_subject_queries(
            subject=plan.subject,
            subject_node=subject_node,
            predicate=predicate,
            index=index,
            run_fingerprint=run_fingerprint,
            root_ref=root_ref,
            root_class_iri=root_class_iri,
            mentions=mentions,
            dependency_refs=dependency_refs,
        )
        slot_key = evidence_hash(
            [scope_hash, plan.subject.entity_id, predicate.iri]
            if self.policy.policy_version == "semantic-ranking-v2"
            else [plan.subject.entity_id, predicate.iri]
        )
        started = time.monotonic()
        costs = _zero_costs()
        if not self.budget_enabled:
            costs["budget_accounted"] = False
        raw_token_counter = (
            self.model.count_tokens
            if self.model is not None and self.policy.mode == "semantic"
            else len
        )
        set_deadline = getattr(self.model, "set_deadline", None)
        if callable(set_deadline):
            set_deadline(started + self.policy.ranking_timeout)

        def check_deadline():
            if time.monotonic() - started >= self.policy.ranking_timeout:
                raise TimeoutError("ranking_timeout")

        def token_counters(texts):
            check_deadline()
            keys = [
                evidence_hash([scope_hash, self.model_identity, text, "tokens"])
                if self.policy.policy_version == "semantic-ranking-v1"
                else evidence_hash([
                    scope_hash, self.model_identity, text, self.policy.mode,
                    "max-tokenizers-special-true-padding-false-truncation-false-v1",
                ]) for text in texts
            ]
            missing = dict(
                (key, text) for key, text in zip(keys, texts, strict=True)
                if key not in self._token_cache
            )
            if self.budget_enabled:
                hits = len(keys) - len(missing)
                costs["token_cache_hits"] += hits
                self.costs["token_cache_hits"] += hits
            raw_batch = getattr(self.model, "count_tokens_batch", None)
            uncached = list(missing.items())
            for offset in range(0, len(uncached), self.policy.batch_size):
                check_deadline()
                batch = uncached[offset:offset + self.policy.batch_size]
                if callable(raw_batch) and self.policy.mode == "semantic":
                    values = raw_batch([text for _, text in batch])
                else:
                    values = []
                    for _, text in batch:
                        check_deadline()
                        values.append(raw_token_counter(text))
                        check_deadline()
                check_deadline()
                if len(values) != len(batch) or any(
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                    for value in values
                ):
                    raise ValueError("ranking_invalid_token_count")
                self._token_cache.update(
                    (key, value) for (key, _), value in zip(batch, values, strict=True)
                )
                self._cache_snapshots.pop("token_cache", None)
            return [self._token_cache[key] for key in keys]

        def token_counter(text):
            return token_counters([text])[0]

        reason = None
        try:
            views = self._views(
                index,
                metadata,
                scope_hash=scope_hash,
                count_tokens=token_counter,
                count_tokens_batch=token_counters,
            )
            max_query_tokens = max(token_counters([query.model_text for query in queries]))
        except (ModelCancelled, ExecutionLost):
            raise
        except Exception as exc:
            reason = _failure_reason(exc)
            token_counter = len
            views = self._views(
                index, metadata, scope_hash=scope_hash, count_tokens=len,
            )
            max_query_tokens = max(len(query.model_text) for query in queries)
        eligible = [
            rid
            for rid in available
            if views[rid].status == "complete"
            and views[rid].token_count + max_query_tokens <= self.policy.max_tokens_per_pair
        ]
        ineligible = set(available) - set(eligible)
        ordinary_exploration = (
            reason is None
            and self.policy.mode == "semantic"
            and self.model is not None
            and not eligible
        )
        signals = {}
        channels = {}

        def charge(tokens, *, pairs=0, embeddings=0):
            if time.monotonic() - started > self.policy.ranking_timeout:
                raise TimeoutError("ranking_timeout")
            if not self.budget_enabled:
                return
            if (
                tokens + self.slot_costs.get(slot_key, 0)
                > (self.policy.max_ranking_tokens_per_slot)
                or tokens + self.costs["tokens"] > self.policy.max_ranking_tokens_per_run
            ):
                raise ValueError("ranking_token_budget_exhausted")
            self.slot_costs[slot_key] = self.slot_costs.get(slot_key, 0) + tokens
            for key, value in {
                "tokens": tokens,
                "input_pairs": pairs,
                "embedding_inputs": embeddings,
                "model_calls": 1,
            }.items():
                costs[key] += value
                self.costs[key] += value

        def model_call(method, values, *, pairs=False, record_ids=(), intent=None):
            request_key = evidence_hash(
                [
                    scope_hash,
                    slot_key,
                    self.model_identity,
                    "pairs" if pairs else "embeddings",
                    values,
                ]
            )
            score_key = evidence_hash([
                request_key, run_fingerprint, plan.plan_id, queries[0].query_dependency_hash,
                [(rid, views[rid].retrieval_view_hash) for rid in record_ids],
            ])
            if pairs and score_key in self._score_cache:
                if self.budget_enabled:
                    costs["cache_hits"] += len(values)
                    self.costs["cache_hits"] += len(values)
                return list(self._score_cache[score_key])
            token_count = (
                sum(token_counter(left) + token_counter(right) for left, right in values)
                if pairs
                else sum(token_counter(value) for value in values)
            )
            record_keys = [evidence_hash([scope_hash, slot_key, rid]) for rid in record_ids]
            intent_keys = (
                [evidence_hash([scope_hash, slot_key, rid, intent]) for rid in record_ids]
                if self.policy.policy_version == "semantic-ranking-v2" else []
            )
            reservation_identity = evidence_hash([
                request_key, score_key, record_keys, token_count, len(values), pairs,
            ])

            def persist_dispatch_state():
                if self.before_model_hook is not None:
                    try:
                        self.before_model_hook(self.snapshot())
                    except (ModelCancelled, ExecutionLost):
                        raise
                    except Exception as exc:
                        raise RankingPersistenceError("ranking_cost_commit_failed") from exc

            def record_dispatch(status, *, reason=None):
                self._dispatch_receipts.append(RankingDispatchReceipt(
                    sequence=len(self._dispatch_receipts) + 1,
                    request_key=request_key,
                    reservation_attempt=self._request_attempts[request_key],
                    reservation_identity=reservation_identity,
                    status=status,
                    reason=reason,
                ))

            for attempt in range(self.policy.technical_retry_limit + 1):
                prior_attempts = self._request_attempts.get(request_key, 0)
                receipt = next(
                    (item for item in reversed(self._dispatch_receipts)
                     if item.request_key == request_key),
                    None,
                )
                reusable = (
                    receipt is not None and receipt.status == "not_dispatched"
                    and receipt.reservation_attempt == prior_attempts
                    and receipt.reservation_identity == reservation_identity
                )
                if reusable and self.budget_enabled:
                    # Claim the existing reservation durably before dispatch. A
                    # crash after this barrier is ambiguous and must not make it
                    # reusable again without a new definite no-dispatch receipt.
                    record_dispatch("dispatch_claimed")
                elif self.budget_enabled:
                    if any(
                        self._record_call_counts.get(key, 0)
                        >= self.policy.max_model_calls_per_record for key in record_keys
                    ):
                        raise ValueError("ranking_call_budget_exhausted")
                    if prior_attempts >= self.policy.technical_retry_limit + 1:
                        raise ValueError("ranking_call_budget_exhausted")
                    if any(
                        self._record_intent_call_counts.get(key, 0)
                        >= self.policy.technical_retry_limit + 1 for key in intent_keys
                    ):
                        raise ValueError("ranking_call_budget_exhausted")
                    charge(
                        token_count,
                        pairs=len(values) if pairs else 0,
                        embeddings=0 if pairs else len(values),
                    )
                    self._request_attempts[request_key] = prior_attempts + 1
                    if prior_attempts:
                        costs["technical_retries"] += 1
                        self.costs["technical_retries"] += 1
                    for key in record_keys:
                        self._record_call_counts[key] = self._record_call_counts.get(key, 0) + 1
                    for key in intent_keys:
                        self._record_intent_call_counts[key] = (
                            self._record_intent_call_counts.get(key, 0) + 1
                        )
                persist_dispatch_state()
                try:
                    check_deadline()
                except TimeoutError:
                    # The acknowledged durability wait can outlast the epoch.
                    # method has definitely not been called in this process.
                    # Keep all gross charges; only this exact reservation may
                    # later be claimed, under the original policy and identity.
                    if self.budget_enabled:
                        record_dispatch("not_dispatched", reason="ranking_timeout")
                        persist_dispatch_state()
                    raise
                try:
                    result = method(values)
                    if len(result) != len(values):
                        raise ValueError("ranking_model_returned_incomplete_batch")
                    if pairs and not all(math.isfinite(value) for value in result):
                        raise ValueError("ranking_model_returned_nonfinite_score")
                    if getattr(self.model, "identity", {}) != self.model_identity:
                        raise ValueError("ranking_model_identity_changed")
                    if time.monotonic() - started > self.policy.ranking_timeout:
                        raise TimeoutError("ranking_timeout")
                    if pairs:
                        self._score_cache[score_key] = _freeze(result)
                        self._cache_snapshots.pop("score_cache", None)
                    return result
                except (ModelCancelled, ExecutionLost):
                    raise
                except Exception:
                    if attempt == self.policy.technical_retry_limit:
                        raise

        def embeddings(texts):
            keys = [
                evidence_hash([scope_hash, text, self.model_identity, "embedding"])
                for text in texts
            ]
            missing = [
                (key, text) for key, text in zip(keys, texts, strict=True) if key not in self._cache
            ]
            if self.budget_enabled:
                costs["cache_hits"] += len(texts) - len(missing)
                self.costs["cache_hits"] += len(texts) - len(missing)
            for offset in range(0, len(missing), self.policy.batch_size):
                batch = missing[offset : offset + self.policy.batch_size]
                vectors = model_call(self.model.embed, [text for _, text in batch])
                for (key, _), vector in zip(batch, vectors, strict=True):
                    # Validation happens before a cache entry can be reused.
                    cosine_scores(vector, {"self": vector})
                    self._cache[key] = _freeze(vector)
                self._cache_snapshots.pop("cache", None)
            return [self._cache[key] for key in keys]

        try:
            if reason:
                raise ValueError(reason)
            if self.policy.mode == "semantic" and self.model is None:
                raise ValueError("semantic_ranking_model_unavailable")
            dense = {}
            if self.policy.mode == "semantic" and self.policy.enable_dense and eligible:
                vectors = embeddings([views[rid].model_text for rid in eligible])
                query_vectors = embeddings([query.model_text for query in queries])
                dense = {
                    query.retrieval_intent: cosine_scores(
                        vector, dict(zip(eligible, vectors, strict=True))
                    )
                    for query, vector in zip(queries, query_vectors, strict=True)
                }
            for query in queries:
                orders, raw = channel_orders(
                    query, views, eligible, dense_scores=dense.get(query.retrieval_intent)
                )
                for channel, order in orders.items():
                    channels[f"{channel}:{query.retrieval_intent}"] = order
                    signals[f"{channel}:{query.retrieval_intent}"] = raw[channel]
        except (ModelCancelled, ExecutionLost):
            raise
        except Exception as exc:
            if isinstance(exc, RankingPersistenceError):
                raise
            reason = reason or _failure_reason(exc)

        channel_quotas = {}
        for channel in ("structure", "dense", "metadata"):
            quota = getattr(self.policy, f"{channel}_quota")
            for number, query in enumerate(queries):
                channel_quotas[f"{channel}:{query.retrieval_intent}"] = quota // len(queries) + int(
                    number < quota % len(queries)
                )
        # Non-rerankable records stay in U and receive ordinary/exploration turns.
        candidate_ids = (
            eligible
            if self.policy.mode == "semantic" and eligible and reason is None
            else available
        )
        pool, protection = select_candidate_pool(
            candidate_ids,
            channels,
            pool_size=self.policy.pool_size,
            protected_ids=required_record_ids or [],
            exploration_ids=[rid for rid in plan.frozen_record_ids if rid in candidate_ids],
            quotas={
                "protected": self.policy.protected_quota,
                "exploration": self.policy.exploration_quota,
                **channel_quotas,
            },
            fill_pool=self.policy.fill_candidate_pool,
        )
        # Tie breaking and every degraded epoch use the frozen deterministic order.
        base_pool = [rid for rid in available if rid in set(pool)]
        ordered = list(base_pool)
        raw_by_intent = {}
        intent_ranks = {}
        fused_scores = {}
        if (
            self.policy.mode == "semantic"
            and self.policy.enable_reranker
            and reason is None
            and not ordinary_exploration
            and (pool or self.policy.fill_candidate_pool)
        ):
            try:
                if not pool or any(rid in ineligible for rid in pool):
                    raise ValueError("incomplete_view")
                for query in queries:
                    values = {}
                    for offset in range(0, len(base_pool), self.policy.batch_size):
                        batch = base_pool[offset : offset + self.policy.batch_size]
                        scores = model_call(
                            self.model.score_pairs,
                            [(query.model_text, views[rid].model_text) for rid in batch],
                            pairs=True,
                            record_ids=batch,
                            intent=query.retrieval_intent,
                        )
                        values.update(zip(batch, scores, strict=True))
                    raw_by_intent[query.retrieval_intent] = values
                    intent_ranks[query.retrieval_intent] = rank_scores(values, base_pool)
                ordered, fused_scores = reciprocal_rank_fusion(
                    intent_ranks,
                    base_pool,
                    weights=self.policy.intent_weights,
                    smoothing=self.policy.rrf_smoothing,
                    require_complete=True,
                )
            except (ModelCancelled, ExecutionLost):
                raise
            except Exception as exc:
                if isinstance(exc, RankingPersistenceError):
                    raise
                reason = _failure_reason(exc)
        elif reason is None and channels:
            # Common baseline uses trusted subject terms, while phase membership stays frozen.
            for query in queries:
                intent = query.retrieval_intent
                ranks = {
                    name: {rid: rank for rank, rid in enumerate(order, 1) if rid in pool}
                    for name, order in channels.items()
                    if name.endswith(f":{intent}")
                }
                _, scores = reciprocal_rank_fusion(
                    ranks,
                    base_pool,
                    smoothing=self.policy.rrf_smoothing,
                )
                intent_ranks[intent] = rank_scores(scores, base_pool)
            ordered, fused_scores = reciprocal_rank_fusion(
                intent_ranks,
                base_pool,
                weights=self.policy.intent_weights,
                smoothing=self.policy.rrf_smoothing,
                require_complete=True,
            )
        if reason:
            ordered = list(base_pool)
            fused_scores = {}
        if self.budget_enabled:
            costs["elapsed_ms"] = (time.monotonic() - started) * 1000
            self.costs["elapsed_ms"] += costs["elapsed_ms"]
        pool_hash = evidence_hash([(rid, views[rid].retrieval_view_hash) for rid in base_pool])
        policy_hash = evidence_hash(self.policy)
        epoch_seq = len(self.epochs) + 1
        identity = [
            plan.plan_id,
            epoch_seq,
            pool_hash,
            queries[0].query_dependency_hash,
            policy_hash,
            self.model_identity,
            scope_hash,
        ]
        epoch_id = stable_id("ranking-epoch", identity)
        positions = {rid: rank for rank, rid in enumerate(ordered, 1)}
        observations = []
        for query in queries:
            intent = query.retrieval_intent
            for rid in base_pool:
                observations.append(
                    {
                        "observation_id": stable_id("ranking-observation", [epoch_id, intent, rid]),
                        "ranking_epoch_id": epoch_id,
                        "record_id": rid,
                        "query_id": query.query_id,
                        "retrieval_intent": intent,
                        "model_input_hash": evidence_hash(
                            [query.model_text, views[rid].model_text]
                        ),
                        "retrieval_view_hash": views[rid].retrieval_view_hash,
                        "source_snapshot_hash": views[rid].source_snapshot_hash,
                        "view_status": "not_rerankable" if rid in ineligible else "complete",
                        "omitted_refs": [],
                        "window_count": 1,
                        "channel_hits": [name for name, raw in signals.items() if rid in raw],
                        "channel_raw_scores": {
                            name: raw[rid] for name, raw in signals.items() if rid in raw
                        },
                        "channel_ranks": {
                            name: order.index(rid) + 1
                            for name, order in channels.items()
                            if rid in order
                        },
                        "raw_rerank_score": raw_by_intent.get(intent, {}).get(rid),
                        "intent_rank": intent_ranks.get(intent, {}).get(rid)
                        if not reason
                        else None,
                        "pool_rank": positions[rid],
                        "fused_score": fused_scores.get(rid),
                        "protected_reasons": protection.get(rid, []),
                        "score_status": "failed"
                        if reason
                        else "scored"
                        if self.policy.mode == "semantic"
                        and self.policy.enable_reranker
                        and not ordinary_exploration
                        else "not_selected",
                        "authority": "retrieval_only",
                        "score_semantics": "raw_relevance_not_probability",
                    }
                )
        epoch = RankingEpoch(
            epoch_id=epoch_id,
            epoch_seq=epoch_seq,
            plan_id=plan.plan_id,
            subject_ref=plan.subject.model_dump(mode="json"),
            query_dependency_hash=queries[0].query_dependency_hash,
            ranking_dependency_hash=evidence_hash(identity),
            pool_hash=pool_hash,
            record_ids=base_pool,
            ordered_record_ids=ordered,
            queries=[query.model_dump(mode="json") for query in queries],
            retrieval_views=[views[rid].model_dump(mode="json") for rid in base_pool],
            observations=observations,
            model_identity=self.model_identity,
            policy_hash=policy_hash,
            permission_scope_hash=scope_hash,
            actual_ranking_mode="deterministic"
            if reason or ordinary_exploration
            else self.policy.mode,
            degraded=reason is not None,
            reason=reason or ("no_rerankable_records" if ordinary_exploration else None),
            status="paused"
            if reason and self.policy.failure_policy == "pause"
            else "degraded"
            if reason
            else "ready",
            costs=costs,
        )
        self._pending[plan.plan_id] = epoch
        if callable(set_deadline):
            set_deadline(None)
        return epoch


def apply_epoch(plan: RetrievalPlan, epoch: RankingEpoch) -> RetrievalPlan:
    if (
        epoch.status != "committed"
        or epoch.plan_id != plan.plan_id
        or epoch.subject_ref != (plan.subject.model_dump(mode="json"))
    ):
        raise ValueError("only an exact committed epoch may update a plan")
    if not set(epoch.record_ids).issubset(plan.frozen_record_ids):
        raise ValueError("ranking epoch contains records outside the frozen universe")
    result = plan.model_copy(deep=True)
    ranks = {rid: rank for rank, rid in enumerate(epoch.ordered_record_ids, 1)}
    for record in result.records:
        if record.record_id in ranks:
            record.ranking_epoch_id = epoch.epoch_id
            record.ranking_epoch_seq = epoch.epoch_seq
            record.pool_rank = ranks[record.record_id]
            record.ranking_mode = epoch.actual_ranking_mode
    result.ranking_epoch_ids = list(dict.fromkeys([*result.ranking_epoch_ids, epoch.epoch_id]))
    # Phase and section membership/order remain a scheduler concern.
    return RetrievalPlan.model_validate(result.model_dump(mode="json"))
