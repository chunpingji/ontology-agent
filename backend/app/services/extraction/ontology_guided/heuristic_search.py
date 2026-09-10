"""Experimental, source-bound admission before expensive semantic retrieval.

This module selects existing records, never graph facts. The executor retains
the complete RetrievalPlan/RecallLedger and the existing model/proof gates.
Snapshots are audit artifacts; experimental run restoration is not supported.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    MetadataSnapshot,
    RetrievalPlan,
    SlotSpec,
)
from app.services.extraction.ontology_guided.records import RecordIndex

SEARCH_POLICY_VERSION = "heuristic-first-v1"
QUERY_RULES_VERSION = "ontology-labels-and-registered-aliases-v1"

# Retrieval vocabulary only: no document names, expected entities or values.
_ALIASES = (
    ("分子量", "molecular weight", "molecularWeight"),
    ("分子式", "molecular formula", "molecularFormula"),
    ("项目名称", "产品名称", "project name", "projectName"),
    ("合成路线", "合成工艺", "制备工艺", "工艺描述", "synthesis route"),
    ("清洗方法", "清洁方法", "清洗流程", "清洁规程", "cleaning procedure"),
    ("设备", "设备清单", "equipment"),
    ("物料", "物料清单", "material"),
)
_GENERIC_TERMS = {"名称", "描述", "信息", "内容", "name", "description", "describes"}


def _normalize(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum()
    )


def _local_name(iri: str) -> str:
    return re.split(r"[/#:]", iri)[-1]


def _identity_field(label: str) -> bool:
    return bool(re.search(r"名称|编号|代码|name|code|identifier", label, re.IGNORECASE))


@dataclass(frozen=True)
class HeuristicSearchPolicy:
    version: str = SEARCH_POLICY_VERSION
    query_rules_version: str = QUERY_RULES_VERSION
    initial_page_size: int = 8
    expanded_page_size: int = 16
    exploration_page_size: int = 32
    max_exploration_pages: int = 1
    semantic_page_size: int = 8
    primary_section_limit: int = 3
    max_cheap_tasks_before_semantic: int = 8

    def __post_init__(self):
        if self.version != SEARCH_POLICY_VERSION or self.query_rules_version != QUERY_RULES_VERSION:
            raise ValueError("unsupported heuristic search policy version")
        for name in (
            "initial_page_size", "expanded_page_size", "exploration_page_size",
            "max_exploration_pages", "semantic_page_size", "primary_section_limit",
            "max_cheap_tasks_before_semantic",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")

    def snapshot(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AdmissionPage:
    batch_id: str
    plan_id: str
    stage: str
    source: str
    record_ids: list[str]
    reason: str
    context_record_ids: dict[str, list[str]]
    matched_terms: dict[str, list[str]]
    epoch_id: str | None = None
    authority: str = "retrieval_only"

    def snapshot(self) -> dict:
        return asdict(self)


class HeuristicSearchIndex:
    """Normalize source/structure once; summaries never become binding evidence."""

    def __init__(self, index: RecordIndex, metadata: MetadataSnapshot):
        if (
            metadata.analysis_id != index.ir.analysis_id
            or metadata.document_hash != index.ir.document_hash
        ):
            raise ValueError("heuristic index metadata belongs to another source")
        self.index = index
        self.metadata = metadata
        self.record_ids = tuple(record.record_id for record in index.records)
        self.source = {}
        self.context = {}
        self.structure = {}
        self.summary = {}
        self.sections: dict[str, list[str]] = defaultdict(list)
        self.tables: dict[tuple, list[str]] = defaultdict(list)
        nodes = {node.node_id: node for node in metadata.node_summaries}
        for record in index.records:
            rid = record.record_id
            node = nodes.get(record.section_node_id)
            self.source[rid] = _normalize(record.text)
            self.context[rid] = _normalize(index.source_text(rid, include_context=True))
            self.structure[rid] = _normalize(" / ".join(node.path) if node else "")
            self.summary[rid] = _normalize(node.summary or "") if node else ""
            self.sections[record.section_node_id].append(rid)
            if record.table_path:
                self.tables[record.table_path].append(rid)

    def context_record_ids(self, record_id: str) -> list[str]:
        """Report complete structural neighbors without granting their coverage."""
        record = self.index.by_id[record_id]
        values = {record_id}
        for group in self.index.field_groups_by_record.get(record_id, ()):
            values.update(group.record_ids)
        if record.table_path:
            values.update(self.tables[record.table_path])
        return sorted(values, key=self.index.record_positions.__getitem__)

    def candidates(
        self, predicate: SlotSpec | EdgeSpec, *, subject_mentions: list[str],
        policy: HeuristicSearchPolicy,
    ) -> tuple[list[str], list[str], dict[str, list[str]]]:
        direct: dict[str, float] = {}

        def add(value: str, weight: float):
            normalized = _normalize(value)
            if len(normalized) > 1 and normalized not in _GENERIC_TERMS:
                direct[normalized] = max(direct.get(normalized, 0), weight)

        add(predicate.label, 12)
        add(_local_name(predicate.iri), 10)
        if isinstance(predicate, EdgeSpec):
            for target in predicate.range_classes:
                add(target.label, 5)
                for label in target.direct_field_labels:
                    add(label, 14 if _identity_field(label) else 3)
        expanded = dict(direct)
        for group in _ALIASES:
            normalized = [_normalize(value) for value in group]
            if any(value in direct for value in normalized):
                expanded.update((value, max(expanded.get(value, 0), 4)) for value in normalized)
        # Definition phrases help only H1. Do not turn every Chinese bigram or
        # common English word into a hit that effectively activates the universe.
        definitions = [predicate.description]
        if isinstance(predicate, EdgeSpec):
            definitions.extend(item.description for item in predicate.range_classes)
        for description in definitions:
            for phrase in re.findall(r"[\"“‘]([^\"”’]{2,24})[\"”’]", description):
                term = _normalize(phrase)
                if term and term not in _GENERIC_TERMS:
                    expanded.setdefault(term, 2)

        mentions = [_normalize(value) for value in subject_mentions if len(value.strip()) >= 2]
        scores, expanded_scores, matches = {}, {}, {}
        section_scores: dict[str, float] = defaultdict(float)
        for rid in self.record_ids:
            source_hits = [term for term in direct if term in self.source[rid]]
            context_hits = [term for term in direct if term in self.context[rid]]
            structure_hits = [term for term in direct if term in self.structure[rid]]
            subject_hit = any(term in self.context[rid] for term in mentions)
            score = (
                sum(direct[term] for term in source_hits)
                + 0.25 * sum(direct[term] for term in context_hits if term not in source_hits)
                + 0.25 * sum(direct[term] for term in structure_hits)
            )
            # A repeated subject alone cannot make an unrelated field a target.
            if score and subject_hit:
                score += 8
            scores[rid] = score
            matches[rid] = list(dict.fromkeys([*source_hits, *context_hits, *structure_hits]))
            section = self.index.by_id[rid].section_node_id
            section_scores[section] = max(section_scores[section], score)
            wider_hits = [term for term in expanded if term in self.context[rid]]
            wider_structure = [term for term in expanded if term in self.structure[rid]]
            metadata_hits = [term for term in expanded if term in self.summary[rid]]
            expanded_scores[rid] = (
                score + sum(expanded[term] for term in wider_hits)
                + 0.5 * sum(expanded[term] for term in wider_structure)
                + 0.1 * len(metadata_hits)
            )
            matches[rid] = list(dict.fromkeys([*matches[rid], *wider_hits, *wider_structure]))
        section_order = sorted(
            section_scores,
            key=lambda sid: (
                -section_scores[sid], self.index.record_positions[self.sections[sid][0]],
            ),
        )
        primary = {sid for sid in section_order[:policy.primary_section_limit]
                   if section_scores[sid] > 0}
        h0 = sorted(
            (rid for rid in self.record_ids
             if scores[rid] > 0 and self.index.by_id[rid].section_node_id in primary),
            key=lambda rid: (-scores[rid], self.index.record_positions[rid]),
        )
        h1 = sorted(
            (rid for rid in self.record_ids if expanded_scores[rid] > 0 and rid not in h0),
            key=lambda rid: (-expanded_scores[rid], self.index.record_positions[rid]),
        )
        # If a list/table is relevant, its remaining rows are explicit H1 work.
        # Context expansion does not itself mean those rows have been examined.
        related = {other for rid in [*h0, *h1]
                   for other in self.context_record_ids(rid)} - set(h0) - set(h1)
        h1.extend(sorted(related, key=self.index.record_positions.__getitem__))
        return h0, h1, matches


@dataclass
class HeuristicSlotSearch:
    plan: RetrievalPlan
    predicate: SlotSpec | EdgeSpec
    search_index: HeuristicSearchIndex
    policy: HeuristicSearchPolicy = field(default_factory=HeuristicSearchPolicy)
    subject_mentions: list[str] = field(default_factory=list)
    run_fingerprint: str = ""

    def __post_init__(self):
        if self.plan.predicate_iri != self.predicate.iri:
            raise ValueError("heuristic predicate does not match its plan")
        if set(self.plan.ledger) != set(self.search_index.record_ids):
            raise ValueError("heuristic plan must retain the complete source universe")
        if self.plan.metadata_snapshot_id != self.search_index.metadata.snapshot_id:
            raise ValueError("heuristic plan uses another metadata snapshot")
        # The document root has no proved product name; filenames are inadmissible.
        if self.plan.subject.is_document_root:
            self.subject_mentions = []
        h0, h1, self._matches = self.search_index.candidates(
            self.predicate, subject_mentions=self.subject_mentions, policy=self.policy,
        )
        self._candidates = {"H0": h0, "H1": h1, "H2": [], "H3": []}
        self._cursors = dict.fromkeys(self._candidates, 0)
        self.stage = "H0"
        self.status = "needs_search"
        self.admitted: set[str] = set()
        self.observed: dict[str, dict] = {}
        self._attempts: dict[tuple[str, str | None], dict] = {}
        self._attempt_history: list[dict] = []
        self._active: list[str] = []
        self._pages: list[AdmissionPage] = []
        self._semantic_epoch_id: str | None = None
        self._semantic_received = False
        self._semantic_skip_reason: str | None = None
        self._exploration_pages = 0
        self._supported_count = 0
        self._reason = "initial_heuristic_search"
        self.events: list[dict] = []

    @property
    def needs_semantic(self) -> bool:
        return self.status == "needs_semantic"

    @property
    def deferred_record_ids(self) -> list[str]:
        return [rid for rid in self.search_index.record_ids if rid not in self.admitted]

    def _transition(self, stage: str, reason: str):
        self.events.append({"from": self.stage, "to": stage, "reason": reason})
        self.stage, self._reason = stage, reason

    def next_admission(self) -> AdmissionPage | None:
        if self._active and not all(rid in self.observed for rid in self._active):
            return None
        if self.status in {"technical_blocked", "local_results_only", "pass_exhausted",
                           "needs_semantic"}:
            return None
        self._active = []
        while True:
            candidates = self._candidates[self.stage]
            cursor = self._cursors[self.stage]
            remaining = [rid for rid in candidates[cursor:] if rid not in self.admitted]
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
                    batch_id=stable_id("heuristic-admission", [
                        self.run_fingerprint, self.plan.plan_id, self.policy.snapshot(),
                        self.stage, len(self._pages) + 1, selected, self._semantic_epoch_id,
                    ]),
                    plan_id=self.plan.plan_id, stage=self.stage,
                    source="semantic" if self.stage == "H2" else "heuristic",
                    record_ids=selected, reason=self._reason,
                    context_record_ids={rid: self.search_index.context_record_ids(rid)
                                        for rid in selected},
                    matched_terms={rid: self._matches.get(rid, []) for rid in selected},
                    epoch_id=self._semantic_epoch_id if self.stage == "H2" else None,
                )
                self._pages.append(page)
                self.status = "ready"
                return page
            if self.stage == "H0":
                self._transition("H1", "local_candidates_consumed_expand_lexical_and_structure")
                continue
            if self._supported_count:
                self.status = (
                    "local_results_only" if self.deferred_record_ids else "pass_exhausted"
                )
                return None
            if self.stage == "H1":
                if not self.deferred_record_ids:
                    self.status = "pass_exhausted"
                    return None
                self._transition("H2", "lexical_candidates_empty_or_without_valid_output")
                self.status = "needs_semantic"
                return None
            if self.stage == "H2":
                self._transition("H3", "semantic_candidates_empty_or_without_valid_output")
                # Explore both tail and other sections in bounded, stable pages.
                sections = [list(reversed([rid for rid in values if rid not in self.admitted]))
                            for values in reversed(list(self.search_index.sections.values()))]
                while any(sections):
                    for values in sections:
                        if values:
                            self._candidates["H3"].append(values.pop(0))
                continue
            self.status = "pass_exhausted"
            return None

    def observe(
        self, record_id: str, semantic_outcome: str, complete: bool, reason_code: str,
        supported_count: int = 0, attempt_id: str | None = None,
    ) -> None:
        if record_id not in self.admitted:
            raise ValueError("cannot observe a record without admission")
        if semantic_outcome not in {"supported", "unsupported", "undetermined", "not_checked"}:
            raise ValueError("invalid semantic outcome")
        if supported_count < 0 or (supported_count and semantic_outcome != "supported"):
            raise ValueError("supported output count is incompatible with the outcome")
        technical_failure = not complete or (
            semantic_outcome == "not_checked" and reason_code != "no_candidate_observed"
        )
        observation = dict(
            semantic_outcome=semantic_outcome, complete=complete, reason_code=reason_code,
            supported_count=supported_count, technical_failure=technical_failure,
        )
        attempt_key = (record_id, attempt_id)
        if attempt_key in self._attempts:
            if self._attempts[attempt_key] != observation:
                raise ValueError("experimental search cannot rewrite one attempt's feedback")
            return
        self._attempts[attempt_key] = observation
        self._attempt_history.append({
            "record_id": record_id, "attempt_id": attempt_id, **observation,
        })
        self.observed[record_id] = observation
        self._supported_count = sum(item["supported_count"] for item in self.observed.values())
        if any(item["technical_failure"] for item in self.observed.values()):
            self.status = "technical_blocked"
            self._reason = reason_code
        elif self.status == "technical_blocked":
            self.status = "needs_search"
            self._reason = "existing_core_retry_completed"

    def accept_semantic(
        self, ordered_record_ids: list[str], epoch_id: str, *, committed: bool,
    ) -> None:
        """Consume a complete committed epoch; this module never calls a model."""
        if not self.needs_semantic or self._semantic_received:
            raise ValueError("semantic ranking was not requested for this slot")
        if not committed or not epoch_id:
            raise ValueError("semantic admission requires a complete committed epoch")
        if len(ordered_record_ids) != len(set(ordered_record_ids)):
            raise ValueError("semantic epoch contains duplicate records")
        if not set(ordered_record_ids).issubset(self.plan.ledger):
            raise ValueError("semantic epoch contains records from another source")
        self._candidates["H2"] = [rid for rid in ordered_record_ids if rid not in self.admitted]
        self._semantic_epoch_id = epoch_id
        self._semantic_received = True
        self.status = "needs_search"
        self._reason = "committed_semantic_epoch"

    def skip_semantic(self, reason: str) -> None:
        """An explicitly disabled capability may proceed without a fake epoch.

        Required-model unavailability, timeouts and budget failures are not
        disabled configuration and must keep their existing pause/failure gate.
        """
        if reason not in {"semantic_disabled", "ranking_policy_deterministic"}:
            raise ValueError("only explicitly disabled semantic configuration may skip H2")
        if not self.needs_semantic or self._semantic_received:
            raise ValueError("semantic ranking was not requested for this slot")
        self._semantic_received = True
        self._semantic_skip_reason = reason
        self.status = "needs_search"
        self._reason = reason
        self.events.append({"from": "H2", "to": "H2", "reason": reason,
                            "semantic_skipped": True})

    def snapshot(self) -> dict:
        return {
            "policy": self.policy.snapshot(), "resume_supported": False,
            "plan_id": self.plan.plan_id, "run_fingerprint": self.run_fingerprint,
            "source_hash": self.search_index.index.ir.document_hash,
            "universe_hash": evidence_hash(list(self.search_index.record_ids)),
            "subject": self.plan.subject.model_dump(mode="json"),
            "predicate_iri": self.predicate.iri, "stage": self.stage,
            "status": self.status, "reason": self._reason,
            "universe_count": len(self.plan.ledger),
            "admitted_count": len(self.admitted),
            "examined_count": sum(not item["technical_failure"]
                                  for item in self.observed.values()),
            "deferred_count": len(self.deferred_record_ids),
            "supported_output_count": self._supported_count,
            "cursors": dict(self._cursors), "semantic_epoch_id": self._semantic_epoch_id,
            "semantic_skip_reason": self._semantic_skip_reason,
            "admitted_record_ids": [rid for rid in self.search_index.record_ids
                                    if rid in self.admitted],
            "deferred_record_ids": self.deferred_record_ids,
            "observed": dict(self.observed),
            "attempt_history": list(self._attempt_history),
            "admission_batches": [page.snapshot() for page in self._pages],
            "transitions": list(self.events),
        }
