"""Pure mapping from the internal graph snapshot to the public v1 contract."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import (
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphSnapshot,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.projection import project_graph
from app.services.extraction.ontology_guided.records import RecordIndex

PUBLIC_TO_INTERNAL_PROJECTION = {
    "effective_affirmed": "effective",
    "all_candidates": "all",
    "unassociated": "unassociated",
    "negated": "negated",
    "conditional": "conditional",
    "undetermined": "undetermined",
    "rejected": "rejected",
}


def public_ranking_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Project committed diagnostics without exposing query text or cache contents."""
    service = state.get("service") or {}
    epochs = []
    for epoch in [*(service.get("epochs") or []), *(service.get("pending_epochs") or [])]:
        if epoch.get("status") not in {"committed", "paused"}:
            continue
        observations = epoch.get("observations") or []
        by_record: dict[str, list[dict]] = {}
        for observation in observations:
            by_record.setdefault(observation["record_id"], []).append(observation)
        records = []
        for rank, record_id in enumerate(epoch.get("ordered_record_ids") or [], start=1):
            values = by_record.get(record_id, [])
            channels = sorted({
                channel
                for value in values
                for channel in value.get("channel_hits", [])
            })
            records.append({
                "record_id": record_id,
                "rank": rank,
                "channels": channels,
                "intent_ranks": {
                    value["retrieval_intent"]: value["intent_rank"]
                    for value in values
                    if value.get("retrieval_intent") and value.get("intent_rank") is not None
                },
                "raw_scores": {
                    value["retrieval_intent"]: value["raw_rerank_score"]
                    for value in values
                    if value.get("retrieval_intent") and value.get("raw_rerank_score") is not None
                },
            })
        subject = epoch.get("subject_ref") or {}
        queries = epoch.get("queries") or []
        epochs.append({
            "epoch_id": epoch["epoch_id"],
            "status": epoch["status"],
            "query_id": queries[0].get("query_id") if queries else None,
            "subject_ref": {
                "entity_id": subject.get("entity_id") or subject.get("id"),
                "revision": subject["revision"],
            } if subject else None,
            "predicate_iri": queries[0].get("predicate_iri") if queries else None,
            "plan_id": epoch.get("plan_id"),
            "requested_mode": (service.get("policy") or {}).get("mode", "deterministic"),
            "actual_mode": epoch.get("actual_ranking_mode", "deterministic"),
            "degraded": bool(epoch.get("degraded", False)),
            "reason": epoch.get("reason") or None,
            "budget_accounted": (epoch.get("costs") or {}).get("budget_accounted", True),
            "records": records,
        })
    costs = service.get("costs") or {}
    observations = service.get("model_observations") or []
    inference_observations = [
        item for item in observations if item.get("operation") in {"embed", "score_pairs"}
    ]
    measured = [item for item in inference_observations if item.get("input_tokens") is not None]
    return {
        "requested_mode": (service.get("policy") or {}).get("mode", "deterministic"),
        "budget_enabled": service.get("budget_enabled", True),
        "actual_modes": sorted({
            epoch["actual_mode"] for epoch in epochs if epoch["status"] == "committed"
        }),
        "degraded": any(epoch["degraded"] for epoch in epochs),
        "paused": any(epoch["status"] == "paused" for epoch in epochs),
        "reasons": sorted({epoch["reason"] for epoch in epochs if epoch["reason"]}),
        "committed_epochs": sum(epoch["status"] == "committed" for epoch in epochs),
        "epochs": epochs,
        "cost": {
            "model_calls": costs.get("model_calls", 0),
            "observed_requests": len(inference_observations),
            "input_pairs": costs.get("input_pairs", 0),
            "input_tokens": costs.get("tokens", 0),
            "reserved_input_tokens": costs.get("tokens", 0),
            "measured_input_tokens": sum(item["input_tokens"] for item in measured),
            "unknown_request_count": max(0, costs.get("model_calls", 0) - len(measured)),
            "queue_seconds": sum(item.get("queue_seconds") or 0 for item in observations),
            "retries": costs.get("technical_retries", 0),
            "elapsed_seconds": costs.get("elapsed_ms", 0) / 1000,
        },
    }


def _generic_ref(value) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"id": value.id, "revision": value.revision}


