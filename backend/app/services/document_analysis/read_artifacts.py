"""Build compact, immutable display artifacts at existing write boundaries."""

from __future__ import annotations

from app.models.document_analysis import DocumentAnalysisArtifact
from app.services.document_analysis.public_projection import public_ranking_payload
from app.services.document_analysis.run_store import content_hash
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu


def publish_read_artifacts(store, run, token, *, kind, payload, status, event_head):
    """Caller owns the transaction containing the authoritative batch and heads."""
    outputs = {}
    if kind in {"structure", "metadata"}:
        ir = payload["analysis"]
        outputs["source_header"] = {key: ir[key] for key in (
            "analysis_id", "document_hash", "structure_hash",
        )}
    if kind == "ranking_state":
        outputs["ranking_summary"] = public_ranking_payload(payload)
    if kind == "graph":
        compact = {key: payload[key] for key in (
            "snapshot_id", "analysis_id", "ontology_snapshot_id", "generated_at", "graph",
            "selection_registry",
        )}
        dependencies = payload.get("dependency_index") or {}
        compact["dependency_index"] = {
            "requirements": dependencies.get("requirements", {}),
            "invalidated": dependencies.get("invalidated", []),
        }
        ontology_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "ontology_snapshot")
        ontology_artifact = store.db.get(DocumentAnalysisArtifact, ontology_ref.artifact_id)
        ontology = OntologySnapshot.model_validate(ontology_artifact.payload)
        menus = {}
        for entity in payload["graph"]["nodes"]:
            if entity["class_iri"] not in ontology.classes:
                continue
            menu = compile_local_menu(ontology, SubjectRef(
                entity_id=entity["entity_id"], revision=entity["revision"],
                class_iri=entity["class_iri"], is_document_root=entity.get("root", False),
            ))
            menus[entity["entity_id"]] = [
                {"predicate_iri": item.iri, "predicate_label": item.label, "kind": item.kind}
                for item in [*menu.relationships, *menu.properties]
            ]
        compact["predicate_menus"] = menus
        outputs["public_graph"] = compact
        header = store.get_artifact(run.recognition_run_id, run.owner_id, "source_header")
        if header:
            outputs["source_selections"] = {
                **store.db.get(DocumentAnalysisArtifact, header.artifact_id).payload,
                "selection_registry": payload["selection_registry"],
            }
        # Empty/deterministic runs may publish a graph without a ranking hook.
        if not store.get_artifact(run.recognition_run_id, run.owner_id, "ranking_summary"):
            outputs["ranking_summary"] = public_ranking_payload(payload.get("ranking_state") or {})
    for artifact_kind, body in outputs.items():
        head = store.get_artifact_head(run.recognition_run_id, run.owner_id, artifact_kind)
        digest = content_hash(body)
        store.update_artifact(
            run.recognition_run_id, run.owner_id, token, artifact_kind=artifact_kind,
            expected_revision=head.revision if head else 0,
            artifact_hash=digest, status=status,
            artifact_id=stable_id(artifact_kind, [str(run.recognition_run_id), digest]),
            payload=body, media_type="application/json", event_head=event_head,
        )
