"""T-Box contract for risk-report source-document provenance."""

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from app.api.extraction import _risk_report_source_document
from app.models.entity_shadow import EntityShadow
from app.models.extraction import ExtractionJob

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology" / "slpra"
RISK = "https://ontology.pharma-gmp.cn/slpra/risk/"
DOCUMENT = "https://ontology.pharma-gmp.cn/slpra/document/"
DRUG_DEVELOPMENT = "https://ontology.pharma-gmp.cn/slpra/drug-development/"


def test_risk_report_has_typed_source_document_relation():
    graph = Graph()
    graph.parse(str(ONTOLOGY_DIR / "slpra-risk.ttl"), format="turtle")

    predicate = URIRef(RISK + "basedOnSourceDocument")
    assert (predicate, RDF.type, OWL.ObjectProperty) in graph
    assert (predicate, RDFS.domain, URIRef(RISK + "RiskAssessmentReport")) in graph
    assert (predicate, RDFS.range, URIRef(DOCUMENT + "RegulatoryDocument")) in graph


def test_regulatory_document_declares_document_name():
    graph = Graph()
    graph.parse(str(ONTOLOGY_DIR / "slpra-document.ttl"), format="turtle")

    document_name = URIRef(DOCUMENT + "documentName")
    assert (document_name, RDF.type, OWL.DatatypeProperty) in graph
    assert (document_name, RDFS.domain, URIRef(DOCUMENT + "RegulatoryDocument")) in graph


def test_managed_cmc_document_name_resolves_from_typed_abox_property(db):
    document_ref = "http://slpra.org/facts#cmc-1597"
    db.add(EntityShadow(
        iri=document_ref,
        class_iri="https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        label_zh="旧显示名称",
        module="document",
        properties_json={"documentName": "HRS-1597 CMCReport.docx"},
    ))
    job = ExtractionJob(
        source_type="doc_repo",
        source_filename=document_ref,
        source_config={"doc_ref": document_ref},
        status="completed",
    )
    db.add(job)
    db.commit()

    assert _risk_report_source_document(job, db) == (
        "HRS-1597 CMCReport.docx",
        document_ref,
    )


def test_report_source_resolves_name_from_materialized_cmc_document(db):
    from app.api.extraction import _risk_report_source_document
    from app.models.entity_shadow import EntityShadow
    from app.models.extraction import ExtractionJob

    document_ref = "http://slpra.org/facts#cmc-1597"
    db.add(EntityShadow(
        iri=document_ref,
        class_iri=DRUG_DEVELOPMENT + "CMCReport",
        label_zh="旧显示名称",
        module="document",
        properties_json={"documentName": "HRS-1597 CMCReport.docx"},
    ))
    job = ExtractionJob(
        source_type="doc_repo",
        source_filename="fallback.docx",
        source_config={"doc_ref": document_ref},
        status="completed",
    )
    db.add(job)
    db.commit()

    assert _risk_report_source_document(job, db) == (
        "HRS-1597 CMCReport.docx",
        document_ref,
    )
