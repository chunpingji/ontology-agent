"""Unresolved source fields stay visible without becoming graph facts."""

from copy import deepcopy

import pytest
from docx import Document
from sqlalchemy import delete

from app.api import document_analysis
from app.models.document_analysis import DocumentRunCurrentState
from app.schemas.attribute_calibration import AttributeCalibrationCandidate
from app.schemas.attribute_value import ParsedAttributeValue
from app.schemas.document_analysis import GraphArtifactResponse
from app.services.document_analysis import current_state
from app.services.document_analysis.public_projection import (
    build_selection_registry,
    public_graph_payload,
)
from app.services.extraction.ontology_guided.contracts import (
    GraphNode,
    GraphSnapshot,
    OntologySnapshot,
    VersionedRef,
)
from app.services.extraction.ontology_guided.projection import TOOL_PROJECTION_POLICY
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.word_analysis import analyze_word_core

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


@pytest.fixture
def candidate_graph(tmp_path):
    document = Document()
    document.add_paragraph("时间：2026年02月")
    path = tmp_path / "candidate.docx"
    document.save(path)
    index = RecordIndex(analyze_word_core(path).ir)
    unit = index.ir.evidence_units[0]
    candidate = AttributeCalibrationCandidate(
        candidate_id="date-candidate", record_id=index.records[0].record_id,
        field_id="date-field", field_label="时间", raw_value="2026年02月",
        label_refs=[index.ir.anchor(unit.evidence_id, 0, 2)],
        value_refs=[index.ir.anchor(unit.evidence_id, 3, len(unit.text))],
        parsed_value=ParsedAttributeValue(
            value="2026-02", datatype_iri="http://www.w3.org/2001/XMLSchema#gYearMonth",
            precision="month",
        ),
        reason_codes=["attribute_subject_missing"],
        checks={"binding": "not_checked", "metric": "not_checked", "shacl": "not_checked"},
    )
    graph = GraphSnapshot(
        recognition_run_id="a4f9885d-1b70-4718-9a97-2eb03c3ef8c4", run_revision=1,
        event_head=1, root_ref=VersionedRef(id="root", revision=1), artifact_status="partial",
        metadata_snapshot_id="metadata",
        nodes=[], attribute_candidates=[candidate], projection_policy=TOOL_PROJECTION_POLICY,
        generated_from_hash="a" * 64,
    )
    return index, graph


def stored_graph(index, graph):
    return {
        "snapshot_id": "candidate-graph", "analysis_id": index.ir.analysis_id,
        "ontology_snapshot_id": "ontology", "graph": graph.model_dump(mode="json"),
        "selection_registry": build_selection_registry(
            recognition_run_id=graph.recognition_run_id, analysis_id=index.ir.analysis_id,
            graph=graph, index=index,
        ),
    }


@pytest.mark.parametrize("projection", ["verified", "effective_affirmed", "all_candidates"])
def test_candidate_only_graph_keeps_parsed_value_and_exact_sources(candidate_graph, projection):
    index, graph = candidate_graph
    graph.attribute_candidates.append(graph.attribute_candidates[0].model_copy(
        update={"candidate_id": "resolved", "status": "resolved"},
    ))
    stored = stored_graph(index, graph)
    public = GraphArtifactResponse.model_validate(public_graph_payload(
        recognition_run_id=graph.recognition_run_id, run_revision=1, event_head=1,
        artifact_revision=1, availability="partial", projection=projection,
        stored_payload=stored, extraction_protocol="ontology-tool-extraction-v1",
    ))
    assert public.entities == public.properties == public.relationships == []
    assert len(public.attribute_candidates) == 1
    candidate = public.attribute_candidates[0]
    assert candidate.parsed_value.value == "2026-02"
    assert candidate.options == [] and candidate.checks["shacl"] == "not_checked"
    dumped = candidate.model_dump(mode="json")
    assert "label_refs" not in dumped and "value_refs" not in dumped
    for refs, expected in [(candidate.source_selection_refs.label, "时间"),
                           (candidate.source_selection_refs.value, "2026年02月")]:
        assert len(refs) == 1
        anchor = stored["selection_registry"][refs[0]]["anchors"][0]
        unit = index.ir.unit(anchor["evidence_id"])
        assert unit.text[anchor["span_start"]:anchor["span_end"]] == expected


def test_pending_artifact_cannot_expose_uncommitted_candidate(candidate_graph):
    index, graph = candidate_graph
    payload = public_graph_payload(
        recognition_run_id=graph.recognition_run_id, run_revision=1, event_head=1,
        artifact_revision=1, availability="partial", projection="verified",
        stored_payload=stored_graph(index, graph),
        extraction_protocol="ontology-tool-extraction-v1",
    )
    payload.update(availability="pending", graph_snapshot=None)
    with pytest.raises(ValueError, match="pending graph cannot expose"):
        GraphArtifactResponse.model_validate(payload)


