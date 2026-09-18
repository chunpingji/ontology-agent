"""The experimental SKOS hints must not invent facts or merge lexical roles."""

from functools import partial
from pathlib import Path

import pytest
from rdflib import RDF, RDFS, SKOS, Graph, Literal, URIRef

from app.services.extraction.tool_validation.vocabulary import (
    DEFAULT_OVERLAY,
    VOCAB,
    build_experimental_extraction_vocabulary,
)

build_extraction_vocabulary = partial(
    build_experimental_extraction_vocabulary, overlay_path=DEFAULT_OVERLAY
)

DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"


@pytest.fixture()
def ontology_dir(tmp_path):
    directory = tmp_path / "lexical_ontology"
    directory.mkdir()
    (directory / "terms.ttl").write_text("""
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix drug: <https://ontology.pharma-gmp.cn/slpra/drug/> .
        @prefix equip: <https://ontology.pharma-gmp.cn/slpra/equipment/> .
        drug:DrugProduct rdfs:label "Drug product"@en, "药物产品"@zh .
        equip:Equipment rdfs:label "设备"@zh .
        equip:Reactor rdfs:subClassOf equip:Equipment ; skos:altLabel "反应釜"@zh .
        <urn:test:ontology> owl:imports <https://not-to-be-fetched.invalid/import.ttl> .
    """, encoding="utf-8")
    return directory


def card(properties=()):
    return {"label": "unused card label", "properties": list(properties)}


def by_iri(result, iri):
    return next(entry for entry in result["entries"].values() if entry["iri"] == iri)


def test_direct_authority_altlabels_override_manual_fallback_without_network(
    ontology_dir, monkeypatch,
):
    def no_network(*args, **kwargs):
        pytest.fail("Vocabulary compilation must not fetch ontology imports")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    path = ontology_dir / "aliases.ttl"
    path.write_text(f'<{DRUG}DrugProduct> <{SKOS.altLabel}> "制剂成品"@zh .')
    before = {p.name: p.read_bytes() for p in ontology_dir.glob("*.ttl")}
    result = build_extraction_vocabulary(
        {DRUG + "DrugProduct": card()}, [DRUG + "DrugProduct"], ontology_dir=ontology_dir,
    )
    entry = by_iri(result, DRUG + "DrugProduct")
    assert entry["pref_label"] == "药物产品"
    assert entry["alt_labels"] == ["制剂成品"]
    assert result["metadata"]["entries_using_authority_alt_labels"] == 1
    assert any(source["predicate"] == str(SKOS.altLabel) and source["kind"] == "ontology"
               for source in entry["sources"])
    assert {p.name: p.read_bytes() for p in ontology_dir.glob("*.ttl")} == before


def test_missing_authority_aliases_use_explicit_manual_source_without_subclass_leak(ontology_dir):
    result = build_extraction_vocabulary(
        {EQUIP + "Equipment": card()}, [EQUIP + "Equipment"], ontology_dir=ontology_dir,
    )
    entry = by_iri(result, EQUIP + "Equipment")
    assert "equipment" in entry["alt_labels"]
    assert "反应釜" not in entry["alt_labels"]
    assert any(source["kind"] == "manual_overlay" and source["predicate"] == str(SKOS.altLabel)
               for source in entry["sources"])
    assert entry["role"] == "entity_name"
    assert "编号与型号" in entry["description"]


def test_unknown_iri_is_missing_without_label_splitting_or_value_leakage(ontology_dir):
    new = "urn:new:unreviewed_property"
    catalog = {DRUG + "DrugProduct": {
        "label": "untrusted name",
        "properties": [{"iri": new, "label": "秘密报告值/自动标签"}],
    }}
    result = build_extraction_vocabulary(
        catalog, [DRUG + "DrugProduct", "urn:new:outside_class"], ontology_dir=ontology_dir,
    )
    assert {item["iri"] for item in result["missing"]} == {new, "urn:new:outside_class"}
    assert result["groups"]["values"] == []
    assert "秘密报告值" not in str(result["entries"])
    assert "untrusted name" not in str(result["entries"])
    assert result["metadata"]["source_text_used"] is False


