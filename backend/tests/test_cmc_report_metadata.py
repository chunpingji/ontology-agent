"""CMC document metadata stays optional, local to CMCReport and consistent across stores."""

from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pytest
from rdflib import OWL, RDF, RDFS, XSD, Graph, Namespace

from app.config import settings
from app.models.ontology_meta import OntologyDataProperty
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import OntologySnapshot
from app.services.extraction.ontology_guided.ontology_plan import ontology_snapshot_from_engine
from app.services.extraction.ontology_guided.record_discovery import compile_record_schema_card
from app.services.ontology_engine import OntologyEngine
from app.services.ontology_meta_store import OntologyMetaStore
from app.services.ttl_merge import load_base_graph

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_DIR = ROOT / "ontology" / "slpra"
DOC = Namespace("https://ontology.pharma-gmp.cn/slpra/document/")
DEV = Namespace("https://ontology.pharma-gmp.cn/slpra/drug-development/")
INTEGRATION = Namespace("https://ontology.pharma-gmp.cn/slpra/integration/")
NEW_PROPERTIES = {DOC.documentNumber, DOC.confidentialityLevel}
INHERITED_PROPERTIES = {
    DOC.documentName, DOC.documentVersion, DOC.approvalStatus,
    DOC.sourceSystem, DOC.contentHash, DEV.ctdModuleNumber,
}


@pytest.fixture
def isolated_tbox(tmp_path, monkeypatch):
    directory = tmp_path / "cmc-ontology"
    copytree(ONTOLOGY_DIR, directory)
    monkeypatch.setattr(settings, "ontology_dir", directory)
    engine = OntologyEngine(directory, tmp_path / "cmc-world.sqlite3")
    try:
        engine.load()
        yield engine
    finally:
        engine.close()


@pytest.mark.parametrize("predicate", sorted(NEW_PROPERTIES))
def test_cmc_metadata_has_only_the_approved_string_declaration(predicate):
    graph = load_base_graph(ONTOLOGY_DIR)
    preview = Graph().parse(
        ROOT / "specs/030-contextual-attribute-disambiguation/contracts/ontology-diff.ttl",
        format="turtle",
    )
    assert len(preview) == 12
    assert set(graph.predicate_objects(predicate)) == set(preview.predicate_objects(predicate))
    assert set(graph.objects(predicate, RDF.type)) == {OWL.DatatypeProperty}
    assert set(graph.objects(predicate, RDFS.domain)) == {DEV.CMCReport}
    assert set(graph.objects(predicate, RDFS.range)) == {XSD.string}
    assert not list(graph.subjects(OWL.onProperty, predicate))
    assert not list(graph.objects(predicate, INTEGRATION.identityKey))
    assert not any(
        predicate in set(graph.items(head)) for head in graph.objects(None, OWL.hasKey)
    )


def test_cmc_record_card_has_eight_properties_without_widening_other_documents(isolated_tbox):
    graph = load_base_graph(ONTOLOGY_DIR)
    snapshot = ontology_snapshot_from_engine(isolated_tbox)
    documents = sorted({str(iri) for iri in graph.transitive_subjects(
        RDFS.subClassOf, DOC.RegulatoryDocument,
    )})
    assert str(DEV.CMCReport) in documents
    assert str(DOC.StabilityReport) in documents
    assert str(DEV.ClinicalStudyReport) in documents
    card = compile_record_schema_card(
        snapshot, class_iris=documents, analysis_scope_ref="cmc-metadata-test",
        profile=ExtractionProfile(),
    )
    properties = {p.iri: p for p in card.for_class(str(DEV.CMCReport)).properties}
    assert set(properties) == {str(iri) for iri in INHERITED_PROPERTIES | NEW_PROPERTIES}
    assert len(properties) == 8
    for predicate in NEW_PROPERTIES:
        prop = properties[str(predicate)]
        assert prop.declared_by == [str(DEV.CMCReport)]
        assert prop.datatype_iris == [str(XSD.string)]
        assert (prop.multiplicity, prop.min_count, prop.max_count) == (
            "unspecified", None, None,
        )
        assert prop.identity_key is False
    for document in documents:
        if document == str(DEV.CMCReport):
            continue
        assert {str(iri) for iri in NEW_PROPERTIES}.isdisjoint(
            p.iri for p in card.for_class(document).properties
        ), document
    assert card.for_class(str(DEV.CMCReport)).identity_keys == []


