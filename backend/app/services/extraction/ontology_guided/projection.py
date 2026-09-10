"""Server-authoritative graph projections from exact candidate revisions."""

from __future__ import annotations

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    CoverageSummary,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphSnapshot,
    RunProgress,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex


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


def effective_proof_gate(item: GraphEdge | GraphProperty) -> bool:
    """Fail closed unless the current candidate has a complete eligible proof."""
    return (
        item.structural_valid
        and item.model_supported
        and item.policy_eligible
        and item.proof_ref is not None
        and bool(item.decision_refs)
        and item.independent_review != "rejected"
    )


def _candidate_key(item: GraphEdge | GraphProperty) -> str:
    return f"{item.candidate_id}@{item.revision}"


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
) -> GraphSnapshot:
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
        generated_from_hash=evidence_hash(payload),
    )