def test_metric_identities_and_unit_denominators_remain_distinct(ontology_dir):
    pde_api = DRUG + "pde_mg_per_day"
    pde_record = DEV + "pde_mg_per_day"
    noael = DEV + "noael_mg_per_kg_per_day"
    dose = DRUG + "maximumDailyDose_mg"
    result = build_extraction_vocabulary({
        DRUG + "DrugProduct": card([{"iri": dose, "canonical_unit": "mg"}]),
        DRUG + "ActivePharmaceuticalIngredient": card([
            {"iri": pde_api, "canonical_unit": "mg/day"}]),
        DEV + "SharedLineAssessmentData": card([
            {"iri": pde_record, "canonical_unit": "mg/day"},
            {"iri": noael, "canonical_unit": "mg/kg/day"}]),
    }, [DRUG + "DrugProduct", DRUG + "ActivePharmaceuticalIngredient",
        DEV + "SharedLineAssessmentData"], ontology_dir=ontology_dir)
    assert result["missing"] == []
    assert len(result["groups"]["values"]) == 4
    assert "PDE" in by_iri(result, pde_api)["alt_labels"]
    assert "PDE" in by_iri(result, pde_record)["alt_labels"]
    assert "PDE" not in by_iri(result, noael)["alt_labels"]
    assert "PDE" not in by_iri(result, dose)["alt_labels"]
    record = by_iri(result, DEV + "SharedLineAssessmentData")
    assert record["role"] == "record_anchor"
    assert "共享API单元" in record["description"]
    units = [entry for entry in result["entries"].values() if entry["role"] == "unit"]
    canonical = {entry["canonical_unit"] for entry in units}
    assert {"mg", "mg/day", "mg/kg/day"} <= canonical
    per_day = next(entry for entry in units if entry["canonical_unit"] == "mg/day")
    assert "mg" not in per_day["alt_labels"]
    assert "mg/kg/day" not in per_day["alt_labels"]
    assert "不能从属性" in by_iri(result, noael)["description"]


def test_value_hints_preserve_roles_instead_of_extracting_field_headers(ontology_dir):
    properties = [DRUG + name for name in ("projectName", "productIdentifier", "isCytotoxic")]
    result = build_extraction_vocabulary(
        {DRUG + "DrugProduct": card([{"iri": iri} for iri in properties])},
        [DRUG + "DrugProduct"], ontology_dir=ontology_dir,
    )
    for iri in properties:
        entry = by_iri(result, iri)
        assert entry["role"] == "attribute_value"
        assert "不抽字段标题" in entry["description"]
    assert "不自动具备唯一实例身份" in by_iri(result, DRUG + "productIdentifier")["description"]
    assert "基因毒性阴性" in by_iri(result, DRUG + "isCytotoxic")["description"]
    assert "项目名称" not in by_iri(result, DRUG + "productIdentifier")["alt_labels"]


def test_shelf_life_does_not_make_retest_and_expiry_equivalent(ontology_dir):
    result = build_extraction_vocabulary(
        {DEV + "StorageCondition": card([{"iri": DEV + "shelfLife"}])},
        [DEV + "StorageCondition"], ontology_dir=ontology_dir,
    )
    entry = by_iri(result, DEV + "shelfLife")
    assert "有效期" not in entry["alt_labels"]
    assert "复测期" not in entry["alt_labels"]
    assert "不是等价业务概念" in entry["description"]
    month = by_iri(result, str(VOCAB.UnitMonth))
    assert "M" in month["alt_labels"] and "M有多种含义" in month["description"]
    assert all(entry["role"] != "unit" or entry.get("canonical_unit") != "mg/day"
               for entry in result["entries"].values())