def test_snapshot_retains_only_loaded_cross_module_parents_and_preserves_old_artifacts():
    from tests.test_extraction.test_ontology_snapshot_ordering import (
        FIRST,
        ROOT,
        EnumeratedEngine,
    )

    class CrossModuleEngine(EnumeratedEngine):
        def get_class_detail(self, iri):
            return SimpleNamespace(comment="", parent_iris=(
                [ROOT, "urn:unloaded:Parent"] if iri == FIRST else []
            ))

    historical = ontology_snapshot_from_engine(EnumeratedEngine()).model_dump(mode="json")
    snapshot = ontology_snapshot_from_engine(CrossModuleEngine())
    assert snapshot.classes[FIRST].parent_iris == [ROOT]
    assert "urn:unloaded:Parent" not in snapshot.classes
    assert OntologySnapshot.model_validate(historical).model_dump(mode="json") == historical
    assert historical["classes"][FIRST]["parent_iris"] == []


def test_seed_and_world_projection_agree_and_do_not_overwrite_existing_drafts(db, isolated_tbox):
    store = OntologyMetaStore(db, isolated_tbox)
    assert store.project_from_ttl() > 0
    rows = db.query(OntologyDataProperty).filter(
        OntologyDataProperty.slpra_iri.in_([str(iri) for iri in NEW_PROPERTIES]),
    ).all()
    assert len(rows) == 2
    before = {row.slpra_iri: (row.id, row.version) for row in rows}
    for row in rows:
        detail = store.data_property_detail(row)
        assert detail["domain_iri"] == str(DEV.CMCReport)
        assert detail["datatype"] == "string"
        assert detail["multiplicity"] == "unspecified"
        assert detail["min_cardinality"] is None and detail["max_cardinality"] is None
        assert detail["controlled_vocab"] is None
        assert detail["status"] == "published"

    # Exercise the existing DB -> World projection using only temporary stores.
    payloads = [payload for payload in store._projection_payloads()
                if payload["kind"] == "data_property"
                and payload["iri"] in {str(iri) for iri in NEW_PROPERTIES}]
    assert len(payloads) == 2
    isolated_tbox.project_entities(payloads, published_graph=load_base_graph(settings.ontology_dir))
    world_properties = {
        prop["iri"]: prop
        for prop in isolated_tbox.get_data_properties_by_domain(str(DEV.CMCReport))
    }
    for row in rows:
        prop = world_properties[row.slpra_iri]
        assert prop["datatype"] == row.datatype == "string"
        assert prop["label"] == row.label
        assert prop["multiplicity"] == row.multiplicity == "unspecified"
        assert prop["min_count"] is None and prop["max_count"] is None
        assert prop.get("identity_key", False) is False
    assert {item["slpra_iri"] for item in store.list_data_properties(
        str(DEV.CMCReport), include_inherited=True,
    )} == {str(iri) for iri in INHERITED_PROPERTIES | NEW_PROPERTIES}

    existing = db.query(OntologyDataProperty).filter_by(slpra_iri=str(DOC.documentName)).one()
    existing.label, existing.status, existing.version = "保留已有草稿", "draft", 7
    db.commit()
    assert store.project_from_ttl() == 0
    db.refresh(existing)
    assert (existing.label, existing.status, existing.version) == ("保留已有草稿", "draft", 7)
    assert {row.slpra_iri: (row.id, row.version) for row in rows} == before
    for document in (DOC.RegulatoryDocument, DOC.StabilityReport, DEV.ClinicalStudyReport):
        assert {str(iri) for iri in NEW_PROPERTIES}.isdisjoint(
            item["slpra_iri"] for item in store.list_data_properties(
                str(document), include_inherited=True,
            )
        )
