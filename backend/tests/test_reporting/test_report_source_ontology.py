"""T-Box contract for risk-report source-document provenance."""

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

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
