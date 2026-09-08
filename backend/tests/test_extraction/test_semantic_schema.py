from pathlib import Path
from types import SimpleNamespace

from app.services.extraction.extraction_tasks import semantic_schema_from_engine


def test_multiple_inheritance_closes_parent_menus_without_executable_annotations():
    class Engine:
        def get_modules(self):
            return [SimpleNamespace(key="example")]

        def get_class_hierarchy(self, key):
            child = SimpleNamespace(iri="urn:Child", label="Child", children=[])
            return [
                SimpleNamespace(iri=iri, label=iri, children=[child]) for iri in ("urn:A", "urn:B")
            ]

        def get_class_detail(self, iri):
            return SimpleNamespace(comment="definition")

        def get_data_properties_by_domain(self, iri):
            return (
                [{"iri": "urn:value", "datatype": "decimal", "pattern": "forbidden",
                  "canonical_unit": "mg", "identity_key": False}]
                if iri == "urn:A"
                else []
            )

        def get_object_properties_by_domain(self, iri):
            return [{"iri": "urn:uses", "range": ["urn:A"]}] if iri == "urn:B" else []

    schema = semantic_schema_from_engine(Engine())
    assert schema["urn:Child"]["properties"] == [
        {"iri": "urn:value", "datatype": "decimal", "canonical_unit": "mg", "identity_key": False}
    ]
    assert schema["urn:Child"]["relationships"] == [{"iri": "urn:uses", "range": ["urn:A"]}]
    assert schema["urn:Child"]["parents"] == ["urn:A", "urn:B"]


def test_repository_ontology_transmits_units_and_identity_contracts(tmp_path):
    from app.services.extraction.literal_normalizer import normalize_literal
    from app.services.ontology_engine import OntologyEngine

    engine = OntologyEngine(
        ontology_dir=Path(__file__).resolve().parents[3] / "ontology" / "slpra",
        store_path=tmp_path / "schema.sqlite3",
    )
    try:
        engine.load()
        schema = semantic_schema_from_engine(engine)
        properties = {p["iri"]: p for c in schema.values() for p in c.get("properties", [])}
        base = "https://ontology.pharma-gmp.cn/slpra/"
        expected = {
            "drug/ld50_mg_per_kg": "mg/kg",
            "drug/pde_mg_per_day": "mg/day",
            "drug/oel_ug_per_m3": "ug/m3",
            "facility/temperature_C": "°C",
            "drug-development/plannedBatchSizeMin_kg": "kg",
            "drug-development/plannedBatchSizeMax_kg": "kg",
        }
        for name, unit in expected.items():
            assert properties[base + name]["canonical_unit"] == unit
        for definition in properties.values():
            if definition.get("canonical_unit"):
                value = normalize_literal(
                    "1" + definition["canonical_unit"], datatype=definition["datatype"],
                    target_unit=definition["canonical_unit"],
                )
                assert value.normalized_value == "1"
        assert properties[base + "equipment/equipmentID"]["identity_key"] is True
        assert any(p.get("identity_key") is True for p in schema[
            base + "drug/ActivePharmaceuticalIngredient"
        ]["properties"])
        # Returned projections are detached; T-Box edits invalidate the snapshot.
        iri = base + "drug/ActivePharmaceuticalIngredient"
        original_label = schema[iri]["label"]
        schema[iri]["label"] = "caller-local edit"
        assert semantic_schema_from_engine(engine)[iri]["label"] == original_label
        engine.upsert_class(iri, label="Updated label")
        assert semantic_schema_from_engine(engine)[iri]["label"] == "Updated label"
    finally:
        engine.close()
