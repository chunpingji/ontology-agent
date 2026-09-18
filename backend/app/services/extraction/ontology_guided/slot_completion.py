"""Proof-bound stopping receipts; stopping ordinary search is not full coverage."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.projection import effective_proof_gate

LAYERED_RECOGNITION_VERSION = "layered-properties-first-v1"
DEPENDENCY_READY_VERSION = "dependency-ready-v1"
SLOT_COMPLETION_VERSION = "single-value-conflict-survey-v1"


def _text(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def single_value_candidate(predicate, subject, properties, dependencies):
    """Return one usable, unconditional value, never infer a finite upper bound."""
    if (predicate.kind != "property" or predicate.max_count != 1
            or predicate.constraint_status != "resolved"
            or predicate.multiplicity == "multiple"):
        return None
    eligible = []
    for item in properties:
        if (item.subject_ref.id, item.subject_ref.revision, item.predicate_iri) != (
            subject.entity_id, subject.revision, predicate.iri,
        ):
            continue
        if item.independent_review == "rejected" or item.decision_status == "unsupported":
            continue
        if item.decision_status != "supported":
            return None
        if item.conditions or item.applicability or item.polarity != "affirmed":
            return None
        if (item.decision_status == "supported" and item.evidence_refs
                and effective_proof_gate(item)
                and dependencies.is_valid(f"{item.candidate_id}@{item.revision}")):
            eligible.append(item)
    # Different source-backed spellings may need normalization/comparison; this
    # gate does not invent equivalence between raw values or competing owners.
    if not eligible or len({item.raw_value for item in eligible}) != 1:
        return None
    return min(eligible, key=lambda item: (item.candidate_id, item.revision))


class SlotCompletionIndex:
    def __init__(self, index):
        self.index = index
        self.receipts = {}
        self._lexical_scopes = {}

    @staticmethod
    def key(slot):
        return evidence_hash(list(slot))

    def get(self, slot):
        return self.receipts.get(self.key(slot))

    def reopen(self, slot):
        """Remove only this slot's stop gate; reviewer tasks retain their own identity."""
        return self.receipts.pop(self.key(slot), None)

    def conflict_records(self, predicate):
        """Whole-document exact lexical survey, independent of ranking/pruning.

        Source text and structural headers are search clues, not fact proof.
        Every matched record must still execute the normal independent verifier.
        """
        key = evidence_hash(predicate.model_dump(mode="json"))
        if key not in self._lexical_scopes:
            terms = {_text(predicate.label), _text(re.split(r"[/#:]", predicate.iri)[-1])}
            terms.discard("")
            self._lexical_scopes[key] = tuple(
                record.record_id for record in self.index.records
                if any(term in _text(self.index.source_text(record.record_id, include_context=True))
                       for term in terms)
            )
        return self._lexical_scopes[key]

    def close(self, slot, *, plan, candidate, checked_record_ids, after_outcomes):
        checked = sorted(set(checked_record_ids), key=self.index.record_positions.__getitem__)
        if any(plan.ledger.get(rid) is None or plan.ledger[rid].coverage_state != "examined"
               for rid in checked):
            raise ValueError("slot completion cannot hide an unchecked conflict candidate")
        payload = {
            "version": SLOT_COMPLETION_VERSION,
            "slot": list(slot),
            "plan_id": plan.plan_id,
            "candidate_ref": {"id": candidate.candidate_id, "revision": candidate.revision},
            "proof_ref": candidate.proof_ref.model_dump(mode="json"),
            "source_scope_ref": deepcopy(plan.search_scope_ref),
            "checked_record_ids": checked,
            "checked_scope_hash": evidence_hash(checked),
            "unexamined_source_count": len(self.index.records) - len(checked),
            "after_outcomes": after_outcomes,
            "stop_reason": "single_value_satisfied",
            "coverage_claim": "policy_scope_only",
        }
        payload["completion_id"] = stable_id("slot-completion", payload)
        self.receipts[self.key(slot)] = payload
        return payload

    def snapshot(self):
        return {"version": SLOT_COMPLETION_VERSION, "receipts": deepcopy(self.receipts)}