def test_distinct_iris_with_same_extraction_label_fail_instead_of_merging(ontology_dir, tmp_path):
    graph = Graph().parse(DEFAULT_OVERLAY, format="turtle")
    source, target = URIRef(DRUG + "DrugProduct"), URIRef(EQUIP + "Equipment")
    label = graph.value(source, VOCAB.extractionLabel)
    graph.set((target, VOCAB.extractionLabel, label))
    overlay = tmp_path / "collision.ttl"
    graph.serialize(overlay, format="turtle")
    with pytest.raises(ValueError, match="duplicate_extraction_label"):
        build_extraction_vocabulary(
            {str(source): card(), str(target): card()}, [str(source), str(target)],
            ontology_dir=ontology_dir, overlay_path=overlay,
        )


def test_overlay_is_lexical_only_and_every_term_marks_manual_origin():
    graph = Graph().parse(DEFAULT_OVERLAY, format="turtle")
    terms = set(graph.subjects(VOCAB.role, None))
    assert len(terms) == 108  # 15 selected classes, 75 properties, 18 separate units.
    assert all(list(graph.objects(term, VOCAB.source)) == [VOCAB.ManualGeneralKnowledge]
               for term in terms)
    assert not list(graph.triples((None, RDFS.subClassOf, None)))
    assert not list(graph.triples((None, RDF.type, None)))
    text = Path(DEFAULT_OVERLAY).read_text()
    assert "HRS-1597" not in text and "RE64611" not in text
    assert not list(graph.triples((None, URIRef("http://www.w3.org/2002/07/owl#sameAs"), None)))


def test_all_declared_manual_properties_compile_without_implicit_limits(ontology_dir):
    graph = Graph().parse(DEFAULT_OVERLAY, format="turtle")
    properties = sorted(str(iri) for iri in graph.subjects(VOCAB.role, Literal("attribute_value")))
    catalog = {DRUG + "DrugProduct": card([{"iri": iri} for iri in properties])}
    result = build_extraction_vocabulary(catalog, list(catalog), ontology_dir=ontology_dir)
    assert len(result["groups"]["values"]) == 75
    assert result["missing"] == []
    assert result["metadata"]["truncated"] is False


@pytest.fixture()
def frozen_snapshot():
    from app.services.extraction.evidence_identity import evidence_hash
    from app.services.extraction.ontology_guided.contracts import (
        ONTOLOGY_LEXICAL_SNAPSHOT_VERSION,
        OntologyClassDefinition,
        OntologySnapshot,
        SlotSpec,
    )
    from app.services.extraction.ontology_guided.ontology_lexical import build_lexical_context

    classes = {"urn:Record": OntologyClassDefinition(
        iri="urn:Record", label="记录", description="一条独立记录", source_hash="frozen",
        declared_properties=[SlotSpec(
            iri="urn:mass", label="质量", description="该记录的质量", canonical_unit="mg",
        ), SlotSpec(iri="urn:unknown", label="未定义")],
    )}
    lexical = build_lexical_context({
        "urn:Record": [{"text": "记录别名", "language": "zh", "predicate_iri": str(SKOS.altLabel)}],
        "urn:mass": [{"text": "重量", "language": "zh", "predicate_iri": str(SKOS.altLabel)}],
        "urn:subclass": [{"text": "不可继承别名", "predicate_iri": str(SKOS.altLabel)}],
    })
    return OntologySnapshot(
        snapshot_id="snapshot", version=ONTOLOGY_LEXICAL_SNAPSHOT_VERSION,
        ontology_hash=evidence_hash({"classes": classes, "lexical_context": lexical}),
        classes=classes, lexical_context=lexical,
    )


