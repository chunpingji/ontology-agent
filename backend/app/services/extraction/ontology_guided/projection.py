"""Server-authoritative graph projections from exact candidate revisions."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    CoverageSummary,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphRelationshipGroup,
    GraphSnapshot,
    RunProgress,
    ScopeMember,
    TraversalScope,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex

TOOL_EXTRACTION_PROTOCOL = "ontology-tool-extraction-v1"
TOOL_PROJECTION_POLICY = "ontology-tool-graph-v1"
GraphClaim = GraphEdge | GraphProperty | GraphRelationshipGroup


def _eligible_status(status: str, polarity: str, projection: str) -> bool:
    if projection == "all":
        return True
    if projection == "effective":
        return status == "supported" and polarity == "affirmed"
    if projection == "negated":
        return polarity == "negated"
    if projection == "conditional":
        return polarity == "conditional"
    if projection in {"undetermined", "unassociated"}:
        return status in {"undetermined", "not_checked"}
    if projection == "rejected":
        return status == "unsupported"
    return False


def effective_proof_gate(item: GraphClaim) -> bool:
    """Fail closed unless the current candidate has a complete eligible proof."""
    return (
        item.structural_valid
        and item.model_supported
        and item.policy_eligible
        and item.proof_ref is not None
        and bool(item.decision_refs)
        and item.independent_review != "rejected"
    )


def _candidate_key(item: GraphClaim) -> str:
    return f"{item.candidate_id}@{item.revision}"


def _claim_references(item: GraphClaim) -> list[VersionedRef]:
    references = [item.subject_ref, *item.decision_refs, *item.dependency_refs]
    if item.proof_ref is not None:
        references.append(item.proof_ref)
    if isinstance(item, GraphEdge):
        references.append(item.object_ref)
    elif isinstance(item, GraphRelationshipGroup):
        references.extend(item.object_refs)
    for step in item.scope.members:
        references.extend((step.relation_ref, step.member_ref))
    return references


def _current_proof(item: GraphClaim, dependencies: DependencyIndex) -> bool:
    if (isinstance(item, GraphRelationshipGroup)
            and (item.selection == "undetermined" or not item.selection_evidence_refs)):
        return False
    return (item.decision_status == "supported" and effective_proof_gate(item)
            and dependencies.is_valid(_candidate_key(item))
            and dependencies.is_valid_references(_claim_references(item)))


def _members(relation: GraphEdge | GraphRelationshipGroup) -> list[VersionedRef]:
    return [relation.object_ref] if isinstance(relation, GraphEdge) else relation.object_refs


def _scope_member_key(member: ScopeMember) -> tuple:
    return (member.relation_ref.id, member.relation_ref.revision,
            member.member_ref.id, member.member_ref.revision)


@dataclass(frozen=True)
class FrontierEligibility:
    eligible: bool
    scope: TraversalScope
    reason_code: str


def frontier_eligibility(
    relation: GraphEdge | GraphRelationshipGroup, *, member: VersionedRef,
    inherited_scope: TraversalScope, dependencies: DependencyIndex,
) -> FrontierEligibility:
    """Permit scoped exploration without promoting a qualified claim into a plain edge."""
    reason = None
    if member not in _members(relation):
        reason = "member_revision_mismatch"
    elif relation.polarity != "affirmed":
        reason = "relation_not_affirmed"
    elif relation.modality == "unspecified":
        reason = "modality_unspecified"
    elif isinstance(relation, GraphRelationshipGroup) and relation.selection == "undetermined":
        reason = "selection_undetermined"
    elif not _current_proof(relation, dependencies):
        reason = "relation_proof_incomplete"
    elif not dependencies.is_valid_references([
        ref for step in inherited_scope.members for ref in (step.relation_ref, step.member_ref)
    ]):
        reason = "scope_dependency_invalidated"
    inherited = {_scope_member_key(step) for step in inherited_scope.members}
    if reason is None and any(_scope_member_key(step) not in inherited
                              for step in relation.scope.members):
        reason = "scope_mismatch"
    step = ScopeMember(
        relation_ref=VersionedRef(id=relation.candidate_id, revision=relation.revision),
        member_ref=member,
    )
    if reason is None:
        for prior in inherited_scope.members:
            if prior.relation_ref.id != relation.candidate_id:
                continue
            if prior.relation_ref.revision != relation.revision:
                reason = "scope_revision_mismatch"
            elif prior.member_ref == member:
                reason = "scope_cycle"
            elif isinstance(relation, GraphRelationshipGroup) and relation.selection == "one_of":
                reason = "scope_conflict"
            if reason is not None:
                break
    if reason is not None:
        return FrontierEligibility(False, inherited_scope, reason)
    qualified = (isinstance(relation, GraphRelationshipGroup) or relation.conditions
                 or relation.applicability or relation.modality != "asserted")
    scope = (TraversalScope.create([*inherited_scope.members, step])
             if qualified else inherited_scope)
    return FrontierEligibility(True, scope, "eligible")


def project_graph(
    *,
    recognition_run_id: str,
    run_revision: int,
    event_head: int,
    metadata_snapshot_id: str | None,
    root_ref: VersionedRef,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    properties: list[GraphProperty],
    coverage: list[CoverageSummary],
    progress: RunProgress,
    dependency_index: DependencyIndex | None = None,
    projection: str = "effective",
    artifact_status: str = "partial",
    current_content_hash: str | None = None,
    relationship_groups: list[GraphRelationshipGroup] | None = None,
    extraction_protocol: str | None = None,
) -> GraphSnapshot:
    if extraction_protocol is not None and extraction_protocol != TOOL_EXTRACTION_PROTOCOL:
        raise ValueError("unsupported_extraction_protocol")
    if extraction_protocol == TOOL_EXTRACTION_PROTOCOL:
        return _project_tool_graph(
            recognition_run_id=recognition_run_id, run_revision=run_revision, event_head=event_head,
            metadata_snapshot_id=metadata_snapshot_id, root_ref=root_ref, nodes=nodes, edges=edges,
            properties=properties, relationship_groups=relationship_groups or [], coverage=coverage,
            progress=progress, dependencies=dependency_index or DependencyIndex(),
            projection=projection, artifact_status=artifact_status,
            current_content_hash=current_content_hash,
        )
    if projection == "verified":
        raise ValueError("unsupported_projection")
    if relationship_groups:
        raise ValueError("relationship_groups_require_tool_protocol")
    node_heads = {item.entity_id: item.revision for item in nodes}
    dependencies = dependency_index or DependencyIndex()
    selected_edges = []
    for edge in edges:
        exact_endpoints = (
            node_heads.get(edge.subject_ref.id) == edge.subject_ref.revision
            and node_heads.get(edge.object_ref.id) == edge.object_ref.revision
        )
        if (
            exact_endpoints
            and _eligible_status(edge.decision_status, edge.polarity, projection)
            and (
                projection != "effective"
                or (
                    not edge.conditions
                    and not edge.applicability
                    and dependencies.is_valid(_candidate_key(edge))
                    and effective_proof_gate(edge)
                )
            )
        ):
            selected_edges.append(edge)
    selected_properties = []
    for item in properties:
        exact_subject = node_heads.get(item.subject_ref.id) == item.subject_ref.revision
        if (
            exact_subject
            and _eligible_status(item.decision_status, item.polarity, projection)
            and (
                projection != "effective"
                or (
                    not item.conditions
                    and not item.applicability
                    and dependencies.is_valid(_candidate_key(item))
                    and effective_proof_gate(item)
                )
            )
        ):
            selected_properties.append(item)
    if projection == "effective":
        reached = {root_ref.id}
        changed = True
        while changed:
            changed = False
            for edge in selected_edges:
                if edge.subject_ref.id in reached and edge.object_ref.id not in reached:
                    reached.add(edge.object_ref.id)
                    changed = True
        selected_edges = [
            edge
            for edge in selected_edges
            if edge.subject_ref.id in reached and edge.object_ref.id in reached
        ]
        selected_properties = [
            item for item in selected_properties if item.subject_ref.id in reached
        ]
        selected_nodes = [item for item in nodes if item.entity_id in reached]
    else:
        endpoint_ids = {root_ref.id}
        endpoint_ids.update(edge.subject_ref.id for edge in selected_edges)
        endpoint_ids.update(edge.object_ref.id for edge in selected_edges)
        endpoint_ids.update(item.subject_ref.id for item in selected_properties)
        selected_nodes = [item for item in nodes if item.entity_id in endpoint_ids]
    payload = None
    if current_content_hash is None:
        payload = {
        "recognition_run_id": recognition_run_id,
        "run_revision": run_revision,
        "event_head": event_head,
        "metadata_snapshot_id": metadata_snapshot_id,
        "root_ref": root_ref.model_dump(mode="json"),
        "projection": projection,
        "nodes": [item.model_dump(mode="json") for item in selected_nodes],
        "edges": [item.model_dump(mode="json") for item in selected_edges],
        "properties": [item.model_dump(mode="json") for item in selected_properties],
        "coverage": [item.model_dump(mode="json") for item in coverage],
        "progress": progress.model_dump(mode="json"),
    }
    return GraphSnapshot(
        recognition_run_id=recognition_run_id,
        run_revision=run_revision,
        event_head=event_head,
        metadata_snapshot_id=metadata_snapshot_id,
        root_ref=root_ref,
        projection=projection,
        artifact_status=artifact_status,
        nodes=selected_nodes,
        edges=selected_edges,
        properties=selected_properties,
        coverage=coverage,
        progress=progress,
        generated_from_hash=current_content_hash or evidence_hash(payload),
    )


def _project_tool_graph(
    *, recognition_run_id, run_revision, event_head, metadata_snapshot_id, root_ref,
    nodes, edges, properties, relationship_groups, coverage, progress, dependencies,
    projection, artifact_status, current_content_hash,
) -> GraphSnapshot:
    node_heads = {node.entity_id: node for node in nodes}
    relation_heads = {item.candidate_id: item for item in [*edges, *relationship_groups]}
    if (len(node_heads) != len(nodes)
            or len(relation_heads) != len(edges) + len(relationship_groups)):
        raise ValueError("current_graph_identity_ambiguous")

    def current_references(references):
        for reference in references:
            current = node_heads.get(reference.id) or relation_heads.get(reference.id)
            if current is not None and current.revision != reference.revision:
                return False
        return dependencies.is_valid_references(references)

    def node_valid(node):
        if (node.decision_status != "supported" or node.independent_review == "rejected"
                or not dependencies.is_valid(f"{node.entity_id}@{node.revision}")):
            return False
        if node.root:
            return node.entity_id == root_ref.id and node.revision == root_ref.revision
        proof_refs = [node.referent_ref, node.type_decision_ref, node.referent_decision_ref]
        if node.grounding_kind == "record":
            proof_refs.append(node.composition_decision_ref)
        return (all(ref is not None for ref in proof_refs) and bool(node.evidence_refs)
                and current_references([*proof_refs, *node.dependency_refs]))

    valid_nodes = {node.entity_id for node in nodes if node_valid(node)}

    def exact_node(ref, *, proved=False):
        current = node_heads.get(ref.id)
        return (current is not None and current.revision == ref.revision
                and (not proved or ref.id in valid_nodes))

    def exact_endpoints(item, *, proved=False):
        refs = [item.subject_ref]
        if isinstance(item, (GraphEdge, GraphRelationshipGroup)):
            refs.extend(_members(item))
        return all(exact_node(ref, proved=proved) for ref in refs)

    def scope_valid(scope, trail=frozenset()):
        scope_keys = {_scope_member_key(step) for step in scope.members}
        selected = {}
        for step in scope.members:
            relation = relation_heads.get(step.relation_ref.id)
            if (relation is None or relation.revision != step.relation_ref.revision
                    or relation.candidate_id in trail or step.member_ref not in _members(relation)
                    or not exact_endpoints(relation, proved=True)
                    or relation.polarity != "affirmed" or relation.modality == "unspecified"
                    or not _current_proof(relation, dependencies)
                    or not current_references(_claim_references(relation))):
                return False
            if isinstance(relation, GraphRelationshipGroup) and relation.selection == "one_of":
                previous = selected.setdefault(relation.candidate_id, step.member_ref)
                if previous != step.member_ref:
                    return False
            if (any(_scope_member_key(parent) not in scope_keys
                    for parent in relation.scope.members)
                    or not scope_valid(relation.scope, trail | {relation.candidate_id})):
                return False
        return True

    proved_views = {"verified", "effective", "conditional", "negated"}

    def selected(item):
        if not exact_endpoints(item, proved=projection in proved_views):
            return False
        if projection in proved_views and (
            not _current_proof(item, dependencies) or not scope_valid(item.scope)
            or not current_references(_claim_references(item))
        ):
            return False
        if projection == "verified":
            return True
        if projection == "effective":
            return (item.polarity == "affirmed" and item.modality == "asserted"
                    and not item.scope.members and not item.conditions and not item.applicability
                    and (not isinstance(item, GraphRelationshipGroup) or item.selection == "all"))
        if projection == "conditional":
            return bool(item.conditions or item.applicability or item.scope.members)
        return _eligible_status(item.decision_status, item.polarity, projection)

    selected_edges = [item for item in edges if selected(item)]
    selected_properties = [item for item in properties if selected(item)]
    selected_groups = [item for item in relationship_groups if selected(item)]
    if projection == "effective":
        reached = {root_ref.id} if exact_node(root_ref, proved=True) else set()
        changed = True
        while changed:
            changed = False
            for relation in [*selected_edges, *selected_groups]:
                if relation.subject_ref.id in reached:
                    new = {ref.id for ref in _members(relation)} - reached
                    if new:
                        reached.update(new)
                        changed = True
        selected_edges = [item for item in selected_edges if item.subject_ref.id in reached]
        selected_groups = [item for item in selected_groups if item.subject_ref.id in reached]
        selected_properties = [item for item in selected_properties
                               if item.subject_ref.id in reached]
        selected_nodes = [node for node in nodes if node.entity_id in reached]
    elif projection == "verified":
        selected_nodes = [node for node in nodes if node.entity_id in valid_nodes]
    elif projection == "all":
        selected_nodes = list(nodes)
    else:
        endpoints = {root_ref.id}
        for item in [*selected_edges, *selected_groups, *selected_properties]:
            endpoints.add(item.subject_ref.id)
            if isinstance(item, (GraphEdge, GraphRelationshipGroup)):
                endpoints.update(ref.id for ref in _members(item))
        selected_nodes = [node for node in nodes if node.entity_id in endpoints]
    payload = None if current_content_hash is not None else {
        "recognition_run_id": recognition_run_id, "run_revision": run_revision,
        "event_head": event_head, "metadata_snapshot_id": metadata_snapshot_id,
        "root_ref": root_ref.model_dump(mode="json"), "projection": projection,
        "projection_policy": TOOL_PROJECTION_POLICY,
        "extraction_protocol": TOOL_EXTRACTION_PROTOCOL,
        "nodes": [node.model_dump(mode="json") for node in selected_nodes],
        "edges": [item.model_dump(mode="json") for item in selected_edges],
        "properties": [item.model_dump(mode="json") for item in selected_properties],
        "relationship_groups": [item.model_dump(mode="json") for item in selected_groups],
        "coverage": [item.model_dump(mode="json") for item in coverage],
        "progress": progress.model_dump(mode="json"),
    }
    return GraphSnapshot(
        recognition_run_id=recognition_run_id, run_revision=run_revision, event_head=event_head,
        metadata_snapshot_id=metadata_snapshot_id, root_ref=root_ref, projection=projection,
        projection_policy=TOOL_PROJECTION_POLICY, artifact_status=artifact_status,
        nodes=selected_nodes, edges=selected_edges, properties=selected_properties,
        relationship_groups=selected_groups, coverage=coverage, progress=progress,
        generated_from_hash=current_content_hash or evidence_hash(payload),
    )