def _entity_ref(value) -> dict[str, Any]:
    return {"entity_id": value.id, "revision": value.revision}


def _deduplicate_anchors(values: Iterable[EvidenceAnchor]) -> list[EvidenceAnchor]:
    result: list[EvidenceAnchor] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def build_selection_registry(
    *,
    recognition_run_id: str,
    analysis_id: str,
    graph: GraphSnapshot,
    index: RecordIndex,
) -> dict[str, dict[str, Any]]:
    """Register every graph citation as an opaque, run-owned source selection."""

    record_by_evidence: dict[str, tuple[str, str]] = {}
    for record, view in zip(index.records, index.record_views, strict=True):
        for unit in [
            *record.source_units,
            *record.header_units,
            *record.note_units,
            *record.parent_units,
        ]:
            record_by_evidence.setdefault(unit.evidence_id, (record.record_id, view.record_view_id))

    registry: dict[str, dict[str, Any]] = {}

    def add(anchor: EvidenceAnchor, role: str) -> str:
        unit = index.ir.unit(anchor.evidence_id)
        source_record_ref, record_view_ref = record_by_evidence.get(
            anchor.evidence_id, (None, None)
        )
        identity = {
            "recognition_run_id": recognition_run_id,
            "analysis_id": analysis_id,
            "role": role,
            "anchor": anchor.model_dump(mode="json"),
        }
        selection_ref = stable_id("selection", identity)
        span_ref = stable_id(
            "span",
            [
                anchor.evidence_id,
                anchor.span_start,
                anchor.span_end,
                anchor.structure_hash,
            ],
        )
        registry[selection_ref] = {
            "selection_ref": selection_ref,
            "section_node_id": anchor.section_node_id,
            "source_record_ref": source_record_ref,
            "record_view_ref": record_view_ref,
            "source_cell_id": unit.source_cell_id,
            "span_refs": [span_ref],
            "selection_role": role,
            "anchors": [anchor.model_dump(mode="json")],
        }
        return selection_ref

    for node in graph.nodes:
        for anchor in node.evidence_refs:
            add(anchor, "entity")
    for item in graph.properties:
        role_anchors = _property_role_anchors(item)
        for role, anchors in role_anchors.items():
            for anchor in anchors:
                add(anchor, role)
    for item in graph.edges:
        role_anchors = _edge_role_anchors(item)
        for role, anchors in role_anchors.items():
            for anchor in anchors:
                add(anchor, role)
    return registry


def _refs_for(
    registry: dict[str, dict[str, Any]], anchors: Iterable[EvidenceAnchor], role: str
) -> list[str]:
    wanted = _deduplicate_anchors(anchors)
    result = []
    for selection_ref, selection in registry.items():
        if selection["selection_role"] != role:
            continue
        registered = EvidenceAnchor.model_validate(selection["anchors"][0])
        if registered in wanted:
            result.append(selection_ref)
    return result


def _property_role_anchors(item: GraphProperty) -> dict[str, list[EvidenceAnchor]]:
    predicate = item.predicate_evidence_refs
    subject = item.subject_evidence_refs
    value = item.value_evidence_refs
    if not any((predicate, subject, value)):
        # Older/injected adapters only supplied a flattened citation list. Keep
        # it traceable but do not invent a subject/predicate distinction.
        value = item.evidence_refs
    return {
        "subject": subject,
        "object": [],
        "value": value,
        "predicate_bridge": predicate,
        "condition": item.condition_evidence_refs,
        "counterevidence": item.counterevidence_refs,
    }


def _edge_role_anchors(item: GraphEdge) -> dict[str, list[EvidenceAnchor]]:
    predicate = item.predicate_evidence_refs
    subject = item.subject_evidence_refs
    object_refs = item.object_evidence_refs
    if not any((predicate, subject, object_refs)):
        # Flattened legacy proposals remain visible as endpoint evidence only.
        object_refs = item.evidence_refs
    return {
        "subject": subject,
        "object": object_refs,
        "value": [],
        "predicate_bridge": predicate,
        "condition": item.condition_evidence_refs,
        "counterevidence": item.counterevidence_refs,
    }


