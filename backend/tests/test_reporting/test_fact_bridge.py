"""Unit tests for the fact bridge layer (010, quickstart scenario 4)."""

from __future__ import annotations

import pytest

from app.services.reasoning.fact_bridge import apply_postconditions, edges_to_facts


def _equipment_edge(code: str = "RE64202", spec: str = "搅拌釜", material: str = "316L") -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "设备规格", "value": spec},
            {"iri": None, "label": "材质", "value": material},
        ],
        "source_ref": "表 设备需求",
    }


def _drug_product_edge(drug_code: str = "HRS-1234") -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": drug_code,
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _shared_line_edge() -> dict:
    return {
        "subject_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/hasSharedLineData",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/SharedLineAssessmentData",
        "object_text": "共线评估数据",
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
        ],
        "source_ref": "§ 共线评估",
    }


class TestEdgesToFacts:
    def test_equipment_edge_maps_to_relations(self):
        facts = edges_to_facts([_equipment_edge()])
        assert "usesEquipment" in facts.relations
        assert any("Equipment" in v for v in facts.relations["usesEquipment"])

    def test_pde_extraction_to_scalars_and_data_values(self):
        facts = edges_to_facts([_drug_product_edge()])
        assert facts.scalars["PDE"] == "1.80"
        assert facts.data_values["pde_mg_per_day"] == "1.80"

    def test_drug_class_extraction(self):
        facts = edges_to_facts([_drug_product_edge()])
        assert "化学药品" in facts.drug_classes

    def test_empty_edges_produce_empty_facts(self):
        facts = edges_to_facts([])
        assert facts.relations == {}
        assert facts.data_values == {}
        assert facts.scalars == {}
        assert facts.drug_classes == []

    def test_shared_line_edge_maps_predicate(self):
        facts = edges_to_facts([_shared_line_edge()])
        assert "hasSharedLineData" in facts.relations

    def test_multiple_edges_accumulate(self):
        facts = edges_to_facts([
            _equipment_edge("RE001"),
            _equipment_edge("RE002"),
            _drug_product_edge(),
        ])
        assert len(facts.relations["usesEquipment"]) >= 1
        assert "化学药品" in facts.drug_classes

    def test_data_property_without_iri_goes_only_to_scalars(self):
        facts = edges_to_facts([_equipment_edge()])
        assert "设备规格" in facts.scalars
        assert facts.scalars["设备规格"] == "搅拌釜"


class TestApplyPostconditions:
    def test_boolean_postconditions_inject_into_scalars(self):
        facts = edges_to_facts([_shared_line_edge()])
        augmented = apply_postconditions(facts, {
            "equipment_qualified": True,
            "shared_line_assessed": True,
        })
        assert augmented.scalars["equipment_qualified"] is True
        assert augmented.scalars["shared_line_assessed"] is True

    def test_original_facts_unchanged(self):
        facts = edges_to_facts([_shared_line_edge()])
        original_scalars = dict(facts.scalars)
        apply_postconditions(facts, {"equipment_qualified": True})
        assert facts.scalars == original_scalars

    def test_iri_postcondition_goes_to_relations(self):
        facts = edges_to_facts([])
        augmented = apply_postconditions(facts, {
            "has_validation": "https://example.org/Validated",
        })
        assert "has_validation" in augmented.relations
        assert "https://example.org/Validated" in augmented.relations["has_validation"]
