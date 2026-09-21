"""The target graph is an authenticated read, including before model execution."""

from copy import deepcopy

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.services.document_analysis.application import DocumentAnalysisApplication
from tests.test_api.test_document_analysis import _create, _word_bytes


def freeze_phase(db, run_id, phase):
    run = db.get(DocumentAnalysisRun, run_id)
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    policy = source.payload["performance_policy"]
    source.payload = {**source.payload, "performance_policy": {
        **policy, "record_discovery": {**policy["record_discovery"], "graph_phase": phase},
    }}
    db.commit()


def test_target_graph_get_is_owner_scoped_cached_and_does_not_start_work(
    client, db, analyst_headers, operator_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    dispatches = []
    monkeypatch.setattr(
        document_analysis, "dispatch_run", lambda *args, **kwargs: dispatches.append(args),
    )
    created = _create(client, analyst_headers, _word_bytes(tmp_path), request_key="target-graph")
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    freeze_phase(db, run_id, "candidate_graph")
    dispatches.clear()
    run = db.get(DocumentAnalysisRun, run_id)
    watermark = (run.revision, run.event_head, run.artifact_revision)
    url = f"/api/document-analysis/runs/{run_id}/target-graph"
    response = client.get(url, headers=analyst_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["phase"] == "candidate_graph"
    assert result["graph"]["projection"] == "all_candidates"
    assert result["root"]["class_iri"] == run.root_class_iri
    assert result["targets"] and result["summary"]["relationships"]["total"] > 0
    assert result["graph"]["entities"] == []
    assert result["ontology_snapshot_id"]
    assert all(target["kind"] == "relationship" for target in result["targets"])
    assert result["summary"]["relationships"]["percent"] is None
    assert result["summary"]["properties"]["total"] == 0
    cached = client.get(url, headers={**analyst_headers, "If-None-Match": response.headers["ETag"]})
    assert cached.status_code == 304
    assert client.get(url, headers=operator_headers).status_code == 404
    assert client.get(url).status_code == 401
    assert dispatches == []
    db.refresh(run)
    assert watermark == (run.revision, run.event_head, run.artifact_revision)


def test_candidate_target_api_keeps_unverified_edges_and_endpoint_source_refs(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    dispatches = []
    monkeypatch.setattr(
        document_analysis, "dispatch_run", lambda *args, **kwargs: dispatches.append(args),
    )
    created = _create(client, analyst_headers, _word_bytes(tmp_path), request_key="candidate-view")
    run_id = created.json()["recognition_run_id"]
    freeze_phase(db, run_id, "candidate_graph")
    url = f"/api/document-analysis/runs/{run_id}/target-graph"
    initial = client.get(url, headers=analyst_headers).json()
    target = next(item for item in initial["targets"] if item["range_types"])
    root_ref = {key: initial["root"][key] for key in ("entity_id", "revision")}
    object_ref = {"entity_id": "isolated-candidate", "revision": 1}
    original = DocumentAnalysisApplication.graph_response

    def with_candidate(self, run, **kwargs):
        graph = original(self, run, **kwargs)
        graph.update(availability="partial", graph_snapshot={
            "snapshot_id": "candidate-view", "analysis_id": "analysis",
            "metadata_snapshot_id": "metadata",
            "ontology_snapshot_id": initial["ontology_snapshot_id"],
            "root_ref": root_ref, "projection_policy": "candidate-test",
            "generated_at": "2026-09-21T00:00:00Z",
        })
        graph["entities"] = [{
            **object_ref, "class_iri": target["range_types"][0]["iri"],
            "class_label": target["range_types"][0]["label"], "label": "候选对象",
            "source_selection_refs": ["source:endpoint"],
        }]
        graph["relationships"] = [{
            "candidate_id": "document-candidate", "revision": 1,
            "subject_ref": root_ref, "object_ref": object_ref,
            "predicate_iri": target["predicate_iri"], "predicate_label": target["predicate_label"],
            "structural_valid": True, "model_supported": False, "policy_eligible": False,
            "source_selection_refs": {"predicate_bridge": ["source:relationship"]},
        }]
        return graph

    monkeypatch.setattr(DocumentAnalysisApplication, "graph_response", with_candidate)
    dispatches.clear()
    run = db.get(DocumentAnalysisRun, run_id)
    watermark = (run.revision, run.event_head, run.artifact_revision)
    response = client.get(url, headers=analyst_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["phase"] == "candidate_graph"
    edge = result["graph"]["relationships"][0]
    assert edge["proof_ref"] is None and not edge["policy_eligible"]
    assert edge["source_selection_refs"]["predicate_bridge"] == ["source:relationship"]
    assert result["graph"]["entities"][0]["source_selection_refs"] == ["source:endpoint"]
    projected = next(item for item in result["targets"] if item["assertion_refs"])
    assert projected["assertion_refs"] == [{"id": "document-candidate", "revision": 1}]
    assert projected["supported_assertion_refs"] == [] and not projected["completed"]
    assert result["summary"]["relationships"]["percent"] is None
    assert dispatches == []
    db.refresh(run)
    assert watermark == (run.revision, run.event_head, run.artifact_revision)


def test_evidence_review_api_uses_frozen_phase_and_retains_accepted_property_with_warning(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "artifacts")
    dispatches = []
    monkeypatch.setattr(
        document_analysis, "dispatch_run", lambda *args, **kwargs: dispatches.append(args),
    )
    created = _create(client, analyst_headers, _word_bytes(tmp_path), request_key="evidence-view")
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    freeze_phase(db, run_id, "evidence_review")
    # The report root has no business-valued property in the fixture ontology.
    # Give this isolated frozen test input one slot to exercise public value fields.
    run = db.get(DocumentAnalysisRun, run_id)
    frozen = db.get(
        DocumentAnalysisArtifact, run.artifact_manifest["ontology_snapshot"]["artifact_id"],
    )
    ontology_payload = deepcopy(frozen.payload)
    ontology_payload["classes"][run.root_class_iri]["declared_properties"].append({
        "iri": "https://example.org/reviewQuantity", "label": "原文量值",
    })
    frozen.payload = ontology_payload
    db.commit()
    url = f"/api/document-analysis/runs/{run_id}/target-graph"
    response = client.get(url, headers=analyst_headers)
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["phase"] == "evidence_review"
    target = next(item for item in initial["targets"] if item["kind"] == "property")
    root_ref = {key: initial["root"][key] for key in ("entity_id", "revision")}
    original = DocumentAnalysisApplication.graph_response

    def with_property(self, run, **kwargs):
        graph = original(self, run, **kwargs)
        graph.update(availability="partial", graph_snapshot={
            "snapshot_id": "review-view", "analysis_id": "analysis",
            "metadata_snapshot_id": "metadata",
            "ontology_snapshot_id": initial["ontology_snapshot_id"],
            "root_ref": root_ref, "projection_policy": "evidence-review-test",
            "generated_at": "2026-09-21T00:00:00Z",
        }, properties=[{
            "candidate_id": "raw-property", "revision": 1, "subject_ref": root_ref,
            "predicate_iri": target["predicate_iri"], "predicate_label": target["predicate_label"],
            "raw_value": "5", "raw_unit": "g", "normalized_value": 5000, "unit": "mg",
            "normalization_available": True, "decision_status": "supported",
            "structural_valid": True, "model_supported": True, "policy_eligible": False,
            "proof_ref": {"id": "proof", "revision": 1},
            "decision_refs": [{"id": "decision", "revision": 1}],
            "source_selection_refs": {"value": ["source:raw-value"]},
            "validation_diagnostics": [{
                "check": "shacl", "status": "failed", "reason_codes": ["range_exceeded"],
                "message": "原文值超过约束范围；保留原文证据采信。",
            }],
        }])
        return graph

    monkeypatch.setattr(DocumentAnalysisApplication, "graph_response", with_property)
    dispatches.clear()
    run = db.get(DocumentAnalysisRun, run_id)
    watermark = (run.revision, run.event_head, run.artifact_revision)
    response = client.get(url, headers=analyst_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["phase"] == "evidence_review"
    assert result["summary"]["properties"]["total"] == initial["summary"]["properties"]["total"]
    assert result["summary"]["properties"]["supported"] == 1
    projected = next(item for item in result["targets"] if item["assertion_refs"])
    assert projected["supported_assertion_refs"] == [{"id": "raw-property", "revision": 1}]
    prop = result["graph"]["properties"][0]
    assert prop["raw_value"] == "5" and prop["raw_unit"] == "g"
    assert prop["normalized_value"] == 5000 and prop["unit"] == "mg"
    assert prop["validation_diagnostics"][0]["status"] == "failed"
    assert prop["source_selection_refs"]["value"] == ["source:raw-value"]
    assert dispatches == []
    db.refresh(run)
    assert watermark == (run.revision, run.event_head, run.artifact_revision)