def _selection_roles(
    registry: dict[str, dict[str, Any]], roles: dict[str, list[EvidenceAnchor]]
) -> dict[str, list[str]]:
    return {role: _refs_for(registry, anchors, role) for role, anchors in roles.items()}


def _entity(item: GraphNode, registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    identity_state = {
        "verified": "verified_external",
    }.get(item.identity_status, item.identity_status)
    return {
        "entity_id": item.entity_id,
        "revision": item.revision,
        "class_iri": item.class_iri,
        "class_label": item.class_label,
        "label": item.label,
        "seed_origin": "user_selected" if item.root else "recognized",
        "identity_state": identity_state,
        "independent_review": item.independent_review,
        "source_selection_refs": _refs_for(registry, item.evidence_refs, "entity"),
    }


def _property(
    item: GraphProperty,
    registry: dict[str, dict[str, Any]],
    invalidated: set[str],
) -> dict[str, Any]:
    return {
        "candidate_id": item.candidate_id,
        "revision": item.revision,
        "subject_ref": _entity_ref(item.subject_ref),
        "predicate_iri": item.predicate_iri,
        "predicate_label": item.predicate_label,
        "direction": "subject_to_value",
        "raw_value": item.raw_value,
        "normalized_value": item.normalized_value,
        "polarity": item.polarity,
        "conditions": [{"text": value} for value in item.conditions],
        "applicability": item.applicability,
        "structural_valid": item.structural_valid,
        "model_supported": item.model_supported,
        "policy_eligible": item.policy_eligible,
        "independent_review": item.independent_review,
        "proof_ref": _generic_ref(item.proof_ref),
        "decision_refs": [_generic_ref(value) for value in item.decision_refs],
        "dependency_refs": [_generic_ref(value) for value in item.dependency_refs],
        "invalidated": f"{item.candidate_id}@{item.revision}" in invalidated,
        "source_selection_refs": _selection_roles(registry, _property_role_anchors(item)),
        "reason_code": item.reason_code or None,
        "reason": item.reason or None,
        "datatype_iri": None,
        "unit": None,
    }


def _relationship(
    item: GraphEdge,
    registry: dict[str, dict[str, Any]],
    invalidated: set[str],
) -> dict[str, Any]:
    return {
        "candidate_id": item.candidate_id,
        "revision": item.revision,
        "subject_ref": _entity_ref(item.subject_ref),
        "object_ref": _entity_ref(item.object_ref),
        "predicate_iri": item.predicate_iri,
        "predicate_label": item.predicate_label,
        "direction": ("subject_to_object" if item.direction == "outbound" else "object_to_subject"),
        "polarity": item.polarity,
        "conditions": [{"text": value} for value in item.conditions],
        "applicability": item.applicability,
        "structural_valid": item.structural_valid,
        "model_supported": item.model_supported,
        "policy_eligible": item.policy_eligible,
        "independent_review": item.independent_review,
        "proof_ref": _generic_ref(item.proof_ref),
        "decision_refs": [_generic_ref(value) for value in item.decision_refs],
        "dependency_refs": [_generic_ref(value) for value in item.dependency_refs],
        "invalidated": f"{item.candidate_id}@{item.revision}" in invalidated,
        "source_selection_refs": _selection_roles(registry, _edge_role_anchors(item)),
        "reason_code": item.reason_code or None,
        "reason": item.reason or None,
    }


def public_graph_payload(
    *,
    recognition_run_id: str,
    run_revision: int,
    event_head: int,
    artifact_revision: int,
    availability: str,
    projection: str,
    stored_payload: dict[str, Any],
) -> dict[str, Any]:
    """Filter one stored all-candidate snapshot without causing side effects."""

    internal_projection = PUBLIC_TO_INTERNAL_PROJECTION[projection]
    base = GraphSnapshot.model_validate(stored_payload["graph"])
    dependency_index = DependencyIndex.from_snapshot(stored_payload.get("dependency_index"))
    graph = project_graph(
        recognition_run_id=recognition_run_id,
        run_revision=run_revision,
        event_head=event_head,
        metadata_snapshot_id=base.metadata_snapshot_id,
        root_ref=base.root_ref,
        nodes=base.nodes,
        edges=base.edges,
        properties=base.properties,
        coverage=base.coverage,
        progress=base.progress,
        dependency_index=dependency_index,
        projection=internal_projection,
        artifact_status=base.artifact_status,
    )
    if projection == "effective_affirmed":
        graph.edges = [
            item
            for item in graph.edges
            if item.structural_valid
            and item.model_supported
            and item.policy_eligible
            and item.independent_review != "rejected"
        ]
        graph.properties = [
            item
            for item in graph.properties
            if item.structural_valid
            and item.model_supported
            and item.policy_eligible
            and item.independent_review != "rejected"
        ]
        reached = {graph.root_ref.id}
        changed = True
        while changed:
            changed = False
            for edge in graph.edges:
                if edge.subject_ref.id in reached and edge.object_ref.id not in reached:
                    reached.add(edge.object_ref.id)
                    changed = True
        graph.edges = [
            item
            for item in graph.edges
            if item.subject_ref.id in reached and item.object_ref.id in reached
        ]
        graph.properties = [item for item in graph.properties if item.subject_ref.id in reached]
        graph.nodes = [item for item in graph.nodes if item.entity_id in reached]
    registry = dict(stored_payload.get("selection_registry") or {})
    invalidated = set(dependency_index.invalidated)
    generated_at = stored_payload.get("generated_at") or datetime.now(UTC).isoformat()
    coverage_subjects = [
        {
            "subject_ref": _entity_ref(item.subject_ref),
            "predicate_iri": item.predicate_iri,
            "predicate_label": item.predicate_label,
            "records_planned": item.phase1 + item.phase2,
            "records_examined": item.examined,
            "records_incomplete": item.incomplete,
            "records_unattempted": item.unattempted,
            "phase_counts": {"phase1": item.phase1, "phase2": item.phase2},
            "executed_phase_counts": (
                item.executed_phase_counts
                if "executed_phase_counts" in item.model_fields_set
                else None
            ),
            "pending_frontiers": getattr(item, "pending_frontiers", 0),
            "stop_reason": getattr(item, "stop_reason", None),
        }
        for item in graph.coverage
    ]
    progress = graph.progress
    return {
        "contract_version": "document-analysis-runs-v1",
        "recognition_run_id": recognition_run_id,
        "run_revision": run_revision,
        "event_head": event_head,
        "artifact_revision": artifact_revision,
        "availability": availability,
        "projection": projection,
        "graph_snapshot": {
            "snapshot_id": stored_payload["snapshot_id"],
            "analysis_id": stored_payload["analysis_id"],
            "metadata_snapshot_id": base.metadata_snapshot_id,
            "ontology_snapshot_id": stored_payload["ontology_snapshot_id"],
            "root_ref": _entity_ref(base.root_ref),
            "projection_policy": base.projection_policy,
            "generated_at": generated_at,
        },
        "entities": [_entity(item, registry) for item in graph.nodes],
        "properties": [_property(item, registry, invalidated) for item in graph.properties],
        "relationships": [_relationship(item, registry, invalidated) for item in graph.edges],
        "invalidated_refs": _versioned_refs(invalidated),
        "ranking": public_ranking_payload(stored_payload.get("ranking_state") or {}),
        "coverage": {
            "subjects": coverage_subjects,
            "records_planned": progress.records_planned,
            "records_examined": progress.records_examined,
            "records_incomplete": progress.records_incomplete,
            "records_unattempted": progress.records_unattempted,
            "phase2_started": progress.phase_counts.get("phase2", 0) > 0,
            "pending_frontiers": progress.pending_frontiers,
            "stop_reason": progress.stop_reason,
        },
        "unresolved": {
            "unsupported": progress.unsupported,
            "undetermined": progress.undetermined,
            "not_checked": max(
                0,
                progress.tasks_attempted
                - progress.supported
                - progress.unsupported
                - progress.undetermined,
            ),
            "unassociated_entities": sum(
                item.decision_status in {"undetermined", "not_checked"}
                for item in base.nodes
                if not item.root
            ),
        },
    }


def _versioned_refs(values: set[str]) -> list[dict[str, Any]]:
    refs = []
    for value in sorted(values):
        identifier, separator, revision = value.rpartition("@")
        if separator and identifier and revision.isdigit() and int(revision) >= 1:
            refs.append({"id": identifier, "revision": int(revision)})
    return refs
