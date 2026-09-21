"""Retain unaccepted evidence-review claims without inventing source or proof."""

from __future__ import annotations

from .claim_protocol import (
    EntityDependencyView,
    FrozenClaimSet,
    endpoint_ids,
    iter_quotes,
    ref_key,
)
from .contracts import (
    EdgeSpec,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphRelationshipGroup,
    SlotSpec,
    ValidationDiagnostic,
)
from .source_citations import resolve_fragment_quote


def retain_review_candidates(
    claims, *, context, card, scope, current_entities, accepted_ids=(),
    canonical_refs=None, reviews=None, checks=None, pending_reason="review_pending",
    pending_revision=False,
):
    """Keep safe source projections; failed proof is never converted into an acceptance."""
    from .record_discovery import resolve_claim_schema

    claims = FrozenClaimSet.model_validate(claims.model_dump(mode="json"), strict=True)
    canonical_refs, reviews, checks = canonical_refs or {}, reviews or {}, checks or {}
    values = (current_entities.values() if isinstance(current_entities, dict)
              else current_entities)
    nodes = {node.entity_id: node for value in values for node in [
        value if isinstance(value, GraphNode) else GraphNode.model_validate(value)
    ]}
    dependencies = [EntityDependencyView.model_validate(value)
                    for value in context.tool_inputs.get("entity_dependencies", [])]
    edges, groups, properties = [], [], []

    def resolved(quotes, *, fact_required=False):
        result = []
        for quote in quotes:
            try:
                anchor = resolve_fragment_quote(
                    quote.evidence_id, quote.text, context.fragments,
                    context_text=quote.context_text, fact_required=fact_required,
                )[0]
            except ValueError:
                continue
            if anchor not in result:
                result.append(anchor)
        return result

    for kind, payload in [*(('property', p) for p in claims.properties),
                          *(('relation', r) for r in claims.relations)]:
        claim_ref = claims.local_ref_map[payload.local_id]
        if claim_ref.id in accepted_ids:
            continue
        references = [claims.local_ref_map.get(identity) for identity in endpoint_ids(payload)]
        references = [canonical_refs.get(ref_key(ref), ref) if ref else None for ref in references]
        if any(ref is None or ref.id not in nodes or nodes[ref.id].revision != ref.revision
               or nodes[ref.id].decision_status != "supported" for ref in references):
            continue
        try:
            owner_card = resolve_claim_schema(
                card, payload, claims.local_ref_map, claims.entities, dependencies,
            )
        except ValueError:
            continue
        expected = SlotSpec if kind == "property" else EdgeSpec
        predicate = next((p for p in owner_card.predicates
                          if isinstance(p, expected) and p.iri == payload.predicate_iri), None)
        if predicate is None:
            continue
        subject = claims.local_ref_map[payload.subject_id]
        subject = canonical_refs.get(ref_key(subject), subject)
        if nodes[subject.id].class_iri not in owner_card.class_iris:
            continue
        local_issues = list(claims.claim_issues.get(payload.local_id, []))
        assertion = getattr(payload, "source_assertion", None)
        if kind == "property":
            # A missing marker is an observation, never a rejected/accepted value.
            if any(code in {"attribute_value_missing", "attribute_value_unknown"}
                   for code in local_issues):
                continue
            if not resolved([payload.value_quote], fact_required=True):
                continue
        else:
            if any(nodes[ref.id].class_iri not in predicate.range_class_iris
                   for ref in references[1:]):
                continue
            if not resolved(list(iter_quotes(payload)), fact_required=True):
                continue
        review = reviews.get(payload.local_id, {})
        local_issues.extend(review.get("issues", []))
        if not local_issues:
            local_issues.append(pending_reason)
        decisions = review.get("decisions", [])
        result = checks.get(payload.local_id)
        diagnostics = list(result.validation_diagnostics) if result else []
        if "ontology_constraint_unresolved" in local_issues:
            diagnostics.append(ValidationDiagnostic(
                check="schema", status="incomplete",
                reason_codes=["ontology_constraint_unresolved"],
                message="本体约束未解决，原文候选尚未采信。",
            ))
        qualifier = payload.qualifiers
        shared = dict(
            candidate_id=claim_ref.id,
            revision=claim_ref.revision - 1 if pending_revision else claim_ref.revision,
            subject_ref=subject, predicate_iri=predicate.iri, predicate_label=predicate.label,
            decision_status=review.get("status", "not_checked"), structural_valid=False,
            model_supported=False, policy_eligible=False, proof_ref=None,
            decision_refs=[dict(id=d.decision_id, revision=1) for d in decisions],
            dependency_refs=references, evidence_refs=resolved(list(iter_quotes(payload))),
            subject_evidence_refs=resolved(assertion.subject_support) if assertion else [],
            predicate_evidence_refs=(resolved(assertion.predicate_support) if assertion else
                                     resolved(payload.field_support if kind == "property"
                                              else payload.bridge_support)),
            counterevidence_refs=[a for decision in decisions
                                  for a in decision.counterevidence_refs],
            polarity=qualifier.polarity, modality=qualifier.modality, scope=scope,
            conditions=[quote.text for quote in qualifier.condition_support if resolved([quote])],
            condition_evidence_refs=resolved(qualifier.condition_support),
            applicability={"qualifiers": [dict(
                predicate_iri=value.predicate_iri, text=value.quote.text,
                evidence_refs=[a.model_dump(mode="json") for a in resolved([value.quote])],
            ) for value in qualifier.scope_qualifiers if resolved([value.quote])]}
            if qualifier.scope_qualifiers else {},
            reason_code=local_issues[0], reason="；".join(dict.fromkeys([
                *local_issues, *(d.reason for d in decisions if d.reason),
            ])),
            validation_diagnostics=diagnostics,
        )
        if kind == "property":
            properties.append(GraphProperty(
                **shared, raw_value=payload.value_quote.text,
                raw_unit="；".join(dict.fromkeys(q.text for q in payload.unit_support
                                            if resolved([q]))) or None,
                normalized_value=None, normalization_available=False,
                value_evidence_refs=resolved([payload.value_quote]),
                unit_evidence_refs=resolved(payload.unit_support),
            ))
        else:
            object_refs = [canonical_refs.get(ref_key(claims.local_ref_map[identity]),
                                             claims.local_ref_map[identity])
                           for identity in payload.object_ids]
            if len({ref_key(ref) for ref in object_refs}) != len(object_refs):
                continue
            objects = resolved([q for item in assertion.object_support for q in item.support]
                               if assertion else [])
            if len(object_refs) == 1:
                edges.append(GraphEdge(**shared, object_ref=object_refs[0],
                                       object_evidence_refs=objects))
            else:
                groups.append(GraphRelationshipGroup(
                    **shared, object_refs=object_refs, object_evidence_refs=objects,
                    selection=payload.selection,
                    selection_evidence_refs=resolved(payload.selection_support),
                ))
    return edges, groups, properties


def pending_review_outcome(claims, *, context, card, scope, current_entities, reason):
    from .executor import TaskOutcome

    edges, groups, properties = retain_review_candidates(
        claims, context=context, card=card, scope=scope, current_entities=current_entities,
        pending_reason=reason,
        pending_revision=True,
    )
    return TaskOutcome(
        semantic_outcome="not_checked", complete=False, reason_code=reason,
        reason="候选原文已保存，核验尚未完成。", edges=edges,
        relationship_groups=groups, properties=properties,
    )
