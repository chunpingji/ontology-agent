"""CMC material declarations must reach the runtime discovery and type menus."""

from pathlib import Path

import pytest
from rdflib import Namespace

from app.services.document_harness.ontology import (
    freeze_catalog,
    legal_property,
    legal_relation,
    model_menu,
)
from app.services.ontology_engine import OntologyEngine

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology" / "slpra"
DEV = Namespace("https://ontology.pharma-gmp.cn/slpra/drug-development/")
DRUG = Namespace("https://ontology.pharma-gmp.cn/slpra/drug/")
EQUIP = Namespace("https://ontology.pharma-gmp.cn/slpra/equipment/")
MATERIALS = {
    str(DEV[name])
    for name in (
        "ProcessMaterial", "StartingMaterial", "Solvent", "Reagent", "Catalyst", "ProcessAid",
    )
}


@pytest.fixture(scope="module")
def cmc_catalog(tmp_path_factory):
    engine = OntologyEngine(
        ontology_dir=ONTOLOGY_DIR,
        store_path=tmp_path_factory.mktemp("cmc-materials") / "ontology.sqlite3",
    )
    try:
        engine.load()
        return freeze_catalog(engine, str(DEV.CMCReport))
    finally:
        engine.close()


def test_materials_reach_discovery_and_type_menus_from_cmc_report(cmc_catalog):
    assert MATERIALS <= set(cmc_catalog.reachable_class_iris)
    cards = {card["iri"]: card for card in model_menu(cmc_catalog)["classes"]}
    assert MATERIALS <= cards.keys()
    for iri in MATERIALS:
        for predicate in (DEV.materialGrade, DEV.materialPurpose):
            assert legal_property(cmc_catalog, iri, str(predicate))
            assert str(predicate) in {prop["iri"] for prop in cards[iri]["properties"]}
    assert not any(str(DEV.usesMaterial) in item for item in cmc_catalog.diagnostics)


def test_uses_material_subjects_are_report_or_step_with_inherited_scope(cmc_catalog):
    predicate = str(DEV.usesMaterial)
    for subject in (DEV.CMCReport, DEV.SynthesisStep, DEV.Purification):
        relation = next(
            item for item in cmc_catalog.classes[str(subject)].relations
            if item.iri == predicate
        )
        assert len(relation.domain) == 1
        assert relation.domain[0].kind == "union"
        assert {part.iri for part in relation.domain[0].operands} == {
            str(DEV.CMCReport), str(DEV.SynthesisStep),
        }
        for target in MATERIALS:
            assert legal_relation(cmc_catalog, str(subject), predicate, target)
            assert not legal_relation(cmc_catalog, target, predicate, str(subject))
        for target in (EQUIP.Equipment, DRUG.ActivePharmaceuticalIngredient):
            assert not legal_relation(cmc_catalog, str(subject), predicate, str(target))
    for subject in (DEV.SynthesisRoute, DRUG.ActivePharmaceuticalIngredient, EQUIP.Equipment):
        assert not legal_relation(cmc_catalog, str(subject), predicate, str(DEV.Solvent))
