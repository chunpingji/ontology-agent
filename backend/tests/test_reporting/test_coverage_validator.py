"""Unit tests for the material coverage validator (010, AST-2)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.reporting.ast_template import load_default_template
from app.services.reporting.coverage_validator import (
    MISSING_REQUIRED,
    validate_coverage,
)

# --- edge fixtures (shapes mirror test_risk_report_generator.py) ------------ #


def _drug_edge() -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/describes",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct",
        "object_text": "HRS-1234",
        "object_data_properties": [
            {"iri": "https://ontology.pharma-gmp.cn/slpra/drug/pde_mg_per_day",
             "label": "PDE", "value": "1.80"},
            {"iri": None, "label": "分类", "value": "化学药品"},
        ],
        "source_ref": "§ 产品信息",
    }


def _shared_line_edge() -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/hasSharedLineData",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/SharedLineAssessmentData",
        "object_text": "共线评估",
        "object_data_properties": [],
        "source_ref": "§ 共线评估",
    }


def _equipment_edge(code: str = "RE001") -> dict:
    return {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "设备名称", "value": f"设备-{code}"},
            {"iri": None, "label": "设备规格", "value": "搅拌釜 500L"},
            {"iri": None, "label": "材质", "value": "316L"},
        ],
        "source_ref": "642车间 设备需求",
    }


def _shared_line_rule(key: str, category: str) -> SimpleNamespace:
    return SimpleNamespace(
        rule_key=key,
        antecedent={"op": "some_values_from", "property": "hasSharedLineData",
                    "filler_class": "SharedLineAssessmentData"},
        consequent={"risk_level": "HighRisk", "category": category},
    )


def _full_rules() -> list[SimpleNamespace]:
    return [
        _shared_line_rule("R-RA1", "人员"),
        _shared_line_rule("R-RA2", "生产设备"),
    ]


# --- tests ------------------------------------------------------------------ #


class TestCoverageValidator:
    def test_full_coverage_has_no_omissions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        assert not m.has_omissions
        assert m.missing_required == 0
        # extraction slots filled
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["subject.name"].status == "filled"
        assert by_id["subject.pde"].value == "1.80"
        assert by_id["prereq.shared_line"].status == "filled"
        # rule slots inferred (antecedent TRUE because shared-line present)
        assert by_id["assessment.pre_control_level[R-RA2]"].status == "inferred"

    def test_missing_shared_line_flags_prereq_and_dimensions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _equipment_edge()]  # no shared-line edge
        m = validate_coverage(tpl, edges, _full_rules())
        assert m.has_omissions
        missing = {s.slot_id for s in m.missing_required_slots}
        # prerequisite surfaces the missing extraction input
        assert "prereq.shared_line" in missing
        # and the rule dimensions become UNKNOWN → missing (G1: not silent "低")
        assert "assessment.pre_control_level[R-RA1]" in missing
        assert "assessment.pre_control_level[R-RA2]" in missing

    def test_missing_equipment_flags_required_id(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge()]  # no equipment
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["equipment.id"].status == MISSING_REQUIRED

    def test_no_rules_flags_all_dimensions(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, [])
        missing = {s.slot_id for s in m.missing_required_slots}
        assert any(sid.endswith("__no_rules__") for sid in missing)

    def test_manual_slots_not_counted_missing(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        assert by_id["team.members"].status == "manual"
        assert by_id["conclusion.text"].status == "manual"
        assert m.manual >= 4  # team, review, conclusion, approvals

    def test_optional_missing_is_blank_not_required(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _shared_line_edge(), _equipment_edge()]
        m = validate_coverage(tpl, edges, _full_rules())
        by_id = {s.slot_id: s for s in m.slots}
        # subject.dosage (剂型) is optional and absent from the drug edge
        assert by_id["subject.dosage"].status == "blank_optional"

    def test_summary_and_to_dict_shapes(self):
        tpl = load_default_template()
        edges = [_drug_edge(), _equipment_edge()]  # missing shared-line
        m = validate_coverage(tpl, edges, _full_rules())
        summ = m.summary()
        assert summ["template_id"] == tpl.template_id
        assert summ["missing_required"] == len(summ["missing_slot_ids"])
        assert summ["total_slots"] == m.total_slots
        full = m.to_dict()
        assert len(full["slots"]) == m.total_slots
        assert "prereq.shared_line" in summ["missing_slot_ids"]
