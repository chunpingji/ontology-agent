"""Unit tests for the declarative AST report template (010, AST-1) + 012 extensions."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.services.reporting.ast_template import (
    DEFAULT_TEMPLATE_ID,
    ExtractionSource,
    FactSourceBinding,
    Group,
    LLMExtractionSource,
    OntologyRelationBinding,
    ReportTemplate,
    Repeat,
    Section,
    SemanticSource,
    Slot,
    coverage_key,
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


class TestSectionCoverage:
    """016 AST-1: section-level ontology coverage declarations (contract coverage-schema.md).

    Coverage nests inside ``schema_json``; the invariant is that ``Section.coverage`` is a
    DECLARED field (the codebase default is ``extra='ignore'``, which would silently drop an
    un-declared key on ``model_validate``).
    """

    DOC = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"
    PRED = "https://ontology.pharma-gmp.cn/slpra/drug/manufacturedBy"
    RANGE = "https://ontology.pharma-gmp.cn/slpra/drug/Manufacturer"

    def test_section_coverage_round_trips(self):
        """C1/C2: coverage survives model_validate → model_dump (declared field, not extra)."""
        template_json = {
            "template_id": "t-cov",
            "sections": [
                {
                    "section_id": "s1",
                    "title": "概述",
                    "groups": [],
                    "coverage": [
                        {
                            "kind": "ontology_relation",
                            "doc_class_iri": self.DOC,
                            "predicate_iri": self.PRED,
                            "range_class_iri": self.RANGE,
                        },
                        {"kind": "fact_source", "source": "org.responsible_person"},
                    ],
                }
            ],
        }
        t = ReportTemplate.model_validate(template_json)
        cov = t.sections[0].coverage
        assert cov[0].kind == "ontology_relation"
        assert cov[0].required is True  # C4 — required by default (FR-005a)
        assert cov[0].doc_class_iri == self.DOC
        assert cov[0].required_properties == []
        assert cov[1].kind == "fact_source"

        dumped = t.model_dump()
        assert dumped["sections"][0]["coverage"][0]["required"] is True
        assert dumped["sections"][0]["coverage"][0]["doc_class_iri"] == self.DOC
        # discriminated union survives a full re-validate (kind discriminator intact)
        reparsed = ReportTemplate.model_validate(dumped)
        assert reparsed.sections[0].coverage[0].kind == "ontology_relation"
        assert reparsed.sections[0].coverage[1].kind == "fact_source"

    def test_legacy_template_without_coverage_unchanged(self):
        """C1: a template with no ``coverage`` key validates and defaults to [] (not dropped)."""
        tpl = load_default_template()
        for section in tpl.sections:
            assert section.coverage == []  # default, present as a declared field

        dumped = tpl.model_dump()
        assert all(s["coverage"] == [] for s in dumped["sections"])
        # no shape drift: re-validating the dump is idempotent
        assert ReportTemplate.model_validate(dumped).model_dump() == dumped

    def test_required_defaults_true_and_properties_empty(self):
        """C3/C4: direct construction — required-by-default, no individual-capable field."""
        b = OntologyRelationBinding(
            doc_class_iri=self.DOC, predicate_iri=self.PRED, range_class_iri=self.RANGE
        )
        assert b.required is True
        assert b.required_properties == []
        # C3: the model exposes only class/predicate TYPE IRIs — no individual field exists
        assert set(b.model_dump().keys()) == {
            "kind",
            "doc_class_iri",
            "predicate_iri",
            "range_class_iri",
            "required",
            "required_properties",
            "label",
        }

    def test_section_defaults_empty_coverage(self):
        """A Section built without coverage still exposes the declared [] default."""
        s = Section(section_id="s", title="t", groups=[])
        assert s.coverage == []
        assert FactSourceBinding(source="x").kind == "fact_source"


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


# --------------------------------------------------------------------------- #
# 016+: SemanticSource (语义化插槽) — a projection of Section.coverage + prompt
# --------------------------------------------------------------------------- #

_MFR = "https://ontology.pharma-gmp.cn/slpra/drug/Manufacturer"
_MADE_BY = "https://ontology.pharma-gmp.cn/slpra/drug/manufacturedBy"
_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/DrugProduct"


class TestSemanticSource:
    def test_defaults(self):
        src = SemanticSource()
        assert src.kind == "semantic"
        assert src.prompt is None  # None ⇒ inherit Section.prompt at report time
        assert src.coverage_refs == []  # [] ⇒ project the whole section's coverage

    def test_round_trips_through_slot_schema_json(self):
        """A semantic slot survives model → dict → model with no data loss (schema_json)."""
        slot = Slot(
            slot_id="analysis.overview",
            label="综合分析",
            source=SemanticSource(
                prompt="综述本节关联本体与确定性风险结论",
                coverage_refs=[coverage_key(
                    OntologyRelationBinding(
                        doc_class_iri=_DRUG, predicate_iri=_MADE_BY, range_class_iri=_MFR,
                    )
                )],
            ),
        )
        dumped = slot.model_dump()
        assert dumped["source"]["kind"] == "semantic"
        reloaded = Slot.model_validate(dumped)
        assert isinstance(reloaded.source, SemanticSource)
        assert reloaded.source.prompt == slot.source.prompt
        assert reloaded.source.coverage_refs == slot.source.coverage_refs

    def test_discriminator_picks_semantic_from_raw_json(self):
        """Raw JSON with ``kind:"semantic"`` resolves to SemanticSource via the union."""
        slot = Slot.model_validate({
            "slot_id": "s.sem",
            "label": "语义",
            "source": {"kind": "semantic", "prompt": "写作指令", "coverage_refs": []},
        })
        assert isinstance(slot.source, SemanticSource)
        assert slot.source.prompt == "写作指令"

    def test_semantic_slot_in_full_template_round_trips(self):
        tpl = ReportTemplate(
            template_id="t-sem",
            sections=[
                Section(
                    section_id="s0",
                    title="分析",
                    prompt="本节行文 prompt",
                    coverage=[OntologyRelationBinding(
                        doc_class_iri=_DRUG, predicate_iri=_MADE_BY, range_class_iri=_MFR,
                    )],
                    groups=[Group(
                        group_id="g0", title="综述", kind="fields",
                        slots=[Slot(slot_id="s0.sem", label="综述", source=SemanticSource())],
                    )],
                )
            ],
        )
        reloaded = ReportTemplate.model_validate(tpl.model_dump())
        sem = reloaded.sections[0].groups[0].slots[0].source
        assert isinstance(sem, SemanticSource)


class TestLegacyTypedSlotsStillParse:
    """Backward compatibility: the five pre-016 source kinds remain valid union members
    so existing ``schema_json`` blobs (incl. the default template) still validate (C1/C2)."""

    def test_default_template_still_validates(self):
        tpl = load_default_template()  # every legacy slot kind round-trips
        kinds = {slot.source.kind for _, _, slot in tpl.iter_slots()}
        assert kinds  # non-empty; the load itself is the assertion
        assert "semantic" not in kinds  # default template is untouched (golden-master)

    def test_each_legacy_kind_round_trips(self):
        for source in (
            ExtractionSource(object_class_iri_contains="DrugProduct", text=True),
            ExtractionSource(object_class_iri_contains="DrugProduct", label="PDE"),
            LLMExtractionSource(object_class_iri=_DRUG, data_property_iri=_MADE_BY, label="x"),
        ):
            slot = Slot(slot_id="s", label="l", source=source)
            reloaded = Slot.model_validate(slot.model_dump())
            assert reloaded.source.kind == source.kind


class TestCoverageKey:
    """``coverage_key`` is the single source of truth for the synthetic slot-id a
    section.coverage binding produces — it MUST stay byte-identical to the strings the
    validator previously inlined, so a semantic slot's ``coverage_refs`` key on the exact
    value the manifest carries."""

    def test_ontology_relation_key_is_short_pred_and_range(self):
        binding = OntologyRelationBinding(
            doc_class_iri=_DRUG, predicate_iri=_MADE_BY, range_class_iri=_MFR,
        )
        assert coverage_key(binding) == "coverage.manufacturedBy__Manufacturer"

    def test_fact_source_key_is_short_source(self):
        # ``_short`` splits on ``#``/``/`` only — a dotted source has no separator, so it
        # is carried whole (matching the string the validator previously inlined).
        binding = FactSourceBinding(source="org.responsible_person", label="责任人")
        assert coverage_key(binding) == "coverage.fact_source.org.responsible_person"

    def test_fact_source_key_shortens_iri_style_source(self):
        binding = FactSourceBinding(source="https://ex.org/facts/ResponsiblePerson")
        assert coverage_key(binding) == "coverage.fact_source.ResponsiblePerson"

    def test_key_is_stable_and_matches_validator_output(self):
        """The key the validator emits into the manifest equals ``coverage_key`` verbatim."""
        from app.services.reporting.coverage_validator import validate_coverage

        binding = OntologyRelationBinding(
            doc_class_iri=_DRUG, predicate_iri=_MADE_BY, range_class_iri=_MFR,
        )
        tpl = ReportTemplate(
            template_id="t-cov",
            sections=[Section(section_id="s0", title="节", groups=[], coverage=[binding])],
        )
        m = validate_coverage(tpl, [], [], engine=None)
        assert any(s.slot_id == coverage_key(binding) for s in m.slots)
