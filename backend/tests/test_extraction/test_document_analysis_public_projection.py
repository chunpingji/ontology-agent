from __future__ import annotations

from docx import Document

from app.schemas.document_analysis import GraphArtifactResponse
from app.services.document_analysis.public_projection import (
    build_selection_registry,
    public_graph_payload,
)
from app.services.extraction.ontology_guided.contracts import (
    CoverageSummary,
    GraphEdge,
    GraphNode,
    RunProgress,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.projection import project_graph
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.word_analysis import analyze_word_core

ROOT = "https://ontology.example/Report"
PRODUCT = "https://ontology.example/Product"
DESCRIBES = "https://ontology.example/describes"


def test_public_projection_registers_role_specific_opaque_source_refs(tmp_path):
    document = Document()
    document.add_heading("产品", level=1)
    document.add_paragraph("本报告明确描述产品甲。")
    path = tmp_path / "graph.docx"
    document.save(path)
    analysis = analyze_word_core(path)
    index = RecordIndex(analysis.ir)
    unit = next(item for item in analysis.ir.evidence_units if "产品甲" in item.text)
    anchor = analysis.ir.anchor(unit.evidence_id, unit.text.index("产品甲"), len(unit.text) - 1)
    predicate_anchor = analysis.ir.anchor(
        unit.evidence_id, unit.text.index("描述"), unit.text.index("描述") + 2
    )
    root = GraphNode(
        entity_id="entity:root",
        revision=1,
        class_iri=ROOT,
        class_label="报告",
        label="graph.docx",
        root=True,
        root_origin="user_specified",
    )
    product = GraphNode(
        entity_id="entity:product",
        revision=1,
        class_iri=PRODUCT,
        class_label="产品",
        label="产品甲",
        evidence_refs=[anchor],
    )
    edge = GraphEdge(
        candidate_id="candidate:describes",
        revision=1,
        subject_ref=VersionedRef(id=root.entity_id, revision=1),
        object_ref=VersionedRef(id=product.entity_id, revision=1),
        predicate_iri=DESCRIBES,
        predicate_label="描述",
        decision_status="supported",
        structural_valid=True,
        model_supported=True,
        policy_eligible=True,
        proof_ref=VersionedRef(id="proof:describes", revision=1),
        decision_refs=[VersionedRef(id="decision:describes", revision=1)],
        object_evidence_refs=[anchor],
        predicate_evidence_refs=[predicate_anchor],
        evidence_refs=[anchor, predicate_anchor],
    )
    progress = RunProgress(
        tasks_attempted=1,
        model_calls=1,
        records_planned=1,
        records_examined=1,
        records_incomplete=0,
        records_unattempted=0,
        phase_counts={"phase1": 1, "phase2": 0},
        supported=1,
        completion="in_scope_complete",
    )
    graph = project_graph(
        recognition_run_id="a4f9885d-1b70-4718-9a97-2eb03c3ef8c4",
        run_revision=4,
        event_head=5,
        metadata_snapshot_id="metadata:one",
        root_ref=VersionedRef(id=root.entity_id, revision=1),
        nodes=[root, product],
        edges=[edge],
        properties=[],
        coverage=[
            CoverageSummary(
                subject_ref=VersionedRef(id=root.entity_id, revision=1),
                predicate_iri=DESCRIBES,
                predicate_label="描述",
                phase1=1,
                examined=1,
            )
        ],
        progress=progress,
        projection="all",
        artifact_status="ready",
    )
    registry = build_selection_registry(
        recognition_run_id=graph.recognition_run_id,
        analysis_id=analysis.ir.analysis_id,
        graph=graph,
        index=index,
    )
    payload = public_graph_payload(
        recognition_run_id=graph.recognition_run_id,
        run_revision=7,
        event_head=8,
        artifact_revision=3,
        availability="ready",
        projection="effective_affirmed",
        stored_payload={
            "snapshot_id": "graph-snapshot:one",
            "analysis_id": analysis.ir.analysis_id,
            "ontology_snapshot_id": "ontology-snapshot:one",
            "generated_at": "2026-09-08T12:00:00Z",
            "graph": graph.model_dump(mode="json"),
            "selection_registry": registry,
        },
    )

    response = GraphArtifactResponse.model_validate(payload)
    assert len(response.relationships) == 1
    refs = response.relationships[0].source_selection_refs
    assert refs.object and refs.predicate_bridge
    selection = registry[refs.object[0]]
    assert selection["source_record_ref"]
    assert selection["record_view_ref"]
    assert selection["anchors"][0]["evidence_id"] == unit.evidence_id


def test_public_projection_replays_durable_dependency_invalidations(tmp_path):
    document = Document()
    document.add_paragraph("本报告明确描述产品甲。")
    path = tmp_path / "invalidated-graph.docx"
    document.save(path)
    analysis = analyze_word_core(path)
    root = GraphNode(
        entity_id="entity:root",
        revision=1,
        class_iri=ROOT,
        class_label="报告",
        label=path.name,
        root=True,
        root_origin="user_specified",
    )
    product = GraphNode(
        entity_id="entity:product",
        revision=1,
        class_iri=PRODUCT,
        class_label="产品",
        label="产品甲",
    )
    edge = GraphEdge(
        candidate_id="candidate:describes",
        revision=2,
        subject_ref=VersionedRef(id=root.entity_id, revision=1),
        object_ref=VersionedRef(id=product.entity_id, revision=1),
        predicate_iri=DESCRIBES,
        predicate_label="描述",
        decision_status="supported",
        structural_valid=True,
        model_supported=True,
        policy_eligible=True,
        proof_ref=VersionedRef(id="proof:describes", revision=3),
        decision_refs=[VersionedRef(id="decision:describes", revision=4)],
    )
    progress = RunProgress(completion="in_scope_complete")
    graph = project_graph(
        recognition_run_id="a4f9885d-1b70-4718-9a97-2eb03c3ef8c4",
        run_revision=4,
        event_head=5,
        metadata_snapshot_id="metadata:one",
        root_ref=VersionedRef(id=root.entity_id, revision=1),
        nodes=[root, product],
        edges=[edge],
        properties=[],
        coverage=[],
        progress=progress,
        projection="all",
        artifact_status="ready",
    )
    dependencies = DependencyIndex()
    dependencies.add_proof("proof:describes@3", ["decision:describes@4"])
    dependencies.add_proof("candidate:describes@2", ["proof:describes@3"])
    dependencies.invalidate("decision:describes@4")
    stored = {
        "snapshot_id": "graph-snapshot:invalidated",
        "analysis_id": analysis.ir.analysis_id,
        "ontology_snapshot_id": "ontology-snapshot:one",
        "generated_at": "2026-09-08T12:00:00Z",
        "graph": graph.model_dump(mode="json"),
        "selection_registry": {},
        "dependency_index": dependencies.snapshot(),
    }

    effective = GraphArtifactResponse.model_validate(
        public_graph_payload(
            recognition_run_id=graph.recognition_run_id,
            run_revision=7,
            event_head=8,
            artifact_revision=3,
            availability="ready",
            projection="effective_affirmed",
            stored_payload=stored,
        )
    )
    assert effective.relationships == []
    assert {item.id for item in effective.invalidated_refs} >= {
        "decision:describes",
        "proof:describes",
        "candidate:describes",
    }

    all_candidates = GraphArtifactResponse.model_validate(
        public_graph_payload(
            recognition_run_id=graph.recognition_run_id,
            run_revision=7,
            event_head=8,
            artifact_revision=3,
            availability="ready",
            projection="all_candidates",
            stored_payload=stored,
        )
    )
    assert len(all_candidates.relationships) == 1
    assert all_candidates.relationships[0].invalidated is True
