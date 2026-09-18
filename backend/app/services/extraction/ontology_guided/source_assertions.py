"""Close source roles for frozen relationships without guessing text entailment.

Containment only checks that the submitted source roles reach the submitted
assertion. Independent semantic facets still decide identity and the predicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import get_args

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.ontology_guided.claim_protocol import (
    AllowedBridge,
    _anchor_covers,
    ref_key,
)
from app.services.extraction.ontology_guided.contracts import BridgeStep, VersionedRef
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote


@dataclass
class SourceAssertionResult:
    issues: list[str] = field(default_factory=list)
    steps: list[BridgeStep] = field(default_factory=list)


RELATION_BRIDGE_ISSUES = frozenset({
    "document_subject_description_required", "document_description_subject_invalid",
})


def configured_document_root(subject_ref, root_ref, entity_dependencies):
    """Only the coordinator's exact configured document identity authorizes this bridge."""
    return next((dependency for dependency in entity_dependencies
                 if subject_ref == root_ref == dependency.entity_ref
                 and dependency.grounding_kind == "document_root"
                 and dependency.root_origin in {"user_specified", "user_specified_document_root"}),
                None)


def relation_bridge_options(subject_ref, root_ref, entity_dependencies):
    root = configured_document_root(subject_ref, root_ref, entity_dependencies)
    if root is not None and not root.source_refs:
        return ["document_subject_description"]
    return [kind for kind in get_args(AllowedBridge)
            if kind != "document_subject_description" or root is not None]


def relation_bridge_issue(bridge_kind, subject_ref, root_ref, entity_dependencies):
    if bridge_kind in relation_bridge_options(subject_ref, root_ref, entity_dependencies):
        return None
    return ("document_description_subject_invalid" if bridge_kind == "document_subject_description"
            else "document_subject_description_required")


