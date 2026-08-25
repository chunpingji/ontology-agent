"""Gap B: materialize the PRODUCT report's own declared relations as fact edges.

Contract:
  * a product-report edge (评估小组 / 审批人小组) is materialized IFF the template
    declares that predicate as ``ontology_relation`` coverage (``_declared_predicates``);
  * an undeclared template gets ``[]`` — byte-for-byte unchanged (zero blast radius);
  * each edge carries the member roster in ``object_data_properties`` so
    ``narrative_generator._format_facts`` renders it in 行文 预览 / 报告.
"""

from __future__ import annotations

from app.services.reporting.ast_template import OntologyRelationBinding, ReportTemplate
from app.services.reporting.product_report_edges import (
    APPROVER_TEAM_IRI,
    ASSESSMENT_TEAM_IRI,
    BASED_ON_SOURCE_DOCUMENT_IRI,
    CMC_REPORT_IRI,
    DOCUMENT_NAME_IRI,
    HAS_APPROVER_TEAM_IRI,
    HAS_ASSESSMENT_TEAM_IRI,
    RISK_ASSESSMENT_REPORT_IRI,
    product_report_edges_for_template,
    source_document_edge,
)

_DOC = RISK_ASSESSMENT_REPORT_IRI


def _tpl(*bindings) -> ReportTemplate:
    return ReportTemplate.model_validate({
        "template_id": "t",
        "sections": [{
            "section_id": "s",
            "title": "s",
            "coverage": [b.model_dump() for b in bindings],
            "groups": [],
        }],
    })


def _rel(pred: str, rng: str, label: str) -> OntologyRelationBinding:
    return OntologyRelationBinding(
        doc_class_iri=_DOC, predicate_iri=pred, range_class_iri=rng, label=label
    )


def test_undeclared_template_yields_no_edges():
    """No coverage → nothing materialized (zero blast radius)."""
    assert product_report_edges_for_template(None, _tpl()) == []


def test_assessment_team_declared_materializes_edge():
    tpl = _tpl(_rel(HAS_ASSESSMENT_TEAM_IRI, ASSESSMENT_TEAM_IRI, "评估小组"))
    edges = product_report_edges_for_template(None, tpl)
    assert len(edges) == 1
    edge = edges[0]
    assert edge["predicate_iri"] == HAS_ASSESSMENT_TEAM_IRI
    assert edge["object_class_iri"] == ASSESSMENT_TEAM_IRI
    # roster rides in object_data_properties → _format_facts renders it
    assert edge["object_data_properties"]
    assert all(dp["value"] for dp in edge["object_data_properties"])


def test_approver_team_declared_materializes_edge():
    tpl = _tpl(_rel(HAS_APPROVER_TEAM_IRI, APPROVER_TEAM_IRI, "审批人小组"))
    edges = product_report_edges_for_template(None, tpl)
    assert len(edges) == 1
    assert edges[0]["predicate_iri"] == HAS_APPROVER_TEAM_IRI
    assert edges[0]["object_class_iri"] == APPROVER_TEAM_IRI
    assert edges[0]["object_data_properties"]


def test_both_declared_materializes_both():
    tpl = _tpl(
        _rel(HAS_ASSESSMENT_TEAM_IRI, ASSESSMENT_TEAM_IRI, "评估小组"),
        _rel(HAS_APPROVER_TEAM_IRI, APPROVER_TEAM_IRI, "审批人小组"),
    )
    preds = {e["predicate_iri"] for e in product_report_edges_for_template(None, tpl)}
    assert preds == {HAS_ASSESSMENT_TEAM_IRI, HAS_APPROVER_TEAM_IRI}


def test_edge_shape_mirrors_extractor_keys():
    """Edge dict must carry the same keys the source-doc extractor emits so it flows
    through the fact spine with no special-casing."""
    tpl = _tpl(_rel(HAS_ASSESSMENT_TEAM_IRI, ASSESSMENT_TEAM_IRI, "评估小组"))
    edge = product_report_edges_for_template(None, tpl)[0]
    for key in (
        "subject_class_iri", "subject_text", "predicate_iri", "predicate_label",
        "object_class_iri", "object_text", "object_data_properties", "source_ref",
    ):
        assert key in edge


def test_source_document_edge_carries_typed_cmc_report_name():
    edge = source_document_edge(None, r"C:\uploads\HRS-1597 CMCReport.docx")

    assert edge is not None
    assert edge["predicate_iri"] == BASED_ON_SOURCE_DOCUMENT_IRI
    assert edge["object_class_iri"] == CMC_REPORT_IRI
    assert edge["object_text"] == "HRS-1597 CMCReport.docx"
    assert edge["object_data_properties"] == [{
        "iri": DOCUMENT_NAME_IRI,
        "label": "文档名称",
        "value": "HRS-1597 CMCReport.docx",
    }]


def test_source_document_edge_skips_blank_name():
    assert source_document_edge(None, "  ") is None
