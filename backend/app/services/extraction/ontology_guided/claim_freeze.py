"""Freeze source-grounded proposals without accepting facts or repairing model output."""

from __future__ import annotations

from collections.abc import Sequence

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    BridgeDependencyView,
    DiscoveryEnvelope,
    EntityDependencyView,
    ExternalCandidate,
    FrozenClaimSet,
    SchemaCard,
    allowed_entity_classes,
    endpoint_ids,
    iter_proposals,
    iter_quotes,
    semantic_content,
)
from app.services.extraction.ontology_guided.context import TaskContext, replay_fragment
from app.services.extraction.ontology_guided.contracts import EdgeSpec, SlotSpec, VersionedRef
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote


def freeze_proposal(
    proposal: DiscoveryEnvelope, *, task: RecognitionTask, context: TaskContext,
    card: SchemaCard, index: RecordIndex, generation: int,
    entity_dependencies: Sequence[EntityDependencyView] = (),
    external_candidates: Sequence[ExternalCandidate] = (),
    bridge_dependencies: Sequence[BridgeDependencyView] = (),
    reference_resolution: bool = False,
) -> FrozenClaimSet:
    """Keep every proposal, recording local failures and their exact dependency closure.

    Structural namespace errors and corrupt server inputs fail the boundary. An
    invalid quote, IRI or endpoint marks only that proposal and its dependents;
    in particular, a relationship's original object group is never shortened.
    Observations remain untrusted hints and do not grant evidence or coverage.
    """
    proposal = DiscoveryEnvelope.model_validate(proposal.model_dump(mode="json"), strict=True)
    if type(generation) is not int or generation < 1:
        raise ValueError("invalid_assertion_generation")
    evidence_revision = context.protocol_state.get("evidence_revision", 1)
    if type(evidence_revision) is not int or evidence_revision < 1:
        raise ValueError("invalid_evidence_revision")
    subject_ref = VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
    if (
        context.target.task_id != task.task_id
        or context.target.subject_ref != task.subject
        or context.target.predicate_iri != task.predicate_iri
        or context.record_id != task.record_id
        or card.subject_ref != subject_ref
        or task.subject.class_iri not in card.class_iris
        or context.target.document_context.document_hash != index.ir.document_hash
    ):
        raise ValueError("freeze_context_identity_mismatch")
    index.ir.verify_identity()
    for fragment in context.fragments:
        replay_fragment(index.ir, fragment)

    local_ref_map = {task.subject.entity_id: subject_ref}
    entity_classes = {task.subject.entity_id: task.subject.class_iri}
    for dependency in entity_dependencies:
        EntityDependencyView.model_validate(dependency.model_dump(mode="json"), strict=True)
        identity = dependency.entity_ref.id
        if identity in local_ref_map and (
            local_ref_map[identity] != dependency.entity_ref
            or entity_classes[identity] != dependency.class_iri
        ):
            raise ValueError("registered_entity_identity_conflict")
        for anchor in dependency.source_refs:
            index.ir.resolve(anchor)
        local_ref_map[identity] = dependency.entity_ref
        entity_classes[identity] = dependency.class_iri
    candidates = {candidate.candidate_id: candidate for candidate in external_candidates}
    bridges = {bridge.bridge_id: bridge for bridge in bridge_dependencies}
    if len(candidates) != len(external_candidates) or len(bridges) != len(bridge_dependencies):
        raise ValueError("duplicate_frozen_dependency")
    for dependency in [*external_candidates, *bridge_dependencies]:
        type(dependency).model_validate(dependency.model_dump(mode="json"), strict=True)

    proposals = list(iter_proposals(proposal))
    if any(payload.local_id in local_ref_map for _, payload in proposals):
        raise ValueError("local_id_conflicts_with_registered_entity")
    for kind, payload in proposals:
        local_ref_map[payload.local_id] = VersionedRef(
            id=stable_id("frozen-claim", [
                task.claim_lineage_id, generation, kind, semantic_content(kind, payload),
            ]),
            revision=generation,
        )
        if kind == "entity":
            entity_classes[payload.local_id] = payload.class_iri

    issues: dict[str, list[str]] = {}

    def issue(local_id: str, code: str) -> None:
        codes = issues.setdefault(local_id, [])
        if code not in codes:
            codes.append(code)

    def quote_anchor(quote, *, fact_required=False):
        anchor, _ = resolve_fragment_quote(
            quote.evidence_id, quote.text, context.fragments,
            context_text=quote.context_text, fact_required=fact_required,
        )
        if index.ir.resolve(anchor) != quote.text:
            raise ValueError("source_excerpt_mismatch")
        return anchor

    def check_quote(local_id, quote, *, fact_required=False):
        try:
            return quote_anchor(quote, fact_required=fact_required)
        except ValueError as error:
            known = {
                "source_quote_outside_scope", "ambiguous_allowed_source_fragments",
                "source_excerpt_mismatch", "ambiguous_source_quote",
            }
            code = str(error) if str(error) in known else "source_identity_mismatch"
            if fact_required and code == "source_quote_outside_scope":
                code = "fact_source_outside_scope"
            issue(local_id, code)
            return None

    def has_fact_source(quotes):
        for quote in quotes:
            try:
                quote_anchor(quote, fact_required=True)
                return True
            except ValueError:
                continue
        return False

    allowed_classes = allowed_entity_classes(card)
    from app.services.extraction.ontology_guided.claim_identity import duplicate_mentions

    if not reference_resolution:
        if proposal.reference_bindings:
            for binding in proposal.reference_bindings:
                issue(binding.local_id, "reference_resolution_not_enabled")
        for local_id in duplicate_mentions(
            proposal.entities, entity_dependencies, local_ref_map, quote_anchor,
        ):
            issue(local_id, "entity_referent_already_registered")
    predicates = {predicate.iri: predicate for predicate in card.predicates}
    for kind, payload in proposals:
        identity = payload.local_id
        for quote in iter_quotes(payload):
            check_quote(identity, quote)
        for endpoint in endpoint_ids(payload):
            if endpoint not in entity_classes:
                issue(identity, "entity_reference_missing")
        if kind == "entity":
            if payload.class_iri not in allowed_classes:
                issue(identity, "class_outside_menu")
            if payload.representation == "mention":
                for quote in payload.mentions:
                    check_quote(identity, quote, fact_required=True)
            else:
                substantive = [component for component in payload.record_components
                               if component.role in {"subject", "value"}]
                if not substantive:
                    issue(identity, "record_fact_components_missing")
                for component in substantive:
                    check_quote(identity, component.quote, fact_required=True)
            identity_keys = {
                iri for key in card.identity_keys if key.class_iri == payload.class_iri
                for iri in key.property_iris
            }
            identity_keys.update(
                iri for predicate in card.predicates if isinstance(predicate, EdgeSpec)
                for definition in predicate.range_classes if definition.iri == payload.class_iri
                for iri in definition.identity_property_iris
            )
            for identifier in payload.identifier_claims:
                if identifier.predicate_iri not in identity_keys:
                    issue(identity, "identity_property_outside_menu")
                check_quote(identity, identifier.value_quote, fact_required=True)
            continue
        if kind == "reference_binding":
            fresh = {entity.local_id for entity in proposal.entities}
            registered = {entity.entity_ref.id for entity in entity_dependencies}
            if payload.source_id not in fresh or payload.target_id not in registered:
                issue(identity, "reference_binding_endpoint_invalid")
            if entity_classes.get(payload.source_id) != entity_classes.get(payload.target_id):
                issue(identity, "reference_binding_class_mismatch")
            if payload.target_id == context.target.document_context.root_ref.id:
                issue(identity, "reference_binding_root_forbidden")
            if len({binding.target_id for binding in proposal.reference_bindings
                    if binding.source_id == payload.source_id}) != 1:
                issue(identity, "reference_binding_ambiguous")
            continue
        if kind == "external_link":
            candidate = candidates.get(payload.external_candidate_id)
            if candidate is None:
                issue(identity, "external_candidate_missing")
            elif candidate.class_iri != entity_classes.get(payload.subject_id):
                issue(identity, "external_candidate_class_mismatch")
            if not has_fact_source(payload.identity_support):
                issue(identity, "identity_fact_source_missing")
            continue

        predicate = predicates.get(payload.predicate_iri)
        expected_type = SlotSpec if kind == "property" else EdgeSpec
        if (
            not isinstance(predicate, expected_type)
            or payload.predicate_iri != task.predicate_iri
        ):
            issue(identity, "predicate_outside_menu")
        elif predicate.constraint_status != "resolved":
            issue(identity, "ontology_constraint_unresolved")
        if payload.subject_id != task.subject.entity_id:
            issue(identity, "subject_outside_task")
        if kind == "property":
            check_quote(identity, payload.value_quote, fact_required=True)
        else:
            if reference_resolution:
                from app.services.extraction.ontology_guided.source_assertions import (
                    relation_bridge_issue,
                )

                bridge_issue = relation_bridge_issue(
                    payload.bridge_kind, local_ref_map.get(payload.subject_id),
                    context.target.document_context.root_ref, entity_dependencies,
                )
                if bridge_issue:
                    issue(identity, bridge_issue)
            if reference_resolution and payload.source_assertion is None:
                issue(identity, "source_assertion_required")
            if not reference_resolution and payload.source_assertion is not None:
                issue(identity, "reference_resolution_not_enabled")
            if payload.source_assertion is not None:
                assertion = payload.source_assertion
                if {item.object_id for item in assertion.object_support} != set(payload.object_ids):
                    issue(identity, "source_assertion_object_set_mismatch")
                if not has_fact_source(assertion.predicate_support):
                    issue(identity, "source_assertion_fact_source_missing")
                binding_ids = {binding.local_id for binding in proposal.reference_bindings}
                if any(identity not in binding_ids for identity in assertion.binding_ids):
                    issue(identity, "binding_reference_missing")
            if isinstance(predicate, EdgeSpec):
                for endpoint in payload.object_ids:
                    endpoint_class = entity_classes.get(endpoint)
                    if (endpoint_class is not None
                            and endpoint_class not in predicate.range_class_iris):
                        issue(identity, "object_class_outside_range")
            if not has_fact_source(payload.bridge_support):
                issue(identity, "relation_fact_source_missing")
            if payload.selection != "all" and not payload.selection_support:
                issue(identity, "selection_evidence_missing")
        for qualifier in payload.qualifiers.scope_qualifiers:
            if qualifier.predicate_iri is not None and qualifier.predicate_iri not in predicates:
                issue(identity, "qualifier_predicate_outside_menu")
        assertion = getattr(payload, "source_assertion", None)
        if (payload.bridge_kind == "resolved_reference_chain" and not payload.bridge_ref_ids
                and not (assertion and assertion.binding_ids)):
            issue(identity, "bridge_reference_missing")
        for bridge_id in payload.bridge_ref_ids:
            bridge = bridges.get(bridge_id)
            if bridge is None:
                issue(identity, "bridge_reference_missing")
            elif bridge.bridge_kind != payload.bridge_kind:
                issue(identity, "bridge_kind_mismatch")

    # Keep the complete group and propagate failure instead of pruning an object.
    changed = True
    while changed:
        changed = False
        for _, payload in proposals:
            assertion = getattr(payload, "source_assertion", None)
            dependencies = [*endpoint_ids(payload),
                            *(assertion.binding_ids if assertion else [])]
            if payload.local_id not in issues and any(endpoint in issues
                                                     for endpoint in dependencies):
                issue(payload.local_id, "entity_dependency_invalid")
                changed = True
    values = {
        **proposal.model_dump(mode="json"), "assertion_generation": generation,
        "evidence_revision": evidence_revision, "local_ref_map": local_ref_map,
        "claim_issues": issues,
    }
    return FrozenClaimSet(**values, content_hash=evidence_hash(values))