def validate_source_assertion(
    claim, *, context, verification_input, decisions, reference_proofs=None,
) -> SourceAssertionResult:
    """Require replayable endpoint roles and an independently checked assertion.

    Legacy claims have no source_assertion and keep their frozen contract. New
    claims are required to carry it by freeze, before reaching this boundary.
    """
    result = SourceAssertionResult()
    payload = claim.payload
    assertion = getattr(payload, "source_assertion", None)
    if assertion is None:
        return result
    issues = result.issues
    by_kind = {decision.check_kind: decision for decision in decisions}
    local_refs = verification_input.local_ref_map

    def anchors(quotes):
        resolved = []
        for quote in quotes:
            try:
                resolved.append(resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments,
                    context_text=quote.context_text,
                )[0])
            except ValueError:
                issues.append("source_assertion_quote_invalid")
        return resolved

    def reviewed(kind, sources):
        decision = by_kind.get(kind)
        return decision is not None and decision.verdict == "supported" and all(
            any(_anchor_covers(support, source) for support in decision.support_refs)
            for source in sources
        )

    entity_proposals, root_sources = {}, {}
    for target in verification_input.targets:
        if target.target_kind == "entity":
            entity_proposals[ref_key(target.claim_ref)] = target.payload
    for dependency in verification_input.entity_dependencies:
        if dependency.proposal is not None:
            entity_proposals[ref_key(dependency.entity_ref)] = dependency.proposal
        elif dependency.grounding_kind == "document_root":
            root_sources[ref_key(dependency.entity_ref)] = [
                [anchor] for anchor in dependency.source_refs
            ]

    predicate_sources = anchors(assertion.predicate_support)
    if not predicate_sources:
        issues.append("source_assertion_predicate_missing")
    elif not reviewed("predicate", predicate_sources):
        issues.append("source_assertion_predicate_not_reviewed")

    objects = {item.object_id: anchors(item.support) for item in assertion.object_support}
    if len(objects) != len(assertion.object_support) or set(objects) != set(payload.object_ids):
        issues.append("source_assertion_object_set_mismatch")
    subject_sources = anchors(assertion.subject_support)
    subject_ref = local_refs.get(payload.subject_id)
    root_ref = context.target.document_context.root_ref
    bridge_issue = relation_bridge_issue(
        payload.bridge_kind, subject_ref, root_ref, verification_input.entity_dependencies,
    )
    if bridge_issue:
        issues.append(bridge_issue)
    document_description = (payload.bridge_kind == "document_subject_description"
                            and not bridge_issue)
    bindings, binding_steps = _verified_bindings(
        claim, assertion.binding_ids, verification_input, reference_proofs or {},
        anchors, issues, context,
    )

    def endpoint_sources(endpoint):
        ref = local_refs.get(endpoint)
        if ref is None:
            return []
        pending, visited, candidates = [ref_key(ref)], set(), []
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            proposal = entity_proposals.get(current)
            if proposal is not None:
                candidates.extend(_entity_sources(proposal, anchors))
            else:
                candidates.extend(root_sources.get(current, []))
            pending.extend(bindings.get(current, ()))
        return candidates

    def role_grounded(role, sources, endpoint):
        if not sources:
            issues.append(f"source_assertion_{role}_missing")
            return False
        # One exact mention, or all subject components of a composed record,
        # must be contained in the submitted role. Merely intersecting a name
        # or finding the same spelling at another location does not bind it.
        grounded = any(
            signature and all(any(_anchor_covers(source, anchor) for source in sources)
                              for anchor in signature)
            for signature in endpoint_sources(endpoint)
        )
        if not grounded:
            issues.append(f"source_assertion_{role}_endpoint_unbound")
        if not reviewed(f"{role}_binding", sources):
            issues.append(f"source_assertion_{role}_not_reviewed")
        return grounded

    subject_grounded = document_description or role_grounded(
        "subject", subject_sources, payload.subject_id,
    )
    if (document_description and subject_sources
            and not reviewed("subject_binding", subject_sources)):
        issues.append("source_assertion_subject_not_reviewed")

    for object_id in payload.object_ids:
        object_sources = objects.get(object_id, [])
        object_grounded = role_grounded("object", object_sources, object_id)
        if not subject_grounded or not object_grounded or not predicate_sources:
            continue
        if document_description:
            connected = _covered(predicate_sources, object_sources)
        else:
            connected = (
                _covered(predicate_sources, subject_sources)
                and _covered(predicate_sources, object_sources)
            ) or _structural_connection(
                context, payload.bridge_kind, subject_sources, object_sources, predicate_sources,
            )
        if not connected:
            issues.append("source_assertion_endpoint_chain_not_closed")

    if not issues:
        result.steps.extend(binding_steps)
        predicate_decision = by_kind["predicate"]
        result.steps.append(BridgeStep(
            step_kind="predicate_assertion",
            input_refs=[local_refs[payload.subject_id],
                        *(local_refs[object_id] for object_id in payload.object_ids)],
            output_role="predicate_assertion", source_refs=predicate_sources,
            decision_ref=VersionedRef(id=predicate_decision.decision_id, revision=1),
        ))
    return result


def _entity_sources(proposal, anchors):
    if proposal.representation == "mention":
        return [[anchor] for anchor in anchors(proposal.mentions)]
    components = [component.quote for component in proposal.record_components
                  if component.role == "subject"]
    if not components:
        components = [component.quote for component in proposal.record_components]
    return [anchors(components)]


def _covered(outer: list[EvidenceAnchor], inner: list[EvidenceAnchor]) -> bool:
    return bool(inner) and all(any(_anchor_covers(a, b) for a in outer) for b in inner)


def _structural_connection(context, kind, subjects, objects, predicates):
    """A single IR mapping must supply the owner, target and field assertion.

    This authorizes the structure for independent role/predicate verification;
    it does not infer a relation merely because values occupy one table row.
    """
    expected = {"role_mapped_table": "table", "owned_field_group": "field_group"}.get(kind)
    if expected is None:
        return False
    for binding in context.field_bindings:
        if binding.kind != expected or binding.mapping_status != "structural_candidate":
            continue
        if (_covered(binding.owner_candidate_refs, subjects)
                and _covered(binding.target_value_refs, objects)
                and _covered(predicates, binding.label_refs)):
            return True
    return False


