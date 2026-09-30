"""Production area identifiers survive TTL -> isolated World -> frozen cards."""

from pathlib import Path

from rdflib import OWL, RDF, RDFS, XSD, Graph, Namespace

from app.services.document_harness.ontology import freeze_catalog
from app.services.document_harness.ranking import reading_card
from app.services.ontology_engine import OntologyEngine

FAC = Namespace("https://ontology.pharma-gmp.cn/slpra/facility/")
EQ = Namespace("https://ontology.pharma-gmp.cn/slpra/equipment/")
DEV = Namespace("https://ontology.pharma-gmp.cn/slpra/drug-development/")
INTEG = Namespace("https://ontology.pharma-gmp.cn/slpra/integration/")


def test_area_identifier_is_scoped_string_and_not_a_room_or_equipment_identifier(tmp_path):
    directory = Path(__file__).resolve().parents[2] / "ontology/slpra"
    graph = Graph()
    for file in directory.glob("*.ttl"):
        graph.parse(file)
    assert (FAC.areaIdentifier, RDFS.domain, FAC.ProductionArea) in graph
    assert (FAC.areaIdentifier, RDFS.range, XSD.string) in graph
    assert bool(graph.value(FAC.areaIdentifier, INTEG.identityKey).toPython())
    assert (FAC.areaIdentifier, RDF.type, OWL.FunctionalProperty) not in graph
    assert not list(graph.objects(FAC.ProductionArea, OWL.hasKey))
    engine = OntologyEngine(ontology_dir=directory, store_path=tmp_path / "world.sqlite3")
    try:
        engine.load()
        cards = freeze_catalog(engine, str(DEV.CMCReport))
    finally:
        engine.close()
    applicable = {FAC.ProductionArea, *graph.subjects(RDFS.subClassOf, FAC.ProductionArea)}
    for iri in applicable:
        card = reading_card(
            cards.classes[str(iri)], annotation_contracts=cards.annotation_contracts,
        )
        prop = next(p for p in card["identity_properties"] if p["iri"] == str(FAC.areaIdentifier))
        assert prop["identity_key"] and prop["datatype_iris"] == [str(XSD.string)]
        assert "范围不明时不声明全局唯一" in prop["description"]
        assert any(a["iri"] == str(INTEG.identityKey) for a in card["annotation_contracts"])
    for iri in (FAC.ProductionRoom, EQ.Equipment):
        assert str(FAC.areaIdentifier) not in {p.iri for p in cards.classes[str(iri)].properties}
