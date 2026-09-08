"""Invalidate ownership decisions when a previously unseen competitor arrives.

This is conservative conflict detection, not a replacement ownership model. It
uses the assertion's historical accepted scope and the original task's competitor
set. A routing hint, a name match or a shared model number never excuses a late
competitor. Document metadata roots are not ordinary competing instances.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.instance_registry import _ancestors, canonicalize_candidates
from app.schemas.evidence import Candidate, CandidateRef, ExtractionTask, ValidationIssue
from app.services.extraction.evidence_scope import document_anchors, scope_contains

POLICY_VERSION = "evaluation-quality-late-competitors-v1"


@dataclass
class ReconciliationResult:
    candidates: dict[str, Candidate]
    changed_candidate_ids: list[str] = field(default_factory=list)
    invalidations: list[dict[str, Any]] = field(default_factory=list)
    reference_map: dict[tuple[str, int], CandidateRef] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    reference_audit: dict[str, Any] = field(default_factory=dict)


def invalidate_late_competitors(
    current: Mapping[str, Candidate],
    schema: Mapping[str, Any],
    ir,
    new_entity_ids: Iterable[str],
    task_registry: Mapping[str, ExtractionTask],
) -> ReconciliationResult:
    """Return a detached graph with conflicts and transitively revised references.

    Every positive candidate depending on a withdrawn subject, object, path root
    or dependency is also withdrawn *before* any reference is rewritten. Thus
    upgrading a reference cannot restore a rejected path. Exact previous refs
    map to the new revisions; already-stale references remain unresolved.
    """
    revised = {cid: candidate.model_copy(deep=True) for cid, candidate in current.items()}
    result = ReconciliationResult(candidates=revised)
    changed: set[str] = set()
    ancestry = {iri: _ancestors(iri, schema) for iri in schema}
    # Reuse all registry identity guards, including conflicting components and
    # ambiguous keys. This pass only computes equivalence; no output is adopted.
    identity_aliases = canonicalize_candidates(current, schema).alias_map

    def canonical_id(cid):
        return identity_aliases.get(cid, cid)

    def invalidate(candidate, code, **details):
        cid = candidate.candidate_id
        issue = ValidationIssue(code=code, stage=POLICY_VERSION)
        revised[cid] = candidate.model_copy(update={
            "validation_status": "conflict",
            "validation_issues": [*candidate.validation_issues, issue],
        })
        changed.add(cid)
        result.invalidations.append({
            "candidate_id": cid, "source_revision": candidate.revision,
            "source_task_id": candidate.task_id, "code": code, **details,
        })

    for entity_id in sorted(set(new_entity_ids)):
        entity = current.get(entity_id)
        if not (entity and entity.kind == "entity" and entity.positive_eligible
                and entity.review_status != "rejected"):
            continue
        for candidate in list(revised.values()):
            if (candidate.kind not in {"property", "relationship"}
                    or not candidate.positive_eligible or candidate.scope is None):
                continue
            owner = current.get(candidate.subject.candidate_id)
            task = task_registry.get(candidate.task_id)
            if (owner is None or owner.identity.get("document_root")
                    or owner.candidate_id == entity_id or task is None):
                continue
            if canonical_id(owner.candidate_id) == canonical_id(entity_id):
                continue
            if any(canonical_id(ref.candidate_id) == canonical_id(entity_id)
                   for ref in task.competing_subjects):
                continue
            compatible = (
                owner.class_iri in ancestry.get(entity.class_iri, set())
                or entity.class_iri in ancestry.get(owner.class_iri, set())
            )
            if not compatible:
                continue
            try:
                overlaps = any(scope_contains(candidate.scope, anchor, ir)
                               for anchor in document_anchors(entity))
            except (KeyError, ValueError):
                result.diagnostics.append("quality_late_competitor_scope_unresolvable")
                continue
            if overlaps:
                invalidate(candidate, "quality_late_competing_subject", competitor_id=entity_id)

    while True:
        invalid = [
            candidate for candidate in revised.values()
            if candidate.positive_eligible
            and any(ref is not None and ref.candidate_id in changed
                    for ref in [candidate.subject, candidate.object, candidate.path_root,
                                *candidate.dependency_refs])
        ]
        if not invalid:
            break
        for candidate in invalid:
            dependencies = sorted({ref.candidate_id for ref in [
                candidate.subject, candidate.object, candidate.path_root,
                *candidate.dependency_refs,
            ] if ref is not None and ref.candidate_id in changed})
            invalidate(candidate, "quality_invalidated_dependency", invalidated_ids=dependencies)

    if changed:
        references = canonicalize_candidates(
            revised, schema, revision_changed_ids=changed, merge_instances=False,
        )
        result.candidates = references.candidates
        result.reference_map = references.reference_map
        result.changed_candidate_ids = references.changed_candidate_ids
        result.reference_audit = references.to_dict()
        result.reference_audit.pop("candidates")
        result.reference_audit.pop("mentions")
        for record in result.invalidations:
            record["revision"] = result.candidates[record["candidate_id"]].revision
    result.diagnostics = sorted(set(result.diagnostics))
    return result
