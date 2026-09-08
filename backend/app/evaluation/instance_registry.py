"""Conservative, reference-safe instance canonicalization for offline experiments.

The input must be the complete current candidate graph, not just a task's delta.
Names never establish identity. A verified IRI or a schema-declared identity key
does, within that graph, unless the identity is ambiguous or contradictory.
Conflicting identities remain separate and are reported; canonicalization is not
a substitute for semantic validation. Original entity snapshots live in the
mention sidecar, including their original scopes, paths and verification results.
No source range is widened, no identity IRI or binding evidence is invented.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.schemas.evidence import Candidate, CandidateRef, ValidationIssue
from app.services.extraction.evidence_identity import canonical_json, stable_id

REGISTRY_VERSION = "verified-instance-registry-v1"
_ALTERNATIVE = re.compile(
    r"或|和|及|至|到|[、,，;；|/\n\r~～…]|\.{2}|[–—]|\b(?:or|and)\b|\s-\s",
    re.IGNORECASE,
)
_CODE_RANGE = re.compile(r"\d-[A-Za-z]+\d")
_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$")


@dataclass
class CanonicalizationResult:
    candidates: dict[str, Candidate]
    alias_map: dict[str, str] = field(default_factory=dict)
    reference_map: dict[tuple[str, int], CandidateRef] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    mentions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    scope_rebuild_ids: list[str] = field(default_factory=list)
    changed_candidate_ids: list[str] = field(default_factory=list)
    scope_alias_map: dict[str, str] = field(default_factory=dict)
    invalidated_scope_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe per-invocation audit; callers retain past invocation audits."""
        return {
            "registry_version": REGISTRY_VERSION,
            "candidates": [c.model_dump(mode="json") for c in self.candidates.values()],
            "alias_map": self.alias_map,
            "reference_map": [
                {"source": {"candidate_id": key[0], "revision": key[1]},
                 "canonical": value.model_dump(mode="json")}
                for key, value in sorted(self.reference_map.items())
            ],
            "aliases": self.aliases,
            "mentions": self.mentions,
            "conflicts": self.conflicts,
            "scope_rebuild_ids": self.scope_rebuild_ids,
            "changed_candidate_ids": self.changed_candidate_ids,
            "scope_alias_map": self.scope_alias_map,
            "invalidated_scope_ids": self.invalidated_scope_ids,
        }


def _ref(candidate: Candidate, revision: int | None = None) -> CandidateRef:
    return CandidateRef(
        candidate_id=candidate.candidate_id,
        revision=candidate.revision if revision is None else revision,
        class_iri=candidate.class_iri,
        instance_iri=candidate.identity.get("instance_iri"),
    )


def _ancestors(class_iri: str, schema: Mapping[str, Any]) -> set[str]:
    seen: set[str] = set()
    pending = [class_iri]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(p for p in schema.get(current, {}).get("parents", [])
                       if isinstance(p, str))
    return seen


