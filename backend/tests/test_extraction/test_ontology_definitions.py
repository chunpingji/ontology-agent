"""Authoritative definitions must survive the engine-to-mention-vocabulary path."""

from pathlib import Path

from app.services.extraction.ontology_guided.ontology_plan import ontology_snapshot_from_engine
from app.services.extraction.tool_validation.vocabulary import build_extraction_vocabulary
from app.services.ontology_engine import MANAGED_NAMESPACE_BASE, OntologyEngine


def test_repository_definitions_enable_cleaning_mentions_without_overlay(tmp_path):
    engine = OntologyEngine(
        ontology_dir=Path(__file__).resolve().parents[3] / "ontology" / "slpra",
        store_path=tmp_path / "definitions.sqlite3",
    )
    try:
        engine.load()
        snapshot = ontology_snapshot_from_engine(engine)
    finally:
        engine.close()

    declarations = {
        "class": list(snapshot.classes.values()),
        "object_property": [
            prop
            for definition in snapshot.classes.values()
            for prop in definition.declared_relationships
        ],
        "data_property": [
            prop
            for definition in snapshot.classes.values()
            for prop in definition.declared_properties
        ],
    }
    for kind, definitions in declarations.items():
        managed = [item for item in definitions if item.iri.startswith(MANAGED_NAMESPACE_BASE)]
        assert managed, f"No managed {kind} declarations reached the snapshot"
        missing = sorted({item.iri for item in managed if not item.description.strip()})
        assert not missing, f"Missing {kind} definitions in the snapshot: {missing}"

    cleaning_iris = {
        MANAGED_NAMESPACE_BASE + "cleaning/" + name
        for name in (
            "CleaningProcess",
            "ManualCleaning",
            "CIPCleaning",
            "SIPSterilization",
            "InactivationProcess",
            "HeatInactivation",
            "ChemicalInactivation",
            "pHInactivation",
        )
    }
    vocabulary = build_extraction_vocabulary(snapshot, sorted(cleaning_iris))
    assert vocabulary["metadata"]["overlay_hash"] is None
    assert vocabulary["missing"] == []
    entities = {
        vocabulary["entries"][label]["iri"]: vocabulary["entries"][label]
        for label in vocabulary["groups"]["entity"]
    }
    assert set(entities) == cleaning_iris
    for iri, entry in entities.items():
        assert entry["role"] == "entity"
        assert snapshot.classes[iri].description in entry["description"]
        assert any(
            source["kind"] == "ontology"
            and source.get("iri") == iri
            and source.get("field") == "definition"
            for source in entry["sources"]
        )
