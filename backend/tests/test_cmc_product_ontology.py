"""Authoritative CMC product definitions must reach independent harness cards."""

from pathlib import Path

import pytest
from rdflib import OWL, RDF, XSD, Graph, Namespace

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


@pytest.fixture(scope="module")
def cmc_catalog(tmp_path_factory):
    engine = OntologyEngine(
        ontology_dir=ONTOLOGY_DIR,
        store_path=tmp_path_factory.mktemp("cmc-products") / "ontology.sqlite3",
    )
    try:
        engine.load()
        return freeze_catalog(engine, str(DEV.CMCReport))
    finally:
        engine.close()


def test_cmc_cards_allow_products_and_apis_without_losing_type_or_predicate_boundaries(cmc_catalog):
    catalog = cmc_catalog
    root = str(DEV.CMCReport)
    describes = str(DEV.describes)
    product = str(DRUG.DrugProduct)
    api = str(DRUG.ActivePharmaceuticalIngredient)
    for target in (product, api, str(DRUG.ClinicalTrialDrug), str(DRUG.HighPotencyAPI)):
        assert legal_relation(catalog, root, describes, target)
        assert not legal_relation(catalog, target, describes, root)
        assert not legal_relation(catalog, root, "describes", target)
    for unrelated in (DRUG.Excipient, DEV.ProcessIntermediate, DEV.SynthesisRoute):
        assert not legal_relation(catalog, root, describes, str(unrelated))

    # A wider relation range must not make API a subtype of the finished product.
    assert product not in catalog.classes[api].parent_iris
    assert api not in catalog.classes[product].parent_iris
    menu = model_menu(catalog, [root, product, api])
    cards = {card["iri"]: card for card in menu["classes"]}
    assert {"原料药", "Drug Substance", "API"} <= set(cards[api]["aliases"])
    assert {"药物制剂", "制剂"} <= set(cards[product]["aliases"])
    assert "CMC/GMP" in cards[api]["description"]
    assert "最终给药剂型" in cards[product]["description"]
    relation = next(item for item in cards[root]["relations"] if item["iri"] == describes)
    assert {product, api} <= set(relation["range_class_iris"])
    frozen_relation = next(
        item for item in catalog.classes[root].relations if item.iri == describes
    )
    assert len(frozen_relation.range) == 1
    assert frozen_relation.range[0].kind == "union"
    assert {part.iri for part in frozen_relation.range[0].operands} == {product, api}


def test_chemical_properties_belong_to_api_and_dosage_form_to_finished_product(cmc_catalog):
    chemical_types = {
        "molecularFormula": XSD.string,
        "molecularWeight": XSD.decimal,
        "chemicalNameEn": XSD.string,
        "chemicalNameZh": XSD.string,
        "casNumber": XSD.string,
        "structuralFeatures": XSD.string,
    }
    for name, datatype in chemical_types.items():
        predicate = str(DRUG[name])
        for api_class in (DRUG.ActivePharmaceuticalIngredient, DRUG.HighPotencyAPI):
            assert legal_property(cmc_catalog, str(api_class), predicate)
            card = next(
                prop for prop in cmc_catalog.classes[str(api_class)].properties
                if prop.iri == predicate
            )
            assert card.datatype_iris == (str(datatype),)
            assert not card.identity_key
            assert not legal_property(cmc_catalog, str(api_class), name)
        for other in (DRUG.DrugProduct, DRUG.ClinicalTrialDrug, DRUG.Excipient, DEV.CMCReport):
            assert not legal_property(cmc_catalog, str(other), predicate)

    for product_class in (DRUG.DrugProduct, DRUG.ClinicalTrialDrug):
        assert legal_property(cmc_catalog, str(product_class), str(DRUG.dosageForm))
    for api_class in (DRUG.ActivePharmaceuticalIngredient, DRUG.HighPotencyAPI):
        assert not legal_property(cmc_catalog, str(api_class), str(DRUG.dosageForm))


def test_shared_properties_reach_both_cards_without_becoming_required_or_identity_keys(cmc_catalog):
    shared_types = {
        "appearance": XSD.string,
        "solubility": XSD.string,
        "projectName": XSD.string,
        "projectCode": XSD.string,
        "isOncologyDrug": XSD.boolean,
        "isCytotoxic": XSD.boolean,
        "isHormonal": XSD.boolean,
        "isPenicillinType": XSD.boolean,
        "isHighlySensitizing": XSD.boolean,
        "isHighlyActive": XSD.boolean,
        "physicochemicalProperties": XSD.string,
        "requiresInactivation": XSD.boolean,
    }
    valid_classes = (
        DRUG.ActivePharmaceuticalIngredient, DRUG.HighPotencyAPI,
        DRUG.DrugProduct, DRUG.ClinicalTrialDrug,
    )
    menu = model_menu(cmc_catalog, [str(class_iri) for class_iri in valid_classes])
    cards = {item["iri"]: item for item in menu["classes"]}
    source = Graph().parse(ONTOLOGY_DIR / "slpra-drug.ttl", format="turtle")
    for name, datatype in shared_types.items():
        predicate = str(DRUG[name])
        for class_iri in valid_classes:
            assert legal_property(cmc_catalog, str(class_iri), predicate)
            prop = next(
                item for item in cmc_catalog.classes[str(class_iri)].properties
                if item.iri == predicate
            )
            assert len(prop.domain) == 1 and prop.domain[0].kind == "union"
            assert {item.iri for item in prop.domain[0].operands} == {
                str(DRUG.ActivePharmaceuticalIngredient), str(DRUG.DrugProduct),
            }
            exposed = next(
                item for item in cards[str(class_iri)]["properties"] if item["iri"] == predicate
            )
            assert exposed["datatype_iris"] == [str(datatype)]
            assert exposed["identity_key"] is False
        for other in (DRUG.Excipient, DEV.ProcessIntermediate, DEV.CMCReport):
            assert not legal_property(cmc_catalog, str(other), predicate)
        # Optional source assertions must not require a value or imply a class
        # through a hasValue restriction. Do not introduce uniqueness either.
        assert not list(source.subjects(OWL.onProperty, DRUG[name]))
        assert (DRUG[name], RDF.type, OWL.FunctionalProperty) not in source

    api = cmc_catalog.classes[str(DRUG.ActivePharmaceuticalIngredient)]
    assert not api.identity_key_groups
    # Processing requirements are literals; physical capability keeps its own edge.
    assert legal_relation(
        cmc_catalog, api.iri, str(DRUG.hasInactivationDisposition), str(DRUG.NonInactivatable),
    )
    assert not legal_property(cmc_catalog, api.iri, str(DRUG.hasInactivationDisposition))
    assert not legal_relation(
        cmc_catalog, api.iri, str(DRUG.requiresInactivation), str(DRUG.NonInactivatable),
    )
