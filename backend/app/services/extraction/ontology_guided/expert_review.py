"""Pure, versioned expert-review mutations and bounded source-local repair tasks.

Review text supplies feedback, never evidence authority. The executor records
these mutations between exact task prefixes so old outcomes can still replay.
"""

from __future__ import annotations

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import GraphProperty
from app.services.extraction.ontology_guided.scheduler import RecognitionTask

EXPERT_REVIEW_VERSION = "expert-review-repair-v1"
MAX_REPAIR_TASKS = 16
MAX_REPAIR_CALLS = 32


def assertion_identity(candidate: GraphProperty) -> str:
    """A new proof/ID cannot silently undo rejection of the same sourced assertion."""
    values = candidate.model_dump(include={
        "subject_ref", "predicate_iri", "raw_value", "normalized_value", "unit",
        "polarity", "conditions", "applicability", "value_evidence_refs",
    }, mode="json")
    values["value_evidence_refs"] = sorted(
        values["value_evidence_refs"]
        or [r.model_dump(mode="json") for r in candidate.evidence_refs],
        key=evidence_hash,
    )
    return evidence_hash(values)


def apply_property_review(review: dict, properties: dict, dependencies) -> GraphProperty:
    candidate = properties.get(review["candidate_id"])
    if candidate is None or candidate.revision != review["candidate_revision"]:
        raise ValueError("expert review candidate revision does not match replay boundary")
    if review["decision"] not in {"accepted", "rejected"}:
        raise ValueError("invalid expert review decision")
    if review["decision"] == "rejected" and not str(review.get("reason", "")).strip():
        raise ValueError("expert rejection requires a reason")
    updated = candidate.model_copy(update={
        "revision": candidate.revision + 1,
        "independent_review": review["decision"],
        **({"reason_code": "expert_rejected", "reason": review["reason"]}
           if review["decision"] == "rejected" else
           {"reason_code": "", "reason": ""} if candidate.reason_code == "expert_rejected" else {}),
    })
    previous_key = f"{candidate.candidate_id}@{candidate.revision}"
    updated_key = f"{updated.candidate_id}@{updated.revision}"
    # Preserve the original proof requirements. Confirmation is not a new proof.
    for proof in dependencies.requirements.get(previous_key, []):
        dependencies.add_proof(updated_key, sorted(proof))
    if review["decision"] == "rejected":
        dependencies.invalidate(previous_key)
        dependencies.invalidate(updated_key)
    properties[candidate.candidate_id] = updated
    return updated


def repair_feedback(review: dict, operation: dict) -> dict:
    return {
        "version": EXPERT_REVIEW_VERSION,
        "operation_id": operation["operation_id"],
        "review_id": review["review_id"],
        "review_revision": review["review_revision"],
        "candidate_id": review["candidate_id"],
        "candidate_revision": review["candidate_revision"],
        "reason_code": review.get("reason_code", "other"),
        "reason": review.get("reason", ""),
    }


def local_repair_tasks(review: dict, operation: dict, menu, index) -> tuple[list, str | None]:
    """Return only authorised record/property opportunities, with new paid lineages."""
    if review["decision"] != "rejected":
        raise ValueError("local repair requires an explicit rejection")
    if review.get("reason_code") == "incorrect_subject":
        return [], "expert_subject_localization_required"
    original = RecognitionTask.model_validate(review["original_task"])
    if (original.subject != menu.subject or original.predicate_iri != review["predicate_iri"]
            or original.predicate_kind != "property"):
        raise ValueError("expert repair source task does not match its local menu")
    target_predicates = [p for p in menu.properties if (
        p.iri != review["predicate_iri"] if review.get("reason_code") == "incorrect_property"
        else p.iri == review["predicate_iri"]
    )]
    allowed_records = set(review.get("record_ids") or [original.record_id])
    records = [record for record in index.records if record.record_id in allowed_records]
    if len(records) != len(allowed_records):
        raise ValueError("expert repair references an unavailable original record")
    tasks = []
    limit = min(MAX_REPAIR_TASKS, int(operation.get("max_tasks", MAX_REPAIR_TASKS)))
    feedback = repair_feedback(review, operation)
    for record in sorted(records, key=lambda r: r.record_id):
        for predicate in sorted(target_predicates, key=lambda p: p.iri):
            if len(tasks) >= limit:
                return tasks, "expert_repair_task_limit"
            tasks.append(RecognitionTask.create(
                subject=original.subject, predicate_iri=predicate.iri, predicate_kind="property",
                record_id=record.record_id, phase=original.phase, hop=original.hop,
                dependency_hash=evidence_hash([
                    EXPERT_REVIEW_VERSION, original.dependency_hash, menu.menu_id,
                    predicate.model_dump(mode="json"), record.record_id, feedback,
                ]),
                claim_lineage_id=stable_id("expert-repair-lineage", [
                    operation["operation_id"], predicate.iri, record.record_id,
                ]),
                retry_kind=f"expert_review:{operation['operation_id']}",
                section_node_id=original.section_node_id,
                source_position=original.source_position,
            ))
    truncated = review.get("source_record_count", len(records)) > len(records)
    return tasks, ("expert_repair_task_limit" if tasks and truncated else
                   None if tasks else "expert_repair_no_local_alternative")


class ReviewReplay:
    """Small event journal; new operations are admitted only after base validation."""

    def __init__(self, frozen: dict | None = None):
        if frozen and frozen.get("version") != EXPERT_REVIEW_VERSION:
            raise ValueError("unsupported expert review replay version")
        self.events = deepcopy((frozen or {}).get("events", []))
        self.applied: set[str] = set()
        self.results: dict[str, dict] = {}
        identities = [event["event_id"] for event in self.events]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate expert review event")
        if any(type(e.get("after_outcomes")) is not int or e["after_outcomes"] < 0
               for e in self.events):
            raise ValueError("invalid expert review replay position")

    def admit(self, reviews: list[dict], operations: list[dict], *, after_outcomes: int) -> None:
        identities = {event["event_id"] for event in self.events}
        for kind, values, key in (
            ("review", reviews, "review_id"), ("repair", operations, "operation_id"),
        ):
            for value in values:
                event_id = f"{kind}:{value[key]}"
                if event_id in identities:
                    continue
                if value.get("after_outcomes", 0) > after_outcomes:
                    raise ValueError("review boundary exceeds the restored checkpoint")
                self.events.append({
                    "event_id": event_id, "kind": kind, "after_outcomes": after_outcomes,
                    "payload": deepcopy(value),
                })
                identities.add(event_id)

        # A failed/cancelled execution ends its bounded repair. Publish this as
        # a new event only after validating the last immutable model boundary.
        for operation in operations:
            if operation.get("status") not in {"failed", "cancelled"}:
                continue
            event_id = f"repair-stop:{operation['operation_id']}"
            if event_id in identities:
                continue
            self.events.append({
                "event_id": event_id, "kind": "repair_stop", "after_outcomes": after_outcomes,
                "payload": deepcopy(operation),
            })
            identities.add(event_id)

    def at(self, count: int) -> list[dict]:
        return [event for event in self.events
                if event["event_id"] not in self.applied and event["after_outcomes"] == count]

    def snapshot(self) -> dict:
        return {"version": EXPERT_REVIEW_VERSION, "events": deepcopy(self.events)}
