"""Unit tests for the declarative AST report template (010, AST-1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.reporting.ast_template import (
    DEFAULT_TEMPLATE_ID,
    ExtractionSource,
    Group,
    ReportTemplate,
    Repeat,
    Slot,
    load_default_template,
    load_template,
)


class TestDefaultTemplate:
    def test_loads_and_validates(self):
        tpl = load_default_template()
        assert tpl.template_id == DEFAULT_TEMPLATE_ID
        assert tpl.doc_no == "QS-A-020F05"
        assert len(tpl.sections) == 2

    def test_load_by_id(self):
        assert load_template(DEFAULT_TEMPLATE_ID).template_id == DEFAULT_TEMPLATE_ID

    def test_unknown_id_raises(self):
        with pytest.raises(KeyError):
            load_template("does-not-exist")

    def test_iter_slots_covers_all_groups(self):
        tpl = load_default_template()
        slot_ids = {slot.slot_id for _, _, slot in tpl.iter_slots()}
        # subject + prereq + equipment columns + assessment fields + manuals
        assert "subject.name" in slot_ids
        assert "prereq.shared_line" in slot_ids
        assert "equipment.id" in slot_ids
        assert "assessment.pre_control_level" in slot_ids
        assert "conclusion.text" in slot_ids

    def test_required_slots_are_the_minimal_material_set(self):
        tpl = load_default_template()
        required = {s.slot_id for s in tpl.required_slots()}
        # the no-omission contract: these must be filled or explicitly flagged
        assert {
            "subject.name",
            "subject.pde",
            "subject.class",
            "prereq.shared_line",
            "equipment.id",
            "assessment.hazid",
            "assessment.pre_control_level",
            "assessment.post_control_level",
            "assessment.control_measures",
            "assessment.traceability",
            "assessment.status",
        } <= required
        # manual / optional slots are NOT required
        assert "team.members" not in required
        assert "subject.dosage" not in required

    def test_repeat_tables_declare_repeat(self):
        tpl = load_default_template()
        by_id = {g.group_id: g for s in tpl.sections for g in s.groups}
        assert by_id["equipment"].repeat is not None
        assert by_id["equipment"].repeat.by == "workshop"
        assert by_id["assessment"].repeat.by == "rule"
        assert by_id["assessment"].repeat.rule_group == "risk_assessment"

    def test_extraction_bindings_match_real_edge_fields(self):
        """Slot bindings align with the edge field conventions used in extraction."""
        tpl = load_default_template()
        by_id = {slot.slot_id: slot for _, _, slot in tpl.iter_slots()}
        assert by_id["subject.pde"].source.label == "PDE"
        assert by_id["subject.class"].source.label == "分类"
        assert by_id["subject.name"].source.text is True
        assert by_id["equipment.spec"].source.label == "设备规格"
        assert by_id["prereq.shared_line"].source.relation == "hasSharedLineData"


class TestSchemaValidation:
    def test_extraction_requires_exactly_one_selector(self):
        with pytest.raises(ValidationError):
            ExtractionSource(object_class_iri_contains="DrugProduct")  # zero selectors
        with pytest.raises(ValidationError):
            ExtractionSource(
                object_class_iri_contains="DrugProduct", text=True, label="PDE"
            )  # two selectors
        # exactly one is fine
        ExtractionSource(object_class_iri_contains="DrugProduct", text=True)

    def test_table_group_requires_repeat(self):
        with pytest.raises(ValidationError):
            Group(group_id="eq", title="设备", kind="equipment_table")

    def test_repeat_by_rule_requires_rule_group(self):
        with pytest.raises(ValidationError):
            Repeat(by="rule")
        Repeat(by="rule", rule_group="risk_assessment")  # ok

    def test_duplicate_slot_ids_rejected(self):
        dup = Slot(
            slot_id="x",
            label="x",
            source=ExtractionSource(object_class_iri_contains="DrugProduct", text=True),
        )
        with pytest.raises(ValidationError):
            ReportTemplate(
                template_id="t",
                sections=[
                    {
                        "section_id": "s",
                        "title": "s",
                        "groups": [
                            {"group_id": "g", "title": "g", "kind": "fields", "slots": [dup, dup]}
                        ],
                    }
                ],
            )