def test_narrative_candidate_projects_mapping_option_without_a_field_identity(candidate_graph):
    index, graph = candidate_graph
    graph.nodes = [GraphNode(
        entity_id="root", revision=1, class_iri="urn:Report", label="报告甲",
        class_label="报告", root=True, grounding_kind="document_root",
    )]
    candidate = graph.attribute_candidates[0]
    candidate.field_id = None
    candidate.source_claim_id = "claim:production-date"
    candidate.status = "rejected_mapping"
    candidate.reason_codes = ["semantic_not_supported"]
    candidate.options = [{
        "subject_ref": {"id": "root", "revision": 1}, "class_iri": "urn:Report",
        "predicate_iri": "urn:plannedProductionDate",
    }]
    stored = stored_graph(index, graph)
    stored["predicate_menus"] = {"root": [{
        "predicate_iri": "urn:plannedProductionDate", "predicate_label": "计划生产日期",
    }]}
    public = GraphArtifactResponse.model_validate(public_graph_payload(
        recognition_run_id=graph.recognition_run_id, run_revision=1, event_head=1,
        artifact_revision=1, availability="partial", projection="verified",
        stored_payload=stored, extraction_protocol="ontology-tool-extraction-v1",
    ))
    assert public.properties == []
    projected = public.attribute_candidates[0]
    assert projected.field_id is None
    assert projected.source_claim_id == "claim:production-date"
    assert projected.status == "rejected_mapping"
    assert projected.options[0].subject_ref.model_dump() == {"id": "root", "revision": 1}
    assert projected.options[0].class_iri == "urn:Report"
    assert projected.options[0].subject_label == "报告甲"
    assert projected.options[0].predicate_label == "计划生产日期"


def test_current_candidate_cache_rebuild_and_api_source_isolation(
    candidate_graph, current_run, client, db, monkeypatch,
):
    index, graph = candidate_graph
    store, run, token = current_run
    graph.recognition_run_id = str(run.recognition_run_id)
    run.metadata_snapshot_id = graph.metadata_snapshot_id
    ontology = OntologySnapshot(snapshot_id="ontology", ontology_hash="ontology", classes={})
    for kind, payload in {
        "source": {"performance_policy": {
            "state_storage_version": 4, "extraction_protocol": "ontology-tool-extraction-v1",
        }},
        "structure": {"analysis": index.ir.model_dump(mode="json")},
        "source_header": {key: getattr(index.ir, key)
                          for key in ("analysis_id", "document_hash", "structure_hash")},
        "ontology_snapshot": ontology.model_dump(mode="json"),
    }.items():
        store.update_artifact(
            run.recognition_run_id, run.owner_id, token, artifact_kind=kind,
            expected_revision=0, artifact_hash=current_state.content_hash(payload),
            status="ready", artifact_id="candidate-test-" + kind, payload=payload,
        )
    authority = {"key": "field-task", "position": 0, "value": {
        "attribute_candidates": [graph.attribute_candidates[0].model_dump(mode="json")],
    }}
    current_state.put_rows(store, run, DocumentRunCurrentState, "work:record_discovery",
                           {"field-task": authority}, work_version=run.work_version)
    snapshot, prepared = current_state.display_payload(
        store, run, ontology=ontology, ir=index.ir, metadata=None, index=index, graph=graph,
        dependencies={}, summary={},
    )
    current_state.publish_display(store, run, prepared, snapshot)
    db.commit()
    header = current_state.get_row(store, run, "work:projection")
    assert "attribute_candidates" not in header["graph"]
    headers = {"X-User": run.owner_id, "X-Role": "senior_analyst"}
    url = f"/api/document-analysis/runs/{run.recognition_run_id}"
    calls = []
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_a, **_kw: calls.append(1))
    first = client.get(url + "/graph?projection=verified", headers=headers)
    assert first.status_code == 200, first.text
    candidate = first.json()["attribute_candidates"][0]
    assert first.json()["properties"] == []
    assert client.get(url + "/graph", headers={
        "X-User": "other-owner", "X-Role": "senior_analyst",
    }).status_code == 404
    graph.attribute_candidates[0].status = "rejected_mapping"
    graph.attribute_candidates[0].reason_codes = ["datatype_mismatch"]
    authority["value"]["attribute_candidates"] = [
        graph.attribute_candidates[0].model_dump(mode="json"),
    ]
    current_state.put_rows(store, run, DocumentRunCurrentState, "work:record_discovery",
                           {"field-task": authority}, work_version=run.work_version)
    snapshot, prepared = current_state.display_payload(
        store, run, ontology=ontology, ir=index.ir, metadata=None, index=index, graph=graph,
        dependencies={}, summary={}, changes={"record_discovery": {"field-task": authority}},
    )
    current_state.publish_display(store, run, prepared, snapshot)
    db.commit()
    candidate = client.get(url + "/graph?projection=verified", headers=headers).json()[
        "attribute_candidates"
    ][0]
    assert candidate["status"] == "rejected_mapping"
    assert candidate["reason_codes"] == ["datatype_mismatch"]
    before = deepcopy(current_state.get_row(store, run, "work:record_discovery", "field-task"))
    db.execute(delete(DocumentRunCurrentState).where(
        DocumentRunCurrentState.recognition_run_id == run.recognition_run_id,
        DocumentRunCurrentState.domain.in_(["display:attribute_candidates", "display:selections"]),
    ))
    db.commit()
    rebuilt = client.get(url + "/graph?projection=verified", headers=headers)
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["attribute_candidates"] == [candidate]
    source_ref = candidate["source_selection_refs"]["value"][0]
    source = client.get(url + "/source-selection", headers=headers,
                        params={"selection_ref": source_ref})
    assert source.status_code == 200, source.text
    assert source.json()["anchors"][0]["span_start"] == 3
    assert client.get(url + "/source-selection", params={"selection_ref": source_ref}, headers={
        "X-User": "other-owner", "X-Role": "senior_analyst",
    }).status_code == 404
    assert current_state.get_row(store, run, "work:record_discovery", "field-task") == before
    assert calls == []