def _verified_bindings(claim, identities, verification_input, proofs, anchors, issues, context):
    """Only exact, independently accepted binding claims can connect source roles."""
    graph, steps = {}, []
    targets = {target.payload.local_id: target for target in verification_input.targets
               if target.target_kind == "reference_binding"}
    dependencies = {ref_key(ref) for ref in claim.dependency_refs}
    for identity in identities:
        target = targets.get(identity)
        proof = proofs.get(ref_key(target.claim_ref)) if target is not None else None
        if (target is None or ref_key(target.claim_ref) not in dependencies
                or target.scope != claim.scope or proof is None or not proof.policy_eligible
                or not proof.structural_valid or not proof.model_supported
                or proof.independent_review == "rejected"
                or proof.target.claim_ref != target.claim_ref
                or proof.target.target_id != target.target_id
                or proof.target.literal_hash != target.content_hash
                or proof.target.context_hash != context.target.context_hash
                or proof.target.source_scope_hash != context.target.source_scope_hash
                or proof.target.document_context != context.target.document_context
                or not proof.decisions or any(d.verdict != "supported" for d in proof.decisions)
                or {d.check_kind for d in proof.decisions} != set(target.required_facets)):
            issues.append("source_assertion_reference_not_verified")
            continue
        proposal = target.payload
        left = verification_input.local_ref_map.get(proposal.source_id)
        right = verification_input.local_ref_map.get(proposal.target_id)
        sources = anchors(proposal.support)
        identity_decision = next((decision for decision in proof.decisions
                                  if decision.check_kind == "reference_identity"), None)
        if left is None or right is None or not sources or identity_decision is None:
            issues.append("source_assertion_reference_invalid")
            continue
        graph.setdefault(ref_key(left), set()).add(ref_key(right))
        graph.setdefault(ref_key(right), set()).add(ref_key(left))
        steps.append(BridgeStep(
            step_kind="same_referent", input_refs=[left, right], output_role="referent_binding",
            source_refs=sources,
            decision_ref=VersionedRef(id=identity_decision.decision_id, revision=1),
        ))
    return graph, steps


def validate_reference_binding(claim, *, context, verification_input, decisions):
    """Verify the structural identity question without treating names as identity."""
    issues = []
    payload = claim.payload
    local_refs = verification_input.local_ref_map
    source_ref = local_refs.get(payload.source_id)
    destination_ref = local_refs.get(payload.target_id)
    source = next((target for target in verification_input.targets
                   if target.target_kind == "entity" and target.claim_ref == source_ref), None)
    destination = next((dependency for dependency in verification_input.entity_dependencies
                        if dependency.entity_ref == destination_ref), None)
    if (source is None or destination is None or source_ref == destination_ref
            or destination.grounding_kind == "document_root" or destination.proposal is None):
        return ["reference_binding_endpoints_invalid"]
    if source.payload.class_iri != destination.class_iri:
        issues.append("reference_binding_type_mismatch")

    def anchors(quotes):
        result = []
        for quote in quotes:
            try:
                result.append(resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments,
                    context_text=quote.context_text,
                )[0])
            except ValueError:
                issues.append("reference_binding_quote_invalid")
        return result

    support = anchors(payload.support)
    if not support:
        issues.append("reference_binding_support_missing")
    for proposal, role in ((source.payload, "source"), (destination.proposal, "target")):
        if not any(signature and _covered(support, signature)
                   for signature in _entity_sources(proposal, anchors)):
            issues.append(f"reference_binding_{role}_unbound")
    by_kind = {decision.check_kind: decision for decision in decisions}
    for name in ("reference_identity", "reference_scope"):
        decision = by_kind.get(name)
        if (decision is None or decision.verdict != "supported"
                or not _covered(decision.support_refs, support)):
            issues.append(f"{name}_support_not_reviewed")
    destinations = {
        ref_key(local_refs[target.payload.target_id])
        for target in verification_input.targets
        if target.target_kind == "reference_binding"
        and local_refs.get(target.payload.source_id) == source_ref
        and target.payload.target_id in local_refs
    }
    if len(destinations) > 1:
        issues.append("reference_binding_conflicting_targets")
    return issues
