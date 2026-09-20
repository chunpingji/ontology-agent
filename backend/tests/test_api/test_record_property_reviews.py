"""Record-discovered attributes retain their actual owner through human review."""

from copy import deepcopy

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
)
from app.services.document_analysis.reviews import review_operations
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.extraction.ontology_guided.contracts import GraphNode
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryTask
from tests.test_api.test_document_property_reviews import post_review

pytest_plugins = ["tests.test_api.test_document_property_reviews"]


def record_origin(db, run):
    store = DocumentAnalysisRunStore(db)
    reference = store.get_artifact(run.recognition_run_id, run.owner_id, "recognition_checkpoint")
    artifact = db.get(DocumentAnalysisArtifact, reference.artifact_id)
    checkpoint = deepcopy(artifact.payload)
    old = checkpoint["task_outcomes"][0]["task"]
    original = RecordDiscoveryTask.create(
        run_fingerprint=run.run_fingerprint, record_id=old["record_id"],
        schema_card_id="record-card",
        analysis_scope_ref="scope", dependency_hash=old["dependency_hash"],
    ).model_dump(mode="json")
    owner = GraphNode(entity_id="device", revision=3, class_iri="urn:test:Device",
                      class_label="装置", label="装置甲", root=False)
    payload = owner.model_dump(mode="json")
    db.add(DocumentRunCandidate(
        recognition_run_id=run.recognition_run_id, candidate_id=owner.entity_id,
        revision=owner.revision, kind="entity", payload=payload, payload_hash=content_hash(payload),
    ))
    db.flush()
    db.add(DocumentRunCandidateHead(
        recognition_run_id=run.recognition_run_id, candidate_id=owner.entity_id,
        revision=owner.revision, payload_hash=content_hash(payload),
    ))
    candidate = db.get(DocumentRunCandidate, (run.recognition_run_id, "appearance", 1))
    prop = deepcopy(candidate.payload)
    prop["subject_ref"] = {"id": owner.entity_id, "revision": owner.revision}
    candidate.payload, candidate.payload_hash = prop, content_hash(prop)
    head = db.get(DocumentRunCandidateHead, (run.recognition_run_id, "appearance"))
    head.payload_hash = candidate.payload_hash
    checkpoint["task_outcomes"][0] = {"task": original, "outcome": {"properties": [prop]}}
    artifact.payload = checkpoint
    artifact.content_hash = reference.content_hash = content_hash(checkpoint)
    graph_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "graph")
    graph_artifact = db.get(DocumentAnalysisArtifact, graph_ref.artifact_id)
    graph = deepcopy(graph_artifact.payload)
    graph["graph"]["nodes"].append(payload)
    graph["graph"]["properties"] = [prop]
    graph_artifact.payload = graph
    graph_artifact.content_hash = graph_ref.content_hash = content_hash(graph)
    db.commit()
    return original, graph_artifact, graph_ref


def test_review_preserves_record_origin_and_binds_actual_property_owner(
    client, db, analyst_headers, reviewed_run,
):
    original, _, _ = record_origin(db, reviewed_run)
    response = post_review(client, reviewed_run, analyst_headers)
    assert response.status_code == 201, response.text
    target = review_operations(db, reviewed_run)[0]
    assert target["original_task"] == original
    assert "subject" not in target["original_task"]
    assert target["subject_ref"] == {"id": "device", "revision": 3}
    assert target["subject"] == {
        "entity_id": "device", "revision": 3, "class_iri": "urn:test:Device",
        "is_document_root": False, "referent_id": None,
    }


def test_record_review_rejects_displayed_owner_different_from_exact_candidate(
    client, db, analyst_headers, reviewed_run,
):
    _, artifact, reference = record_origin(db, reviewed_run)
    graph = deepcopy(artifact.payload)
    next(node for node in graph["graph"]["nodes"] if node["entity_id"] == "device")[
        "class_iri"
    ] = "urn:WrongOwnerClass"
    artifact.payload = graph
    artifact.content_hash = reference.content_hash = content_hash(graph)
    db.commit()
    response = post_review(client, reviewed_run, analyst_headers)
    assert response.status_code == 409, response.text


def test_record_review_rejects_property_whose_owner_is_missing_from_current_graph(
    client, db, analyst_headers, reviewed_run,
):
    _, artifact, reference = record_origin(db, reviewed_run)
    graph = deepcopy(artifact.payload)
    graph["graph"]["nodes"] = [node for node in graph["graph"]["nodes"]
                                if node["entity_id"] != "device"]
    artifact.payload = graph
    artifact.content_hash = reference.content_hash = content_hash(graph)
    db.commit()
    response = post_review(client, reviewed_run, analyst_headers)
    assert response.status_code == 409, response.text