def test_generic_vocabulary_requires_no_overlay_and_keeps_both_field_roles(frozen_snapshot):
    from app.services.extraction.tool_validation.vocabulary import build_extraction_vocabulary

    result = build_extraction_vocabulary(frozen_snapshot, ["urn:Record"])
    entries = list(result["entries"].values())
    assert {(entry["iri"], entry["role"]) for entry in entries} == {
        ("urn:Record", "entity"), ("urn:mass", "field_label"), ("urn:mass", "field_value"),
    }
    assert {entry["role"] for entry in result["missing"]} == {"field_label", "field_value"}
    assert all(entry["reason"] == "definition_missing" for entry in result["missing"])
    assert "不可继承别名" not in str(result)
    assert "cmc" not in str(result)
    assert all(label.startswith("vocab_") for label in result["entries"])
    assert result["groups"]["field_label"] != result["groups"]["field_value"]
    assert all(entry["alt_labels"] == ["重量"] for entry in entries if entry["iri"] == "urn:mass")


def test_overlay_roles_sources_and_unit_selection_are_explicit(frozen_snapshot):
    from app.services.extraction.tool_validation.vocabulary import (
        VocabularyOverlay,
        build_extraction_vocabulary,
    )

    overlay = VocabularyOverlay(version="1", entries=[{
        "iri": "urn:Record", "role": "record_anchor", "source_refs": ["curated-v1"],
        "aliases": [{"text": "被权威别名替代"}],
    }, {
        "iri": "urn:unit-mg", "role": "unit", "source_refs": ["units-v1"],
        "labels": [{"text": "毫克", "language": "zh"}], "definition": "质量单位毫克",
        "for_canonical_units": ["mg"], "canonical_unit": "mg",
    }, {
        "iri": "urn:unit-kg", "role": "unit", "source_refs": ["units-v1"],
        "labels": [{"text": "千克"}], "definition": "质量单位千克",
        "for_predicates": ["urn:not-selected"], "canonical_unit": "kg",
    }])
    result = build_extraction_vocabulary(frozen_snapshot, ["urn:Record"], overlay=overlay)
    record = by_iri(result, "urn:Record")
    assert record["role"] == "record_anchor"
    assert record["alt_labels"] == ["记录别名"]
    assert len(result["groups"]["entity"]) == 1
    assert by_iri(result, "urn:unit-mg")["canonical_unit"] == "mg"
    assert "urn:unit-kg" not in str(result["entries"])
    assert {row["source_ref"] for row in record["sources"] if row["kind"] == "manual_overlay"} == {
        "curated-v1",
    }


def test_generic_labels_are_stable_and_overlay_cannot_expand_predicates(frozen_snapshot):
    from app.services.extraction.tool_validation.vocabulary import (
        VocabularyOverlay,
        build_extraction_vocabulary,
    )

    initial = build_extraction_vocabulary(frozen_snapshot, ["urn:Record"])
    overlay = VocabularyOverlay(version="1", entries=[{
        "iri": "urn:outside", "role": "field_value", "source_refs": ["source"],
        "labels": [{"text": "新字段"}], "definition": "外部词表无权添加谓词",
    }, {
        "iri": "urn:mass", "role": "field_value", "source_refs": ["source"],
        "aliases": [{"text": "不应覆盖已有别名"}],
    }])
    result = build_extraction_vocabulary(frozen_snapshot, ["urn:Record"], overlay=overlay)
    assert set(initial["entries"]) == set(result["entries"])
    assert "urn:outside" not in str(result["entries"])
    assert result["metadata"]["overlay_hash"] is not None


def test_overlay_rejects_missing_sources_and_colliding_roles():
    from pydantic import ValidationError

    from app.services.extraction.tool_validation.vocabulary import VocabularyOverlay

    entry = {"iri": "urn:p", "role": "field_label", "source_refs": []}
    with pytest.raises(ValidationError):
        VocabularyOverlay(version="v1", entries=[entry])
    entry["source_refs"] = ["source"]
    with pytest.raises(ValidationError, match="duplicate_vocabulary_role"):
        VocabularyOverlay(version="v1", entries=[entry, entry])
    entry["extraction_label"] = "collision"
    with pytest.raises(ValidationError, match="duplicate_extraction_label"):
        VocabularyOverlay(version="v1", entries=[entry, entry | {"role": "field_value"}])
