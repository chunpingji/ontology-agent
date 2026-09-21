"""Freeze source-grounded proposals without accepting facts or repairing model output."""

from __future__ import annotations

from collections.abc import Sequence

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.candidate_semantics import (
    identity_field_role_mismatch,
    missing_field_value_kind,
)
from app.services.extraction.ontology_guided.claim_protocol import (
    BridgeDependencyView,
    DiscoveryEnvelope,
    EntityDependencyView,
    ExternalCandidate,
    FrozenClaimSet,
    ObservationProposal,
    SchemaCard,
    allowed_entity_classes,
    endpoint_ids,
    iter_proposals,
    iter_quotes,
    semantic_content,
)
from app.services.extraction.ontology_guided.context import TaskContext, replay_fragment
from app.services.extraction.ontology_guided.contracts import EdgeSpec, SlotSpec, VersionedRef
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoverySchemaCard,
    RecordDiscoveryTarget,
    RecordDiscoveryTask,
    resolve_record_predicate,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from app.services.extraction.ontology_guided.value_observation import normalize_field_support


def freeze_proposal(proposal, *, task, **options):
    if not isinstance(task, RecognitionTask):
        raise ValueError("predicate_task_required")
    return _freeze_proposal(proposal, task=task, **options)


def freeze_record_proposal(proposal, *, task, **options):
    if not isinstance(task, RecordDiscoveryTask):
        raise ValueError("record_task_required")
    return _freeze_proposal(proposal, task=task, **options)


