"""v4 search disposition and liveness, isolated from historical snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.adaptive_retrieval import (
    AdaptivePolicy,
    AdmissionDecision,
    GateEvaluation,
    RetrievalDiagnostics,
    epoch_decision,
    gate_decision,
)
from app.services.extraction.ontology_guided.candidate_planning import is_sparse
from app.services.extraction.ontology_guided.contextual_retrieval import anchor_hits
from app.services.extraction.ontology_guided.heuristic_search import (
    AdmissionPage,
    HeuristicSlotSearch,
)
from app.services.extraction.ontology_guided.state_delta import FrozenDict, FrozenList, freeze_json


@dataclass
class AdaptiveSlotSearch(HeuristicSlotSearch):
    adaptive_policy: AdaptivePolicy = field(default_factory=AdaptivePolicy)
    permission_scope_hash: str = ""
    query_dependency_hash: str = ""

    def __post_init__(self):
        if self.policy.version != "heuristic-first-v4":
            raise ValueError("adaptive search requires heuristic-first-v4")
        super().__post_init__()
        self._dispositions = {}
        self._decisions = {}
        self._results = {}
        self._rounds = []
        self._hits = {}
        self._reactivations = []
        self._unavailable = set()
        self._stage_results = {}
        self._group_context = {}
        self._dependency_exhausted = False

    @property
    def enforcing(self):
        return self.adaptive_policy.active_search

    @property
    def semantic_boundary(self):
        rounds = len(self._rounds) if hasattr(self, "_rounds") else 0
        return {
            "version": "bounded-semantic-v1",
            "round": min(rounds, 3) + 1,
            "pool_limit": (8, 16, 32, 64)[min(rounds, 3)],
        }

    @property
    def expansion_attempt_id(self):
        return stable_id(
            "adaptive-expansion",
            [
                self.run_fingerprint,
                self.plan.plan_id,
                self.query_dependency_hash,
                len(self._rounds),
                evidence_hash(self.adaptive_policy),
            ],
        )

    def needs_evaluation_ids(self, stage="rerank"):
        if stage not in {"lexical", "dense", "rerank"}:
            raise ValueError("unknown retrieval stage")
        if self._dependency_exhausted:
            return []
        return [
            rid
            for rid in self.search_index.record_ids
            if rid not in self.admitted
            and rid not in self._unavailable
            and self._dispositions.get(rid, "unevaluated")
            in {"unevaluated", "reactivatable", "evaluated_pending_disposition"}
            and stage not in self._stage_results.get(rid, {})
        ]

    @property
    def pending_disposition_ids(self):
        return [
            rid
            for rid in self.search_index.record_ids
            if self._dispositions.get(rid) == "evaluated_pending_disposition"
        ]

    def exploration_eligible_ids(self):
        if self._dependency_exhausted:
            return []
        return [
            rid
            for rid in self.search_index.record_ids
            if rid not in self.admitted
            and self._dispositions.get(rid) not in {"soft_pruned", "dependency_exhausted"}
        ]

    def _audit_page(self, page):
        if page is None:
            return None
        for rid in page.record_ids:
            self._dispositions[rid] = "admitted"
            page.context_record_ids[rid] = list(
                dict.fromkeys(
                    [
                        *page.context_record_ids.get(rid, []),
                        *self._group_context.get(rid, []),
                    ]
                )
            )
        self._hits[page.batch_id] = freeze_json(
            {
                rid: anchor_hits(
                    self.search_index.index,
                    rid,
                    page.matched_terms.get(rid, []),
                    context_ids=page.context_record_ids.get(rid, []),
                )
                for rid in page.record_ids
            }
        )
        return page

    def next_admission(self):
        if not self.enforcing:
            return self._audit_page(super().next_admission())
        if self._active and not all(rid in self.observed for rid in self._active):
            return None
        if self.status in {
            "technical_blocked",
            "local_results_only",
            "pass_exhausted",
            "needs_semantic",
        }:
            return None
        self._active = []
        while True:
            candidates = self._candidates[self.stage]
            remaining = [
                rid
                for rid in candidates[self._cursors[self.stage] :]
                if rid not in self.admitted and self._dispositions.get(rid) != "soft_pruned"
            ]
            if remaining:
                if (
                    self.stage == "H3"
                    and self._exploration_pages >= self.policy.max_exploration_pages
                ):
                    self.status = "pass_exhausted"
                    return None
                size = {
                    "H0": self.policy.initial_page_size,
                    "H1": self.policy.expanded_page_size,
                    "H2": self.policy.semantic_page_size,
                    "H3": self.policy.exploration_page_size,
                }[self.stage]
                selected = remaining[:size]
                self._cursors[self.stage] = candidates.index(selected[-1]) + 1
                self.admitted.update(selected)
                self._active = selected
                if self.stage == "H3":
                    self._exploration_pages += 1
                page = AdmissionPage(
                    batch_id=stable_id(
                        "adaptive-admission",
                        [
                            self.run_fingerprint,
                            self.plan.plan_id,
                            self.adaptive_policy,
                            self.stage,
                            len(self._pages),
                            selected,
                            self._semantic_epoch_id,
                        ],
                    ),
                    plan_id=self.plan.plan_id,
                    stage=self.stage,
                    source="semantic" if self.stage == "H2" else "heuristic",
                    record_ids=selected,
                    reason=self._reason,
                    context_record_ids={
                        rid: self.search_index.context_record_ids(rid) for rid in selected
                    },
                    matched_terms={rid: self._matches.get(rid, []) for rid in selected},
                    epoch_id=self._semantic_epoch_id if self.stage == "H2" else None,
                )
                self._pages.append(page)
                self.status = "ready"
                return self._audit_page(page)
            if self.stage == "H0":
                self._transition("H1", "local_candidates_consumed")
                continue
            if self.stage in {"H1", "H2"}:
                if (
                    len(self._rounds) < 4
                    and self.needs_evaluation_ids()
                    and not self._semantic_skip_reason
                ):
                    self._transition("H2", "remaining_candidates_or_required_counterevidence")
                    self._semantic_received = False
                    self.status = "needs_semantic"
                    return None
                self._transition("H3", "no_new_semantic_work")
                eligible = set(self.exploration_eligible_ids())
                self._candidates["H3"] = [
                    rid
                    for values in reversed(list(self.search_index.sections.values()))
                    for rid in reversed(values)
                    if rid in eligible
                ]
                if is_sparse(self.plan):
                    self._candidates["H3"] = self._candidates["H3"][:
                        self.policy.exploration_page_size * self.policy.max_exploration_pages
                    ]
                continue
            self.status = "pass_exhausted"
            return None

    def accept_semantic(self, *args, **kwargs):
        raise ValueError("v4 requires the committed epoch and exact AdmissionDecision")

    def _validate_context(self, item):
        dependency = getattr(item, "dependency_hash", None) or item.query_dependency_hash
        if (
            item.plan_id != self.plan.plan_id
            or item.subject_ref != self.plan.subject.model_dump(mode="json")
            or item.permission_scope_hash != self.permission_scope_hash
            or dependency != self.query_dependency_hash
        ):
            raise ValueError("adaptive result belongs to another slot or dependency")

    def accept_ranked(self, epoch, decision):
        self._validate_context(epoch)
        decision = AdmissionDecision.model_validate(decision.model_dump(mode="json"))
        expected = epoch_decision(epoch, self.adaptive_policy)
        if decision != expected:
            raise ValueError("admission decision does not match the committed epoch")
        prior = self._decisions.get(decision.decision_id)
        if prior is not None:
            if prior != decision.model_dump(mode="json"):
                raise ValueError("admission decision content changed")
            return
        if (
            not self.needs_semantic
            or epoch.expansion_boundary != self.semantic_boundary
            or epoch.epoch_id in self._semantic_epochs
            or not set(epoch.record_ids) <= self.record_universe
            or set(epoch.record_ids) & self.admitted
        ):
            raise ValueError("invalid adaptive epoch scope or expansion")
        self._decisions[decision.decision_id] = freeze_json(decision.model_dump(mode="json"))
        for rid in epoch.record_ids:
            self._dispositions[rid] = "evaluated_pending_disposition"
            self._stage_results[rid] = freeze_json(
                {
                    **self._stage_results.get(rid, {}),
                    "rerank": epoch.epoch_id,
                }
            )
        for rid in decision.disposition_records.get("soft_pruned", []):
            self._dispositions[rid] = "soft_pruned"
        self._rounds.append(self.expansion_attempt_id)
        self._semantic_epochs.append(epoch.epoch_id)
        self._semantic_epoch_id = epoch.epoch_id
        self._candidates["H2"] = list(decision.admitted_record_ids)
        self._cursors["H2"] = 0
        self._semantic_received = True
        self.status = "needs_search"
        self._reason = "committed_admission_decision"

    def accept_result(self, result, gates):
        digest = evidence_hash(result)
        if result.expansion_attempt_id in self._results:
            if self._results[result.expansion_attempt_id] != digest:
                raise ValueError("adaptive preparation result changed")
            return
        if (
            result.plan_id != self.plan.plan_id
            or result.expansion_attempt_id != self.expansion_attempt_id
        ):
            raise ValueError("adaptive preparation identity mismatch")
        for ref in result.evaluation_refs:
            self.apply_gate(gates[ref])
        self._results[result.expansion_attempt_id] = digest
        if result.kind == "filtered_empty":
            self._rounds.append(result.expansion_attempt_id)
        elif result.kind == "no_new_candidates":
            # Already scored pools awaiting disposition must not be lost.
            if any(v == "evaluated_pending_disposition" for v in self._dispositions.values()):
                raise ValueError("ranking exhausted while committed admission remains pending")
            self._semantic_skip_reason = "semantic_scope_exhausted"
        self._semantic_received = True
        self.status = "needs_search"
        self._reason = result.reason

    def apply_gate(self, value):
        gate = GateEvaluation.model_validate(value)
        self._validate_context(gate)
        if gate.policy_hash != evidence_hash(self.adaptive_policy) or not set(
            gate.record_ids
        ) <= self.record_universe:
            raise ValueError("gate policy or record scope changed")
        decision = gate_decision(gate, self.adaptive_policy)
        if decision.decision_id in self._decisions:
            return
        if gate.expansion_attempt_id != self.expansion_attempt_id:
            raise ValueError("gate belongs to another expansion attempt")
        self._decisions[decision.decision_id] = freeze_json(decision.model_dump(mode="json"))
        for rid in gate.passed_record_ids:
            if rid not in self.admitted:
                self._dispositions[rid] = "evaluated_pending_disposition"
                self._stage_results[rid] = freeze_json(
                    {
                        **self._stage_results.get(rid, {}),
                        gate.stage: gate.evaluation_id,
                    }
                )
        for rid in gate.pruned_record_ids:
            entry = self.plan.ledger.get(rid)
            if rid in self.admitted or (
                entry is not None and entry.coverage_state != "unattempted"
            ):
                raise ValueError("cannot prune admitted or examined source")
            self._dispositions[rid] = "soft_pruned"
        self._unavailable.update(gate.unavailable_record_ids)
        for group_id, group in gate.group_observations.items():
            members = group["selected_record_ids"]
            if (len(members) > self.adaptive_policy.group_member_limit
                    or not set(members) <= self.record_universe):
                raise ValueError("group expansion exceeds the frozen record scope")
            if group.get("matched_intents"):
                for rid in members:
                    self._group_context[rid] = freeze_json(
                        list(
                            dict.fromkeys(
                                [
                                    *self._group_context.get(rid, []),
                                    *members,
                                ]
                            )
                        )[: self.adaptive_policy.group_member_limit]
                    )
                self.reactivate(members, reason="group_member", trigger_ref=group_id)

    def reactivate(self, record_ids, *, reason, trigger_ref):
        if (
            reason not in {"group_member", "required_evidence", "counterevidence"}
            or not trigger_ref
        ):
            raise ValueError("reactivation requires an explicit evidence trigger")
        if not set(record_ids) <= self.record_universe:
            raise ValueError("reactivation outside record scope")
        identity = stable_id("reactivation", [self.plan.plan_id, reason, trigger_ref, record_ids])
        if any(item.get("trigger_id") == identity for item in self._reactivations):
            return
        activated = [rid for rid in record_ids if self._dispositions.get(rid) == "soft_pruned"]
        for rid in activated[: self.adaptive_policy.group_member_limit]:
            self._dispositions[rid] = "reactivatable"
        self._reactivations.append(
            freeze_json(
                {
                    "trigger_id": identity,
                    "reason": reason,
                    "trigger_ref": trigger_ref,
                    "record_ids": activated[: self.adaptive_policy.group_member_limit],
                }
            )
        )

    def exhaust_dependency(self):
        # In sparse mode the default disposition belongs to the entire slot.
        # Never materialize one identical entry for every untouched source row.
        self._dependency_exhausted = is_sparse(self.plan)
        record_ids = list(self._dispositions) if self._dependency_exhausted else (
            self.search_index.record_ids
        )
        for rid in record_ids:
            if rid not in self.admitted and self._dispositions.get(rid) != "soft_pruned":
                self._dispositions[rid] = "dependency_exhausted"
        self.status = "pass_exhausted"
        self._reason = "subject_dependency_invalidated"

    def group_context_refs(self, record_id):
        return [
            anchor
            for rid in self._group_context.get(record_id, [])
            if rid != record_id
            for anchor in self.search_index.index.record_views_by_id[rid].source_refs
        ]

    def snapshot(self):
        state = super().snapshot()
        state["resume_state"].update(
            {
                "schema_version": 1,
                "adaptive_policy": self.adaptive_policy.model_dump(mode="json"),
                "permission_scope_hash": self.permission_scope_hash,
                "query_dependency_hash": self.query_dependency_hash,
                "dispositions": dict(self._dispositions),
                "decisions": FrozenDict(self._decisions),
                "preparation_results": dict(self._results),
                "expansion_attempts": list(self._rounds),
                "anchor_hits": FrozenDict(self._hits),
                "reactivations": FrozenList(self._reactivations),
                "unavailable_record_ids": sorted(self._unavailable),
                "stage_results": FrozenDict(self._stage_results),
                "group_context": FrozenDict(self._group_context),
            }
        )
        if is_sparse(self.plan):
            state["resume_state"]["dependency_exhausted"] = self._dependency_exhausted
        return state

    def restore(self, state):
        saved = state.get("resume_state", {})
        if is_sparse(self.plan):
            exhausted = saved.get("dependency_exhausted")
            if type(exhausted) is not bool or (
                exhausted and state.get("status") != "pass_exhausted"
            ):
                raise ValueError("invalid shared dependency disposition")
            self._dependency_exhausted = exhausted
        if (
            saved.get("schema_version") != 1
            or saved.get("adaptive_policy") != self.adaptive_policy.model_dump(mode="json")
            or saved.get("permission_scope_hash") != self.permission_scope_hash
            or saved.get("query_dependency_hash") != self.query_dependency_hash
        ):
            raise ValueError("adaptive search snapshot version or dependency mismatch")
        self._dispositions = dict(saved["dispositions"])
        if not set(self._dispositions) <= self.record_universe or not set(
            self._dispositions.values()
        ) <= {
            "unevaluated",
            "evaluated_pending_disposition",
            "soft_pruned",
            "reactivatable",
            "admitted",
            "dependency_exhausted",
        }:
            raise ValueError("invalid adaptive disposition")
        self._decisions = {
            key: freeze_json(AdmissionDecision.model_validate(value).model_dump(mode="json"))
            for key, value in saved["decisions"].items()
        }
        if any(key != value["decision_id"] for key, value in self._decisions.items()):
            raise ValueError("decision index mismatch")
        self._results = dict(saved["preparation_results"])
        self._rounds = list(saved["expansion_attempts"])
        if len(self._rounds) > 4 or len(self._rounds) != len(set(self._rounds)):
            raise ValueError("invalid expansion history")
        for decision in self._decisions.values():
            self._validate_context(AdmissionDecision.model_validate(decision))
        self._hits = {key: freeze_json(value) for key, value in saved["anchor_hits"].items()}
        self._reactivations = [freeze_json(value) for value in saved["reactivations"]]
        self._unavailable = set(saved["unavailable_record_ids"])
        self._stage_results = {
            key: freeze_json(value) for key, value in saved["stage_results"].items()
        }
        self._group_context = {
            key: freeze_json(value) for key, value in saved["group_context"].items()
        }
        if any(
            key not in self.record_universe
            or not set(value) <= self.record_universe
            or len(value) > self.adaptive_policy.group_member_limit
            for key, value in self._group_context.items()
        ):
            raise ValueError("invalid restored group expansion")
        if not (self._unavailable | set(self._stage_results)) <= self.record_universe:
            raise ValueError("adaptive stage result outside record universe")
        super().restore(state)
        if any(
            rid in self.admitted and value != "admitted"
            for rid, value in self._dispositions.items()
        ):
            raise ValueError("admission and disposition disagree")

    def continue_search(self):
        if is_sparse(self.plan):
            return
        if self.status not in {"local_results_only", "pass_exhausted"}:
            return
        for rid, value in list(self._dispositions.items()):
            if value == "soft_pruned":
                self._dispositions[rid] = "reactivatable"
                self._reactivations.append(
                    freeze_json(
                        {
                            "record_id": rid,
                            "reason": "explicit_continue",
                        }
                    )
                )
        super().continue_search()
        if self.enforcing:
            self.stage = "H3"
            self._candidates["H3"] = self.exploration_eligible_ids()
            self._cursors["H3"] = 0

    def diagnostics(self):
        counts = {
            name: sum(value == name for value in self._dispositions.values())
            for name in (
                "soft_pruned",
                "evaluated_pending_disposition",
                "reactivatable",
                "dependency_exhausted",
            )
        }
        if self._dependency_exhausted:
            counts["dependency_exhausted"] = (
                len(self.record_universe) - len(self.admitted) - counts["soft_pruned"]
            )
        return RetrievalDiagnostics(
            pruning_quality="unvalidated" if self.adaptive_policy.mode == "trial" else None,
            records_soft_pruned=counts["soft_pruned"],
            records_pending_disposition=counts["evaluated_pending_disposition"],
            records_reactivatable=counts["reactivatable"],
            records_dependency_exhausted=counts["dependency_exhausted"],
            reason_counts={"low_relevance": counts["soft_pruned"]},
            search_status=self.status,
        )