def _unique(values: Iterable[Any]) -> list[Any]:
    result, seen = [], set()
    for value in values:
        key = canonical_json(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _references(candidate: Candidate):
    for name in ("subject", "object", "path_root"):
        value = getattr(candidate, name)
        if value:
            yield name, value
    for index, value in enumerate(candidate.dependency_refs):
        yield f"dependency_refs[{index}]", value
    if candidate.scope:
        yield "scope.subject", candidate.scope.subject


def canonicalize_candidates(
    candidates: Mapping[str, Candidate] | Iterable[Candidate],
    schema: Mapping[str, Any],
    *,
    preferred_ids: Iterable[str] = (),
    revision_changed_ids: Iterable[str] = (),
    merge_instances: bool = True,
) -> CanonicalizationResult:
    """Merge only verified compatible identities; never upgrade stale references.

    ``revision_changed_ids`` versions explicit caller changes (for example a
    reconciler's conflict decisions) while references still point at the supplied
    revisions. It must never be used to make an invalidated fact eligible again.
    ``merge_instances=False`` provides reference-only reconciliation, preserving
    every candidate ID while versioning those explicit decisions.
    Existing canonical IDs can be preferred for incremental use. Ties are sorted
    by candidate ID, independent of input order. Compatible types must form an
    ancestor chain; the most specific verified type is retained. Distinct
    explicit IRIs, conflicting values for the same key, differing ontology
    releases or applicable times block the *entire* identity component, so an
    underspecified third candidate cannot bridge contradictory identities.

    Changed candidates receive one new revision; downstream references propagate
    that revision transitively, including cycles. Missing, stale or contradictory
    reference metadata is preserved for audit and conflicts the owning candidate.
    Exact known references are rewritten, not independently revalidated.
    """
    values = list(candidates.values() if isinstance(candidates, Mapping) else candidates)
    if len({c.candidate_id for c in values}) != len(values):
        raise ValueError("candidate IDs must be unique; pass one current revision per ID")
    original = {c.candidate_id: c.model_copy(deep=True)
                for c in sorted(values, key=lambda c: c.candidate_id)}
    result = CanonicalizationResult(candidates={})
    preferred = set(preferred_ids)
    ancestors = {iri: _ancestors(iri, schema) for iri in schema}
    identities: dict[str, tuple[str | None, tuple[str, str] | None]] = {}

    def conflict(code: str, ids: Iterable[str], **details: Any):
        result.conflicts.append({"code": code, "candidate_ids": sorted(set(ids)), **details})

    for cid, candidate in original.items():
        if not merge_instances:
            continue
        verification = candidate.type_verification
        if not (candidate.kind == "entity" and candidate.positive_eligible
                and candidate.review_status != "rejected" and verification
                and verification.supported and verification.identity_supported):
            continue
        if candidate.class_iri not in schema:
            conflict("identity_unknown_class", [cid])
            continue
        identity = candidate.identity
        iri = identity.get("instance_iri") or None
        if iri and not _IRI.fullmatch(iri):
            conflict("invalid_instance_iri", [cid])
            continue
        key = None
        if "key_predicate" in identity or "key_value" in identity:
            predicate = identity.get("key_predicate", "")
            value = identity.get("key_value", "").strip()
            legal_keys = {
                p.get("iri") for cls in ancestors[candidate.class_iri]
                for p in schema.get(cls, {}).get("properties", [])
                if p.get("identity_key") is True
            }
            if predicate not in legal_keys or not value:
                conflict("invalid_identity_key", [cid], key_predicate=predicate)
                continue
            if _ALTERNATIVE.search(value) or _CODE_RANGE.search(value):
                conflict("ambiguous_identity_value", [cid], key_value=value)
                continue
            # No case folding, punctuation stripping or model-number heuristics.
            key = (predicate, value)
        if iri or key:
            identities[cid] = (iri, key)

    # First find complete identity components, then check all pairs. Greedy union
    # would let an IRI-less mention bridge two conflicting explicit identities.
    neighbours: dict[str, set[str]] = defaultdict(set)
    buckets: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for cid, (iri, key) in identities.items():
        if iri:
            buckets[("iri", iri)].append(cid)
        if key:
            buckets[("key", *key)].append(cid)
    for members in buckets.values():
        for cid in members:
            neighbours[cid].update(members)
    groups, seen = [], set()
    for cid in original:
        if cid in seen:
            continue
        pending, component = [cid], set()
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(neighbours[current] - component)
        seen.update(component)
        issues: set[str] = set()
        ordered = sorted(component)
        for offset, left_id in enumerate(ordered):
            for right_id in ordered[offset + 1:]:
                left, right = original[left_id], original[right_id]
                li, lk = identities[left_id]
                ri, rk = identities[right_id]
                if li and ri and li != ri:
                    issues.add("conflicting_instance_iris")
                if lk and rk and lk[0] == rk[0] and lk[1] != rk[1]:
                    issues.add("conflicting_identity_keys")
                if (left.class_iri not in ancestors.get(right.class_iri, set())
                        and right.class_iri not in ancestors.get(left.class_iri, set())):
                    issues.add("incompatible_identity_types")
                if left.ontology_release != right.ontology_release:
                    issues.add("identity_ontology_release_mismatch")
                if left.applicable_at != right.applicable_at:
                    issues.add("identity_applicability_mismatch")
        if issues:
            for code in sorted(issues):
                conflict(code, ordered)
            groups.extend([[item] for item in ordered])
        else:
            groups.append(ordered)

    merged_ids: set[str] = set()
    base: dict[str, Candidate] = {}
    invalid_scopes: set[str] = set()
    for group in groups:
        group.sort(key=lambda cid: (cid not in preferred, cid))
        canonical_id = group[0]
        members = [original[cid] for cid in group]
        chosen = members[0]
        if chosen.kind == "entity":
            result.mentions[canonical_id] = [c.model_dump(mode="json") for c in members]
            result.aliases[canonical_id] = list(dict.fromkeys(c.text for c in members))
        if len(members) > 1:
            merged_ids.add(canonical_id)
            result.alias_map.update({c.candidate_id: canonical_id for c in members[1:]})
            # All types form a chain, so one verified member has the deepest type.
            specific = max(members, key=lambda c: len(ancestors[c.class_iri]))
            identity = dict(chosen.identity)
            for member in members:
                for key, value in member.identity.items():
                    identity.setdefault(key, value)
                if member.scope:
                    invalid_scopes.add(member.scope.scope_id)
            chosen = chosen.model_copy(update={
                "class_iri": specific.class_iri,
                "identity": identity,
                "type_verification": specific.type_verification.model_copy(deep=True),
                "provenance": _unique(p for c in members for p in c.provenance),
                "dependency_refs": _unique(r for c in members for r in c.dependency_refs),
                "scope": None,
                "revision": max(c.revision for c in members),
            }, deep=True)
        base[canonical_id] = chosen

    def target(ref: CandidateRef) -> Candidate | None:
        source = original.get(ref.candidate_id)
        if source is None or source.revision != ref.revision:
            return None
        if ref.class_iri is not None and ref.class_iri != source.class_iri:
            return None
        if (ref.instance_iri is not None
                and ref.instance_iri != source.identity.get("instance_iri")):
            return None
        return base[result.alias_map.get(ref.candidate_id, ref.candidate_id)]

    invalid_refs: dict[str, list[str]] = defaultdict(list)
    for cid, candidate in base.items():
        for field_name, ref in _references(candidate):
            if target(ref) is None:
                code = "instance_registry_unresolved_reference"
                message = f"{field_name}:{ref.candidate_id}@{ref.revision}"
                invalid_refs[cid].append(message)
                conflict(code, [cid], field=field_name, reference=ref.model_dump(mode="json"))

    changed = set(merged_ids)
    for cid in revision_changed_ids:
        if cid not in original:
            raise ValueError("revision_changed_ids contains an unknown candidate ID")
        changed.add(result.alias_map.get(cid, cid))
    for cid, messages in invalid_refs.items():
        candidate = base[cid]
        issues = _unique([*candidate.validation_issues, *[
            ValidationIssue(code="instance_registry_unresolved_reference", message=message,
                            stage=REGISTRY_VERSION) for message in messages
        ]])
        if candidate.validation_status != "conflict" or issues != candidate.validation_issues:
            changed.add(cid)
        base[cid] = candidate.model_copy(update={"validation_status": "conflict",
                                                "validation_issues": issues})

    def rewrite(ref: CandidateRef) -> CandidateRef:
        resolved = target(ref)
        if resolved is None:
            return ref.model_copy(deep=True)
        revision = resolved.revision + (resolved.candidate_id in changed)
        # Preserve omitted advisory metadata to avoid revising an untouched graph.
        return ref.model_copy(update={
            "candidate_id": resolved.candidate_id, "revision": revision,
            "class_iri": resolved.class_iri if ref.class_iri is not None else None,
            "instance_iri": (resolved.identity.get("instance_iri")
                             if ref.instance_iri is not None else None),
        })

    # Monotone fixed point: increment at most once even for cyclic dependencies.
    while True:
        additions = {cid for cid, candidate in base.items() if cid not in changed
                     and any(rewrite(ref) != ref for _, ref in _references(candidate))}
        if not additions:
            break
        changed.update(additions)

    # A stale endpoint also invalidates its dependents, not just the immediate
    # owner. Existing unrelated rejected/pending candidates remain non-positive.
    invalidated = set(invalid_refs)
    while True:
        additions = {
            cid for cid, candidate in base.items() if cid not in invalidated
            and candidate.positive_eligible
            and any((resolved := target(ref)) is not None
                    and resolved.candidate_id in invalidated
                    for field_name, ref in _references(candidate)
                    if field_name != "scope.subject")
        }
        if not additions:
            break
        invalidated.update(additions)
    for cid in invalidated - invalid_refs.keys():
        candidate = base[cid]
        base[cid] = candidate.model_copy(update={
            "validation_status": "conflict",
            "validation_issues": _unique([*candidate.validation_issues, ValidationIssue(
                code="instance_registry_invalidated_dependency", stage=REGISTRY_VERSION,
            )]),
        })
        changed.add(cid)
        conflict("instance_registry_invalidated_dependency", [cid])
    # Scope parent links are part of the versioned graph as well. Propagate their
    # invalidation without unioning retrieval ranges or silently changing a scope
    # under an unchanged candidate revision.
    changed_scopes = set(invalid_scopes)
    while True:
        before = (len(changed), len(changed_scopes))
        additions = {cid for cid, candidate in base.items() if cid not in changed
                     and any(rewrite(ref) != ref for _, ref in _references(candidate))}
        changed.update(additions)
        for cid, candidate in base.items():
            scope = candidate.scope
            if scope and (rewrite(scope.subject) != scope.subject
                          or scope.parent_scope_id in changed_scopes):
                changed_scopes.add(scope.scope_id)
            if scope and scope.scope_id in changed_scopes:
                changed.add(cid)
        if before == (len(changed), len(changed_scopes)):
            break

    scope_rebuild = set(merged_ids)
    for cid, candidate in sorted(base.items()):
        data = candidate.model_dump(mode="python")
        data["revision"] = candidate.revision + (cid in changed)
        for name in ("subject", "object", "path_root"):
            value = getattr(candidate, name)
            if value:
                data[name] = rewrite(value).model_dump(mode="python")
        data["dependency_refs"] = [r.model_dump(mode="python") for r in
                                   _unique(rewrite(r) for r in candidate.dependency_refs)]
        for binding in data["bindings"]:
            binding["subject_candidate_id"] = data["subject"]["candidate_id"]
            binding["object_candidate_id"] = (data["object"]["candidate_id"]
                                               if data["object"] else None)
        if candidate.scope and candidate.scope.scope_id in changed_scopes:
            if candidate.kind == "entity":
                data["scope"] = None
                scope_rebuild.add(cid)
                invalid_scopes.add(candidate.scope.scope_id)
            else:
                scope = data["scope"]
                scope["subject"] = rewrite(candidate.scope.subject).model_dump(mode="python")
                scope["revision"] += 1
                scope["scope_id"] = stable_id(REGISTRY_VERSION, {
                    "old_scope_id": candidate.scope.scope_id,
                    "old_revision": candidate.scope.revision, "subject": scope["subject"],
                    "parent_changed": candidate.scope.parent_scope_id in changed_scopes,
                })
                result.scope_alias_map[candidate.scope.scope_id] = scope["scope_id"]
        result.candidates[cid] = Candidate.model_validate(data)
    for cid, candidate in result.candidates.items():
        scope = candidate.scope
        if not scope or not scope.parent_scope_id:
            continue
        parent_id = scope.parent_scope_id
        parent = result.scope_alias_map.get(parent_id, parent_id)
        if parent_id in invalid_scopes and parent == parent_id:
            parent = None
        if parent != parent_id:
            # The fixed point above already revised this scope and its owner.
            candidate.scope = scope.model_copy(update={"parent_scope_id": parent})
    for cid, candidate in original.items():
        canonical = result.candidates[result.alias_map.get(cid, cid)]
        result.reference_map[(cid, candidate.revision)] = _ref(canonical)
    result.changed_candidate_ids = sorted(changed)
    result.scope_rebuild_ids = sorted(scope_rebuild)
    result.invalidated_scope_ids = sorted(invalid_scopes)
    result.conflicts.sort(key=canonical_json)
    return result
