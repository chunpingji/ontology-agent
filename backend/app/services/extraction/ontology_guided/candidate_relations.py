"""Project source-grounded relation proposals without claiming semantic verification."""

from __future__ import annotations

from copy import deepcopy

from .claim_protocol import EntityDependencyView, FrozenClaimSet, iter_quotes
from .contracts import (
    EdgeSpec,
    GraphEdge,
    GraphNode,
    GraphRelationshipGroup,
    TraversalScope,
    VersionedRef,
)
from .source_citations import resolve_fragment_quote


def candidate_relation_task(adapter, task) -> bool:
    return bool(
        adapter.record_discovery is not None
        and adapter.record_discovery.graph_phase == "candidate_graph"
        and getattr(task, "predicate_kind", None) == "relationship"
    )


def candidate_relation_schema(schema):
    """A source assertion may be incomplete; quoted evidence is still required locally."""
    schema = deepcopy(schema)
    relation = schema["$defs"]["RelationProposal"]
    relation["properties"]["source_assertion"] = {
        "anyOf": [{"$ref": "#/$defs/SourceAssertion"}, {"type": "null"}],
    }
    return schema


def project_candidate_relations(claims, *, task, context, card, current_entities=None):
    from .executor import TaskOutcome

    claims = FrozenClaimSet.model_validate(claims.model_dump(mode="json"), strict=True)
    dependencies = {
        value.entity_ref.id: value for value in (
            EntityDependencyView.model_validate(item)
            for item in context.tool_inputs["entity_dependencies"]
        )
    }
    current = None
    if current_entities is not None:
        values = (current_entities.values() if isinstance(current_entities, dict)
                  else current_entities)
        current = {node.entity_id: node for value in values
                   for node in [value if isinstance(value, GraphNode)
                                else GraphNode.model_validate(value)]}
    predicates = {value.iri: value for value in card.predicates if isinstance(value, EdgeSpec)}
    subject = VersionedRef(id=task.subject.entity_id, revision=task.subject.revision)
    edges, groups = [], []
    issues = [code for codes in claims.claim_issues.values() for code in codes]

    def anchors(quotes):
        return [resolve_fragment_quote(q.evidence_id, q.text, context.fragments,
                                       context_text=q.context_text)[0] for q in quotes]

    for payload in claims.relations:
        if payload.local_id in claims.claim_issues:
            continue
        predicate = predicates.get(payload.predicate_iri)
        endpoint_ids = [payload.subject_id, *payload.object_ids]
        refs = [claims.local_ref_map.get(identity) for identity in endpoint_ids]
        if (predicate is None or payload.predicate_iri != task.predicate_iri
                or payload.subject_id != task.subject.entity_id or refs[0] != subject
                or any(reference is None or identity not in dependencies
                       or dependencies[identity].entity_ref != reference
                       for identity, reference in zip(endpoint_ids, refs, strict=True))
                or subject.id in payload.object_ids
                or any(dependencies[identity].class_iri not in predicate.range_class_iris
                       for identity in payload.object_ids)):
            issues.append("candidate_relation_endpoint_or_predicate_invalid")
            continue
        if current is not None and any(
            reference.id not in current
            or current[reference.id].revision != reference.revision
            or current[reference.id].class_iri != dependencies[reference.id].class_iri
            for reference in refs
        ):
            issues.append("candidate_relation_endpoint_revision_mismatch")
            continue
        evidence = anchors(list(iter_quotes(payload)))
        if not evidence:
            issues.append("relation_candidate_source_missing")
            continue
        qualifier, assertion = payload.qualifiers, payload.source_assertion
        shared = dict(
            candidate_id=claims.local_ref_map[payload.local_id].id,
            revision=claims.local_ref_map[payload.local_id].revision,
            subject_ref=subject, predicate_iri=predicate.iri, predicate_label=predicate.label,
            decision_status="not_checked", structural_valid=False, model_supported=False,
            policy_eligible=False, proof_ref=None, decision_refs=[], dependency_refs=refs,
            polarity=qualifier.polarity, modality=qualifier.modality,
            scope=task.scope or TraversalScope.create(),
            conditions=[q.text for q in qualifier.condition_support],
            condition_evidence_refs=anchors(qualifier.condition_support),
            applicability={"qualifiers": [dict(
                predicate_iri=item.predicate_iri, text=item.quote.text,
                evidence_refs=[a.model_dump(mode="json") for a in anchors([item.quote])],
            ) for item in qualifier.scope_qualifiers]} if qualifier.scope_qualifiers else {},
            evidence_refs=evidence,
            subject_evidence_refs=anchors(assertion.subject_support) if assertion else [],
            object_evidence_refs=anchors([
                quote for item in assertion.object_support for quote in item.support
            ]) if assertion else [],
            predicate_evidence_refs=anchors(assertion.predicate_support)
            if assertion else anchors(payload.bridge_support),
            reason_code="relation_candidate_discovered",
            reason="已发现带原文证据的关系候选；未执行关系语义核验或图约束证明。",
        )
        if len(payload.object_ids) == 1:
            edges.append(GraphEdge(**shared, object_ref=refs[1]))
        else:
            groups.append(GraphRelationshipGroup(
                **shared, object_refs=refs[1:], selection=payload.selection,
                selection_evidence_refs=anchors(payload.selection_support),
            ))
    return TaskOutcome(
        semantic_outcome="not_checked", complete=not issues,
        reason_code=issues[0] if issues else (
            "relation_candidates_discovered" if edges or groups else "record_no_claims"
        ),
        reason="；".join(dict.fromkeys(issues)) or "已完成当前原文的关系候选发现，未核验关系。",
        edges=edges, relationship_groups=groups,
    )