def _freeze_proposal(
    proposal: DiscoveryEnvelope, *, task: RecognitionTask, context: TaskContext,
    card: SchemaCard, index: RecordIndex, generation: int,
    entity_dependencies: Sequence[EntityDependencyView] = (),
    external_candidates: Sequence[ExternalCandidate] = (),
    bridge_dependencies: Sequence[BridgeDependencyView] = (),
    reference_resolution: bool = False,
    reference_dependencies=(),
    candidate_graph: bool = False,
) -> FrozenClaimSet:
    """Keep every proposal, recording local failures and their exact dependency closure.

    Structural namespace errors and corrupt server inputs fail the boundary. An
    invalid quote, IRI or endpoint marks only that proposal and its dependents;
    in particular, a relationship's original object group is never shortened.
    Observations remain untrusted hints and do not grant evidence or coverage.
    """
    proposal = DiscoveryEnvelope.model_validate(proposal.model_dump(mode="json"), strict=True)
    # ``representation`` is the authoritative grounding choice. Models sometimes
    # populate both schema branches even though the unused branch has no semantic
    # role. Remove that inactive material before quote validation so an ambiguous
    # unused component cannot invalidate an otherwise exact mention (and vice versa).
    proposal = proposal.model_copy(update={
        "entities": [
            entity.model_copy(update={
                "record_components": [],
            }) if entity.representation == "mention" else entity.model_copy(update={
                "mentions": [],
            })
            for entity in proposal.entities
        ],
    })
    proposal = normalize_field_support(proposal, context=context, index=index)
    if type(generation) is not int or generation < 1:
        raise ValueError("invalid_assertion_generation")
    evidence_revision = context.protocol_state.get("evidence_revision", 1)
    if type(evidence_revision) is not int or evidence_revision < 1:
        raise ValueError("invalid_evidence_revision")
    record_discovery = isinstance(task, RecordDiscoveryTask)
    evidence_review = context.tool_inputs.get("graph_phase") == "evidence_review"
    registered_relation = (not record_discovery and task.predicate_kind == "relationship"
                           and context.tool_inputs.get("recognition_pipeline")
                           == "record-entity-first-v1")
    subject_ref = (None if record_discovery else
                   VersionedRef(id=task.subject.entity_id, revision=task.subject.revision))
    if record_discovery:
        if (
            not isinstance(context.target, RecordDiscoveryTarget)
            or not isinstance(card, RecordDiscoverySchemaCard)
            or context.target.task_id != task.task_id
            or task.schema_card_id != card.schema_card_id
            or context.target.schema_card_id != card.schema_card_id
            or context.target.analysis_scope_ref != task.analysis_scope_ref
            or task.analysis_scope_ref != card.analysis_scope_ref
            or context.record_id != task.record_id
            or context.target.document_context.document_hash != index.ir.document_hash
        ):
            raise ValueError("freeze_context_identity_mismatch")
    elif (
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

    local_ref_map = {} if record_discovery else {task.subject.entity_id: subject_ref}
    entity_classes = {} if record_discovery else {task.subject.entity_id: task.subject.class_iri}
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
            # A pending evidence-review display uses the preceding odd revision;
            # verified targets and proof hashes always bind this final even one.
            revision=(2 * generation if kind in {"property", "relation"}
                      and context.tool_inputs.get("graph_phase") == "evidence_review"
                      else generation),
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

    def is_deferred_attribute_value(anchor):
        if (not record_discovery or task.purpose == "property_disambiguation"
                or anchor is None):
            return False
        return any(
            anchor.evidence_id == source.get("evidence_id")
            and anchor.span_start < source.get("span_end", 0)
            and anchor.span_end > source.get("span_start", 0)
            for field in context.tool_inputs.get("deferred_property_fields", [])
            for source in field.get("value_refs", [])
        )

    def check_missing_value(
        identity, quote, anchor, *, subject_id=None, predicate_iri=None, entity_referent=False,
    ):
        # The source gate must pass before the server records missing semantics.
        kind = missing_field_value_kind(
            quote=quote, anchor=anchor, context=context, index=index,
        ) if anchor is not None else None
        if kind is not None:
            issue(identity, f"{'entity_referent' if entity_referent else 'attribute_value'}_{kind}")
            observation = ObservationProposal(
                subject_id=subject_id, predicate_iri=predicate_iri, quote=quote,
                kind=kind, reason=(
                    "原文完整字段值或原文单元为缺失或未知标记，不能作为实体指称事实。"
                    if entity_referent else
                    "原文完整字段值为缺失或未知标记，不能作为已识别属性实值。"
                ),
            )
            if observation not in proposal.observations:
                proposal.observations.append(observation)

    def check_identity_role(identity, anchor, *, class_iri, predicate):
        if predicate is None:
            return
        properties = (card.for_class(class_iri).properties if record_discovery
                      else [value for value in card.predicates if isinstance(value, SlotSpec)])
        identity_iris = {iri for key in card.identity_keys if key.class_iri == class_iri
                         for iri in key.property_iris}
        if predicate.iri in identity_iris and not predicate.identity_key:
            predicate = predicate.model_copy(update={"identity_key": True})
        properties = [prop.model_copy(update={"identity_key": True})
                      if prop.iri in identity_iris else prop for prop in properties]
        if identity_field_role_mismatch(
            value_anchor=anchor, predicate=predicate, properties=properties,
            context=context, index=index,
        ):
            issue(identity, "identity_field_role_mismatch")

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
        candidate_relation = kind == "relation" and (
            evidence_review or candidate_graph and registered_relation
        )
        if registered_relation and kind in {"entity", "property", "external_link",
                                           "reference_binding"}:
            issue(identity, "claim_kind_outside_registered_relation")
            continue
        if record_discovery and (kind == "external_link"
                                 or kind == "relation" and not evidence_review):
            issue(identity, "claim_kind_outside_record_discovery")
            continue
        for quote in iter_quotes(payload):
            check_quote(identity, quote)
        for endpoint in endpoint_ids(payload):
            if endpoint not in entity_classes:
                issue(identity, "entity_reference_missing")
        if kind == "entity":
            if payload.class_iri not in allowed_classes:
                issue(identity, "class_outside_menu")
            if record_discovery and payload.representation == "record":
                def composition_signature(entity):
                    return sorted(
                        (component.role, evidence_hash(quote_anchor(component.quote)))
                        for component in entity.record_components
                    )
                try:
                    signature = composition_signature(payload)
                    if any(
                        dep.class_iri == payload.class_iri and dep.grounding_kind == "record"
                        and dep.proposal is not None
                        and composition_signature(dep.proposal) == signature
                        for dep in entity_dependencies
                    ):
                        issue(identity, "record_entity_already_registered")
                except ValueError:
                    issue(identity, "record_composition_source_invalid")
            if payload.representation == "mention":
                for quote in payload.mentions:
                    anchor = check_quote(identity, quote, fact_required=True)
                    check_missing_value(identity, quote, anchor, entity_referent=True)
            else:
                substantive = [component for component in payload.record_components
                               if component.role in {"subject", "value"}]
                has_subject = any(
                    component.role == "subject" for component in payload.record_components
                )
                if not substantive or (
                    not has_subject and len(payload.record_components) < 2
                ):
                    issue(identity, "record_fact_components_missing")
                for component in substantive:
                    anchor = check_quote(identity, component.quote, fact_required=True)
                    check_missing_value(identity, component.quote, anchor, entity_referent=True)
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
                value_anchor = check_quote(identity, identifier.value_quote, fact_required=True)
                check_missing_value(
                    identity, identifier.value_quote, value_anchor,
                    subject_id=identity, predicate_iri=identifier.predicate_iri,
                )
                if record_discovery and payload.class_iri in card.class_iris:
                    identifier_slot = next((prop for prop in card.for_class(
                        payload.class_iri,
                    ).properties if prop.iri == identifier.predicate_iri), None)
                    check_identity_role(
                        identity, value_anchor, class_iri=payload.class_iri,
                        predicate=identifier_slot,
                    )
                if is_deferred_attribute_value(value_anchor):
                    issue(identity, "attribute_deferred_to_disambiguation")
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

        if record_discovery:
            try:
                predicate = resolve_record_predicate(
                    card, entity_classes.get(payload.subject_id), payload.predicate_iri,
                )
            except ValueError:
                predicate = None
        else:
            predicate = predicates.get(payload.predicate_iri)
        expected_type = SlotSpec if kind == "property" else EdgeSpec
        if (
            not isinstance(predicate, expected_type)
            or not record_discovery and payload.predicate_iri != task.predicate_iri
        ):
            issue(identity, "predicate_outside_menu")
        elif predicate.constraint_status != "resolved" and not candidate_relation:
            issue(identity, "ontology_constraint_unresolved")
        if not record_discovery and payload.subject_id != task.subject.entity_id:
            issue(identity, "subject_outside_task")
        if kind == "property":
            value_anchor = check_quote(identity, payload.value_quote, fact_required=True)
            if isinstance(predicate, SlotSpec):
                check_missing_value(
                    identity, payload.value_quote, value_anchor,
                    subject_id=payload.subject_id, predicate_iri=payload.predicate_iri,
                )
                if (not record_discovery
                        or entity_classes.get(payload.subject_id) in card.class_iris):
                    check_identity_role(
                        identity, value_anchor, class_iri=entity_classes.get(payload.subject_id),
                        predicate=predicate,
                    )
            if is_deferred_attribute_value(value_anchor):
                issue(identity, "attribute_deferred_to_disambiguation")
        else:
            if reference_resolution and not candidate_relation:
                from app.services.extraction.ontology_guided.source_assertions import (
                    relation_bridge_issue,
                )

                bridge_issue = relation_bridge_issue(
                    payload.bridge_kind, local_ref_map.get(payload.subject_id),
                    context.target.document_context.root_ref, entity_dependencies,
                )
                if bridge_issue:
                    issue(identity, bridge_issue)
            if reference_resolution and payload.source_assertion is None and not candidate_relation:
                issue(identity, "source_assertion_required")
            assertion = payload.source_assertion
            known_bindings = {(view.binding_ref.id, view.binding_ref.revision): view
                              for view in reference_dependencies}
            binding_refs = (assertion.binding_dependency_refs
                            if assertion and not candidate_relation else [])
            for ref in binding_refs:
                view = known_bindings.get((ref.id, ref.revision))
                if view is None or view.entity_ref not in local_ref_map.values():
                    issue(identity, "binding_reference_outside_dependencies")
            if not reference_resolution and payload.source_assertion is not None:
                issue(identity, "reference_resolution_not_enabled")
            if payload.source_assertion is not None and not candidate_relation:
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
            if candidate_relation and not has_fact_source(list(iter_quotes(payload))):
                issue(identity, "relation_candidate_source_missing")
            if not candidate_relation and not has_fact_source(payload.bridge_support):
                issue(identity, "relation_fact_source_missing")
            if (not candidate_relation and payload.selection != "all"
                    and not payload.selection_support):
                issue(identity, "selection_evidence_missing")
        for qualifier in payload.qualifiers.scope_qualifiers:
            if qualifier.predicate_iri is not None and qualifier.predicate_iri not in predicates:
                issue(identity, "qualifier_predicate_outside_menu")
        assertion = getattr(payload, "source_assertion", None)
        if candidate_relation:
            # Source references above remain checked. A candidate does not assert
            # that a bridge or a semantic selection proof has been established.
            continue
        if (payload.bridge_kind == "resolved_reference_chain" and not payload.bridge_ref_ids
                and not (assertion and (assertion.binding_ids
                                       or assertion.binding_dependency_refs))):
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
