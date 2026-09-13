"""Versioned retrieval decisions. Scores route work; they never establish facts."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.evidence import EvidenceModel
from app.schemas.retrieval_diagnostics import RetrievalDiagnostics  # noqa: F401
from app.services.extraction.evidence_identity import evidence_hash, stable_id

ADAPTIVE_VERSION = "adaptive-retrieval-v1"
CONTEXT_VIEW_VERSION = "contextual-retrieval-view-v1"
INTENTS = {"discover", "counterevidence"}


class CalibrationProfile(EvidenceModel):
    """An explicit model/view-specific threshold artifact, never a default cutoff."""

    schema_version: Literal[1] = 1
    model_hash: str
    view_version: str
    view_configuration_hash: str
    query_version: Literal["subject-slot-query-v1"] = "subject-slot-query-v1"
    predicate_iris: list[str]
    dense_thresholds: dict[str, float]
    self_thresholds: dict[str, float]
    group_thresholds: dict[str, float]
    rerank_thresholds: dict[str, float]
    sample_manifest_hash: str
    expert_review_hash: str
    quality_status: Literal["development", "validated"] = "development"
    profile_hash: str = ""

    @model_validator(mode="after")
    def coherent(self):
        if not self.predicate_iris or len(set(self.predicate_iris)) != len(self.predicate_iris):
            raise ValueError("calibration requires explicit unique predicates")
        for scores in (
            self.dense_thresholds,
            self.self_thresholds,
            self.group_thresholds,
            self.rerank_thresholds,
        ):
            if set(scores) != INTENTS or not all(math.isfinite(v) for v in scores.values()):
                raise ValueError("calibration requires finite thresholds for both intents")
        if any(
            len(value) != 64
            for value in (
                self.model_hash,
                self.sample_manifest_hash,
                self.expert_review_hash,
                self.view_configuration_hash,
            )
        ):
            raise ValueError("calibration requires model, sample and expert artifact hashes")
        expected = evidence_hash(self.model_dump(mode="json", exclude={"profile_hash"}))
        if self.profile_hash and self.profile_hash != expected:
            raise ValueError("calibration profile hash mismatch")
        object.__setattr__(self, "profile_hash", expected)
        return self


class AdaptivePolicy(EvidenceModel):
    version: Literal["adaptive-retrieval-v1"] = ADAPTIVE_VERSION
    mode: Literal["observation", "enhanced", "trial", "enforce"] = "observation"
    view_version: Literal["complete-record-view-v1", "contextual-retrieval-view-v1"] = (
        CONTEXT_VIEW_VERSION
    )
    sibling_limit: int = Field(default=4, ge=0, le=16)
    ancestor_limit: int = Field(default=2, ge=0, le=8)
    group_member_limit: int = Field(default=8, ge=1, le=32)
    calibration: CalibrationProfile | None = None
    evaluation_only: bool = False

    @model_validator(mode="after")
    def calibrated_enforcement(self):
        if self.mode == "enhanced" and self.calibration is not None:
            raise ValueError("enhanced mode cannot carry pruning calibration; use enforce")
        if self.mode in {"trial", "enforce"}:
            if self.mode == "enforce" and self.calibration is None and self.evaluation_only:
                return self  # Enhanced-view measurement before thresholds exist; no pruning.
            if self.calibration is None or self.calibration.view_version != self.view_version:
                raise ValueError("enforcement requires calibration for the exact view")
            if self.calibration.view_configuration_hash != evidence_hash(
                {
                    key: getattr(self, key)
                    for key in (
                        "view_version",
                        "sibling_limit",
                        "ancestor_limit",
                        "group_member_limit",
                    )
                }
            ):
                raise ValueError("calibration view configuration changed")
            if self.mode == "trial":
                if self.calibration.quality_status != "development":
                    raise ValueError("trial pruning must remain explicitly unvalidated")
                if any(value > -8 for value in self.calibration.rerank_thresholds.values()):
                    raise ValueError("trial pruning requires conservative raw logits at most -8")
                if any(value > 0 for value in self.calibration.group_thresholds.values()):
                    raise ValueError("trial pruning must retain positive group evidence")
            elif self.calibration.quality_status != "validated" and not self.evaluation_only:
                raise ValueError("development calibration is restricted to isolated evaluation")
        return self

    @property
    def active_search(self):
        return self.mode in {"enhanced", "trial", "enforce"}

    def thresholds(self, *, model_identity, predicate_iri, stage):
        profile = self.calibration
        if profile is None or predicate_iri not in profile.predicate_iris:
            return None
        if profile.model_hash != evidence_hash(model_identity):
            raise ValueError("adaptive calibration model mismatch")
        if self.mode == "trial":
            if model_identity.get("score_semantics") != "raw_single_logit":
                raise ValueError("trial pruning requires raw single logits")
            if stage in {"dense", "self"}:
                return None  # Uncalibrated cosine scores never remove candidates at the front gate.
        fields = {
            "dense": "dense_thresholds",
            "self": "self_thresholds",
            "group": "group_thresholds",
            "rerank": "rerank_thresholds",
        }
        if stage not in fields:
            raise ValueError("unknown calibration stage")
        return getattr(profile, fields[stage])


class GateEvaluation(EvidenceModel):
    schema_version: Literal[1] = 1
    evaluation_id: str = ""
    evaluation_hash: str = ""
    plan_id: str
    subject_ref: dict
    query_dependency_hash: str
    permission_scope_hash: str
    policy_hash: str
    expansion_attempt_id: str
    stage: Literal["lexical", "dense", "availability"]
    record_ids: list[str]
    observations: list[dict]
    input_manifest_hash: str = ""
    group_observations: dict[str, dict] = Field(default_factory=dict)
    passed_record_ids: list[str]
    pruned_record_ids: list[str] = Field(default_factory=list)
    unavailable_record_ids: list[str] = Field(default_factory=list)
    request_refs: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(default=0, ge=0)
    status: Literal["complete"] = "complete"

    @model_validator(mode="after")
    def exact_partition(self):
        members = [*self.passed_record_ids, *self.pruned_record_ids, *self.unavailable_record_ids]
        if len(members) != len(set(members)) or set(members) != set(self.record_ids):
            raise ValueError("gate disposition must partition its exact record scope")
        if len(self.record_ids) != len(set(self.record_ids)):
            raise ValueError("duplicate gate records")
        if len(self.observations) != len(self.record_ids) or {
            item["record_id"] for item in self.observations
        } != set(self.record_ids):
            raise ValueError("gate observations must cover evaluated records")
        payload = self.model_dump(mode="json", exclude={"evaluation_id", "evaluation_hash"})
        identity = stable_id("gate-evaluation", payload)
        digest = evidence_hash(payload)
        if (self.evaluation_id and self.evaluation_id != identity) or (
            self.evaluation_hash and self.evaluation_hash != digest
        ):
            raise ValueError("gate evaluation hash mismatch")
        object.__setattr__(self, "evaluation_id", identity)
        object.__setattr__(self, "evaluation_hash", digest)
        return self


class AdmissionDecision(EvidenceModel):
    schema_version: Literal[1] = 1
    decision_id: str = ""
    decision_hash: str = ""
    plan_id: str
    subject_ref: dict
    dependency_hash: str
    permission_scope_hash: str
    policy_hash: str
    evaluation_ref: dict
    decision_target: Literal["next_stage", "recognition", "disposition_only"]
    admission_source: Literal["H0", "H1", "H2", "H3"] = "H2"
    admitted_record_ids: list[str] = Field(default_factory=list)
    next_stage_record_ids: list[str] = Field(default_factory=list)
    disposition_records: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_identity(self):
        if self.decision_target != "recognition" and self.admitted_record_ids:
            raise ValueError("a retrieval gate cannot grant recognition admission")
        if self.decision_target != "next_stage" and self.next_stage_record_ids:
            raise ValueError("unexpected next-stage records")
        ids = [
            *self.admitted_record_ids,
            *self.next_stage_record_ids,
            *(rid for group in self.disposition_records.values() for rid in group),
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("admission dispositions must be disjoint")
        payload = self.model_dump(mode="json", exclude={"decision_id", "decision_hash"})
        identity, digest = stable_id("admission-decision", payload), evidence_hash(payload)
        if (self.decision_id and self.decision_id != identity) or (
            self.decision_hash and self.decision_hash != digest
        ):
            raise ValueError("admission decision hash mismatch")
        object.__setattr__(self, "decision_id", identity)
        object.__setattr__(self, "decision_hash", digest)
        return self


class SearchPreparationResult(EvidenceModel):
    """Non-epoch results are consumed even when every candidate is filtered."""

    kind: Literal["filtered_empty", "no_new_candidates", "view_unavailable", "gate_completed"]
    plan_id: str
    expansion_attempt_id: str
    evaluation_refs: list[str] = Field(default_factory=list)
    reason: str


def protected_observation(observation):
    # Exploration seats are not proof protection. A real source/field hit is.
    return bool(
        "protected" in observation.get("protected_reasons", [])
        or observation.get("source_protected")
        or observation.get("context_complete") is False
        or observation.get("group_protected")
        or any(
            value > 0
            for key, value in observation.get("channel_raw_scores", {}).items()
            if key.startswith(("structure", "metadata", "self", "context", "group"))
        )
    )


def epoch_decision(epoch, policy: AdaptivePolicy) -> AdmissionDecision:
    if epoch.status != "committed":
        raise ValueError("admission requires a committed epoch")
    members = set(epoch.record_ids)
    queries = {query["retrieval_intent"]: query for query in epoch.queries}
    views = {view["record_id"]: view for view in epoch.retrieval_views}
    if (
        len(members) != len(epoch.record_ids)
        or len(epoch.ordered_record_ids) != len(members)
        or set(epoch.ordered_record_ids) != members
        or set(queries) != INTENTS
        or len(epoch.queries) != 2
        or set(views) != members
        or len(epoch.retrieval_views) != len(members)
    ):
        raise ValueError("adaptive admission requires the exact complete epoch pool")
    by_record = {rid: {} for rid in epoch.record_ids}
    for observation in epoch.observations:
        rid, intent = observation["record_id"], observation["retrieval_intent"]
        if (
            rid not in members
            or intent not in queries
            or intent in by_record[rid]
            or observation["ranking_epoch_id"] != epoch.epoch_id
            or observation["query_id"] != queries[intent]["query_id"]
            or observation["retrieval_view_hash"] != views[rid]["retrieval_view_hash"]
            or observation["model_input_hash"]
            != evidence_hash(
                [
                    queries[intent]["model_text"],
                    views[rid]["model_text"],
                ]
            )
        ):
            raise ValueError("adaptive observation input or identity mismatch")
        by_record[rid][intent] = observation
    if any(set(observations) != INTENTS for observations in by_record.values()):
        raise ValueError("adaptive admission requires complete dual-intent observations")
    predicate = epoch.queries[0]["predicate_iri"]
    thresholds = policy.thresholds(
        model_identity=epoch.model_identity,
        predicate_iri=predicate,
        stage="rerank",
    )
    pruned = []
    for rid in epoch.ordered_record_ids:
        observations = list(by_record[rid].values())
        scores = {o["retrieval_intent"]: o.get("raw_rerank_score") for o in observations}
        if (
            policy.mode in {"trial", "enforce"}
            and thresholds
            and not epoch.degraded
            and set(scores) == INTENTS
            and all(
                o.get("view_status") == "complete" and not protected_observation(o)
                for o in observations
            )
            and all(scores[i] is not None and scores[i] < thresholds[i] for i in INTENTS)
        ):
            pruned.append(rid)
    return AdmissionDecision(
        plan_id=epoch.plan_id,
        subject_ref=epoch.subject_ref,
        dependency_hash=epoch.query_dependency_hash,
        permission_scope_hash=epoch.permission_scope_hash,
        policy_hash=evidence_hash(policy),
        evaluation_ref={
            "kind": "epoch",
            "id": epoch.epoch_id,
            "hash": evidence_hash(epoch),
            "pool_hash": epoch.pool_hash,
        },
        decision_target="recognition",
        admitted_record_ids=[rid for rid in epoch.ordered_record_ids if rid not in set(pruned)],
        disposition_records={"soft_pruned": pruned} if pruned else {},
    )


def gate_decision(gate: GateEvaluation, policy: AdaptivePolicy) -> AdmissionDecision:
    return AdmissionDecision(
        plan_id=gate.plan_id,
        subject_ref=gate.subject_ref,
        dependency_hash=gate.query_dependency_hash,
        permission_scope_hash=gate.permission_scope_hash,
        policy_hash=evidence_hash(policy),
        evaluation_ref={"kind": "gate", "id": gate.evaluation_id, "hash": gate.evaluation_hash},
        decision_target="next_stage" if gate.passed_record_ids else "disposition_only",
        next_stage_record_ids=gate.passed_record_ids,
        disposition_records={
            key: value
            for key, value in {
                "soft_pruned": gate.pruned_record_ids,
                "view_unavailable": gate.unavailable_record_ids,
            }.items()
            if value
        },
    )
