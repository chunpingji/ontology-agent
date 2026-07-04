"""Unit tests for the declarative AST report template (010, AST-1) + 012 extensions."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.services.reporting.ast_template import (
    DEFAULT_TEMPLATE_ID,
    ExtractionSource,
    Group,
    LLMExtractionSource,
    ReportTemplate,
    Repeat,
    Slot,
    load_default_template,
    load_template,
    resolve_template,
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


class TestLLMExtractionSource:
    """012: LLMExtractionSource discriminated union variant."""

    def test_constructs_with_required_fields(self):
        src = LLMExtractionSource(
            object_class_iri="http://slpra.org/Drug",
            data_property_iri="http://slpra.org/pde",
            label="PDE",
        )
        assert src.kind == "llm_extraction"
        assert src.object_class_iri == "http://slpra.org/Drug"

    def test_slot_accepts_llm_source(self):
        slot = Slot(
            slot_id="x.pde",
            label="PDE",
            source=LLMExtractionSource(
                object_class_iri="http://slpra.org/Drug",
                data_property_iri="http://slpra.org/pde",
                label="PDE",
            ),
        )
        assert slot.source.kind == "llm_extraction"

    def test_discriminated_union_roundtrip(self):
        tpl = ReportTemplate(
            template_id="t",
            sections=[{
                "section_id": "s", "title": "S",
                "groups": [{
                    "group_id": "g", "title": "G", "kind": "fields",
                    "slots": [{
                        "slot_id": "a",
                        "label": "A",
                        "source": {"kind": "llm_extraction", "object_class_iri": "X",
                                   "data_property_iri": "Y", "label": "Z"},
                    }],
                }],
            }],
        )
        _, _, slot = next(tpl.iter_slots())
        assert slot.source.kind == "llm_extraction"


class TestResolveTemplate:
    """015: three-tier fallback resolution keyed on per-template ``iri_pattern``.

    (Replaces the retired DocumentTypeMapping tier; iri_pattern is the functional
    resolution key.)
    """

    def _seed_template(
        self, db, *, name="Test", version="v1", is_default=False,
        iri_pattern=None, status="draft",
    ):
        from app.models.extraction import AstTemplate
        tpl_json = load_default_template().model_dump()
        row = AstTemplate(
            name=name, version=version, doc_no="TEST",
            schema_json=tpl_json, is_default=is_default,
            iri_pattern=iri_pattern, status=status,
            created_by="test",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def test_tier3_fallback_no_db_templates(self, db):
        tpl, source, db_id = resolve_template("SomeReport", db)
        assert source == "fallback"
        assert db_id is None
        assert tpl.template_id == DEFAULT_TEMPLATE_ID

    def test_tier2_default_template(self, db):
        row = self._seed_template(db, is_default=True)
        tpl, source, db_id = resolve_template("UnknownType", db)
        assert source == "default"
        assert db_id == row.id

    def test_tier1_iri_pattern_match(self, db):
        row = self._seed_template(db, name="CMC", iri_pattern="CMCReport")
        tpl, source, db_id = resolve_template(
            "http://slpra.org/ontology/CMCReport", db,
        )
        assert source == "iri_pattern"
        assert db_id == row.id

    def test_iri_pattern_longest_wins(self, db):
        broad = self._seed_template(db, name="Broad", version="v1", iri_pattern="Report")
        specific = self._seed_template(
            db, name="Specific", version="v2", iri_pattern="CMCReport",
        )
        _, source, db_id = resolve_template(
            "http://slpra.org/ontology/CMCReport", db,
        )
        assert source == "iri_pattern"
        assert db_id == specific.id

    def test_archived_template_excluded_from_iri_pattern(self, db):
        self._seed_template(
            db, name="Archived", iri_pattern="CMCReport", status="archived",
        )
        default_row = self._seed_template(db, name="Fallback", version="v2", is_default=True)
        _, source, db_id = resolve_template(
            "http://slpra.org/ontology/CMCReport", db,
        )
        # archived pattern is skipped → falls through to the default tier
        assert source == "default"
        assert db_id == default_row.id

    def test_no_doc_class_iri_skips_iri_pattern(self, db):
        row = self._seed_template(
            db, name="CMC", iri_pattern="CMCReport", is_default=True,
        )
        tpl, source, db_id = resolve_template(None, db)
        assert source == "default"
        assert db_id == row.id

    def test_delete_default_rejected(self, client, db, analyst_headers):
        row = self._seed_template(db, name="Default", is_default=True)
        resp = client.delete(f"/api/ast-templates/{row.id}", headers=analyst_headers)
        assert resp.status_code == 400

    def test_set_default_unsets_previous(self, client, db, analyst_headers):
        t1 = self._seed_template(db, name="A", version="v1", is_default=True)
        t2 = self._seed_template(db, name="B", version="v2", is_default=False)
        resp = client.post(f"/api/ast-templates/{t2.id}/set-default", headers=analyst_headers)
        assert resp.status_code == 200
        db.refresh(t1)
        db.refresh(t2)
        assert t1.is_default is False
        assert t2.is_default is True
