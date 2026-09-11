"""Bounded source-clue work and positive supplementation, separate from coverage."""

from __future__ import annotations

import re
from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.scheduler import RecognitionTask


class EvidenceWorkQueue:
    version = "evidence-work-v2"

    def __init__(self, index):
        self.index = index
        self.items: dict[str, dict] = {}

    def source_refs(self, task):
        work = self.items.get(task.claim_lineage_id, {})
        return [self.index.ir.anchor(identity) for identity in work.get("binding_ids", [])]

    def observe(self, task, outcome, context, predicate, protocol):
        lineage = task.claim_lineage_id
        existing = self.items.get(lineage)
        if (
            existing
            and task.retry_kind
            and (existing.get("positive_rechecks", 0) or existing.get("reproposals", 0))
        ):
            existing["status"] = (
                "resolved"
                if outcome.semantic_outcome == "supported"
                else ("awaiting_evidence" if outcome.complete else "incomplete")
            )
            existing["latest_verification_status"] = (
                outcome.semantic_outcome if outcome.complete else "incomplete"
            )
            if not outcome.complete:
                existing["missing_facets"] = [outcome.reason_code]
            # Finishing one kind of repair does not consume the other kind's
            # independent limit. Both still share the original lineage budget.
        if not outcome.complete or outcome.semantic_outcome == "supported":
            return None
        issues = {issue for values in protocol.get("gate_issues", {}).values() for issue in values}
        proposals = (protocol.get("discovery") or {}).get("proposals", [])
        reviews = (protocol.get("verification") or {}).get("verifications", [])
        needs_reproposal = (
            "field_column_mismatch" in issues
            or predicate.kind == "property" and "datatype_mismatch" in issues
            or "entity_reference_not_specific" in issues
            or "partial_cleaning_method" in issues
            or "cleaning_method_scope_incomplete" in issues
            or "bridge_entailment_not_supported" in issues
            and any(r.get("bridge_verdict") == "unsupported" for r in reviews)
            or "applicability_not_supported" in issues
            and any(p.get("condition_support") or p.get("applicability") for p in proposals)
        )
        if (proposals and protocol.get("assertion_generation", 0) == 0 and needs_reproposal
                and not (existing or {}).get("reproposals")):
            work = {
                "work_id": lineage,
                "original_task": (existing or {}).get(
                    "original_task", task.model_dump(mode="json"),
                ),
                "slot": [task.subject.entity_id, task.predicate_iri],
                "missing_facets": sorted(issues),
                "binding_ids": (existing or {}).get("binding_ids", []),
                "status": "queued",
                "positive_rechecks": (existing or {}).get("positive_rechecks", 0),
                "reproposals": 1,
                "evidence_revision": protocol["evidence_revision"],
                "latest_verification_status": "incomplete",
                "retry_kind": "rediscovery:1",
            }
            if (
                sum(
                    w["slot"] == work["slot"] and w["status"] == "queued"
                    for w in self.items.values()
                )
                >= 4
            ):
                work["status"] = "deferred"
            self.items[lineage] = work
            return self._task(work) if work["status"] == "queued" else None
        if any(
            "menu" in issue or "forbidden" in issue or "field_column" in issue for issue in issues
        ):
            return None
        record = self.index.by_id[task.record_id]
        clues = [p.get("object_quote") or p.get("value_quote") for p in proposals]
        if not clues and not self.index.field_groups_by_record.get(task.record_id):
            return None
        if existing and existing.get("positive_rechecks", 0) >= 1:
            return None
        slot = [task.subject.entity_id, task.predicate_iri]
        full = sum(w["slot"] == slot and w["status"] == "queued" for w in self.items.values()) >= 4
        # These terms originate in the task's source and frozen ontology only.
        terms = [c["text"] for c in clues if c]
        terms.extend(re.split(r"[：:=＝]", record.text, maxsplit=1)[:1])
        facets = issues | set(protocol.get("missing_facets", []))
        for review in protocol.get("verification_facets", {}).values():
            facets.update(review.get("missing_facets", []))
        if any(
            "owner" in facet or "coreference" in facet or "subject" in facet for facet in facets
        ):
            terms.append(context.subject_label)
        if not facets or any("type" in facet for facet in facets):
            terms.extend(c.label for c in getattr(predicate, "range_classes", []))
        if not facets or any(
            "predicate" in facet or "bridge" in facet or "role" in facet for facet in facets
        ):
            terms.append(predicate.label)
        terms = {t.strip().casefold() for t in terms if 2 <= len(t.strip()) <= 80}
        seen = {f.anchor.evidence_id for f in context.fragments}
        ranked = []
        for other in self.index.records:
            if other.record_id == task.record_id:
                continue
            units = [u for u in other.source_units if u.evidence_id not in seen]
            if not units:
                continue
            text = other.text.casefold()
            score = sum(len(t) for t in terms if t in text)
            if score:
                ranked.append((-score, self.index.record_positions[other.record_id], other))
        selected = [entry[2] for entry in sorted(ranked, key=lambda x: x[:2])[:4]]
        binding_ids = list(
            dict.fromkeys(
                [
                    *(existing or {}).get("binding_ids", []),
                    *(u.evidence_id for r in selected for u in r.source_units if u.text),
                ]
            )
        )
        work = {
            "work_id": lineage,
            "original_task": (existing or {}).get("original_task", task.model_dump(mode="json")),
            "slot": slot,
            "missing_facets": sorted(facets) if selected else sorted(facets | {"no_new_evidence"}),
            "source_clues": clues,
            "binding_ids": binding_ids,
            "status": ("deferred" if full else "queued") if selected else "awaiting_evidence",
            "positive_rechecks": int(bool(selected) and not full),
            "reproposals": (existing or {}).get("reproposals", 0),
            "evidence_revision": int((existing or {}).get("evidence_revision", 0)) + bool(selected),
            "latest_verification_status": "incomplete" if selected else outcome.semantic_outcome,
        }
        self.items[lineage] = work
        if not selected or full:
            return None
        return self._task(work)

    def _task(self, work):
        task = RecognitionTask.model_validate(work["original_task"])
        return RecognitionTask.create(
            subject=task.subject,
            predicate_iri=task.predicate_iri,
            predicate_kind=task.predicate_kind,
            record_id=task.record_id,
            phase=task.phase,
            hop=task.hop,
            claim_lineage_id=task.claim_lineage_id,
            dependency_hash=evidence_hash([task.dependency_hash, work["binding_ids"]]),
            retry_kind=work.get("retry_kind", f"positive_evidence:{work['evidence_revision']}"),
            section_node_id=task.section_node_id,
            source_position=task.source_position,
        )

    def next_deferred(self, subject_is_active):
        # Dict insertion order is the durable arrival cursor. The scheduler's
        # retry/fresh alternation gives another ready slot an intervening turn.
        for work in self.items.values():
            if work["status"] != "deferred":
                continue
            task = RecognitionTask.model_validate(work["original_task"])
            if not subject_is_active(task.subject):
                work["missing_facets"] = ["parent_not_effective"]
                continue
            active = sum(
                w["slot"] == work["slot"] and w["status"] == "queued" for w in self.items.values()
            )
            if active < 4:
                work["status"] = "queued"
                if not work.get("retry_kind", "").startswith("rediscovery:"):
                    work["positive_rechecks"] = 1
                return self._task(work)
        return None

    def snapshot(self):
        return {"version": self.version, "items": deepcopy(self.items)}

    def summary(self):
        counts, reasons = {}, {}
        for work in self.items.values():
            status = work["status"]
            counts[status] = counts.get(status, 0) + 1
            public_reasons = {
                "field_binding_missing",
                "owner_identity_unproven",
                "owner_original_source_missing",
                "owner_field_source_missing",
                "entity_reference_not_specific",
                "partial_cleaning_method",
                "cleaning_method_scope_incomplete",
                "field_column_mismatch",
                "field_role_not_supported",
                "field_role_source_missing",
                "local_coreference_not_supported",
                "type_not_supported",
                "type_ambiguous",
                "bridge_kind_not_in_task_menu",
                "bridge_entailment_not_supported",
                "predicate_entailment_not_supported",
                "applicability_not_supported",
                "no_new_evidence",
                "parent_not_effective",
                "record_model_call_budget_exhausted",
                "context_budget_exceeded",
                "constraint_unresolved",
                "datatype_mismatch",
            }
            for reason in set(work.get("missing_facets", [])):
                reason = reason if reason in public_reasons else "evidence_unresolved"
                reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "enabled": True,
            "total": len(self.items),
            "status_counts": counts,
            "reason_counts": reasons,
            "rechecks": sum(w.get("positive_rechecks", 0) for w in self.items.values()),
        }

    def restore(self, state):
        if state.get("version") != self.version:
            raise ValueError("unsupported evidence work version")
        for lineage, work in state.get("items", {}).items():
            task = RecognitionTask.model_validate(work["original_task"])
            if task.claim_lineage_id != lineage or task.record_id not in self.index.by_id:
                raise ValueError("evidence work target mismatch")
            for identity in work["binding_ids"]:
                self.index.ir.unit(identity)
        self.items = deepcopy(state["items"])
