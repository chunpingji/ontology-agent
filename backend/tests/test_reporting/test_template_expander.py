"""012 T030: Tests for ontology-driven template expansion."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.reporting.ast_template import (
    ExtractionSource,
    Group,
    LLMExtractionSource,
    ReportTemplate,
    Section,
    Slot,
)
from app.services.reporting.template_expander import expand_template_with_ontology


def _minimal_template() -> ReportTemplate:
    return ReportTemplate(
        template_id="test@v1",
        sections=[
            Section(
                section_id="s1",
                title="基本信息",
                groups=[
                    Group(
                        group_id="g1",
                        title="产品",
                        kind="fields",
                        slots=[
                            Slot(
                                slot_id="subject.name",
                                label="产品名称",
                                source=ExtractionSource(
                                    object_class_iri_contains="DrugProduct",
                                    text=True,
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _mock_engine(obj_props: list[dict], data_props_by_range: dict[str, list[dict]], loaded: bool = True):
    engine = MagicMock()
    engine.is_loaded = loaded
    engine.get_object_properties_by_domain.return_value = obj_props
    engine.get_data_properties_by_domain.side_effect = lambda iri: data_props_by_range.get(iri, [])
    return engine


class TestExpandTemplateWithOntology:
    def test_no_expansion_when_engine_not_loaded(self):
        template = _minimal_template()
        engine = _mock_engine([], {}, loaded=False)
        result = expand_template_with_ontology(template, "http://example.org/CMCReport", engine)
        assert result is template

    def test_no_expansion_when_no_doc_class(self):
        template = _minimal_template()
        engine = _mock_engine([], {})
        result = expand_template_with_ontology(template, "", engine)
        assert result is template

    def test_no_expansion_when_no_new_data_props(self):
        template = _minimal_template()
        engine = _mock_engine(
            [{"iri": "http://ex.org/hasPart", "name": "hasPart", "label": "包含", "range": ["http://ex.org/Part"]}],
            {"http://ex.org/Part": []},
        )
        result = expand_template_with_ontology(template, "http://ex.org/Doc", engine)
        assert result is template

    def test_expansion_adds_new_section(self):
        template = _minimal_template()
        engine = _mock_engine(
            [{"iri": "http://ex.org/hasIngredient", "name": "hasIngredient", "label": "含有成分", "range": ["http://ex.org/Ingredient"]}],
            {"http://ex.org/Ingredient": [
                {"iri": "http://ex.org/molecularWeight", "name": "molecularWeight", "label": "分子量"},
                {"iri": "http://ex.org/casNumber", "name": "casNumber", "label": "CAS号"},
            ]},
        )
        result = expand_template_with_ontology(template, "http://ex.org/Doc", engine)

        assert result is not template
        assert len(result.sections) == 2
        expansion = result.sections[-1]
        assert expansion.section_id == "ontology_expansion"
        assert expansion.title == "本体扩展"
        assert len(expansion.groups) == 1

        group = expansion.groups[0]
        assert group.group_id == "ontology_Ingredient"
        assert "含有成分" in group.title
        assert group.kind == "fields"
        assert len(group.slots) == 2

        slot = group.slots[0]
        assert slot.slot_id == "ontology.Ingredient.molecularWeight"
        assert slot.label == "分子量"
        assert isinstance(slot.source, LLMExtractionSource)
        assert slot.source.object_class_iri == "http://ex.org/Ingredient"
        assert slot.source.data_property_iri == "http://ex.org/molecularWeight"
        assert slot.required is False

    def test_dedup_existing_iris(self):
        template = ReportTemplate(
            template_id="test@v1",
            sections=[
                Section(
                    section_id="s1",
                    title="S1",
                    groups=[
                        Group(
                            group_id="g1",
                            title="G1",
                            kind="fields",
                            slots=[
                                Slot(
                                    slot_id="existing",
                                    label="Already here",
                                    source=LLMExtractionSource(
                                        object_class_iri="http://ex.org/Ingredient",
                                        data_property_iri="http://ex.org/molecularWeight",
                                        label="分子量",
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        engine = _mock_engine(
            [{"iri": "http://ex.org/hasIngredient", "name": "hasIngredient", "label": "含有", "range": ["http://ex.org/Ingredient"]}],
            {"http://ex.org/Ingredient": [
                {"iri": "http://ex.org/molecularWeight", "name": "molecularWeight", "label": "分子量"},
                {"iri": "http://ex.org/casNumber", "name": "casNumber", "label": "CAS号"},
            ]},
        )
        result = expand_template_with_ontology(template, "http://ex.org/Doc", engine)

        assert len(result.sections) == 2
        new_group = result.sections[-1].groups[0]
        assert len(new_group.slots) == 1
        assert new_group.slots[0].label == "CAS号"

    def test_original_template_not_mutated(self):
        template = _minimal_template()
        engine = _mock_engine(
            [{"iri": "http://ex.org/hasPart", "name": "hasPart", "label": "部件", "range": ["http://ex.org/Part"]}],
            {"http://ex.org/Part": [
                {"iri": "http://ex.org/weight", "name": "weight", "label": "重量"},
            ]},
        )
        original_sections_count = len(template.sections)
        expand_template_with_ontology(template, "http://ex.org/Doc", engine)
        assert len(template.sections) == original_sections_count

    def test_multiple_range_classes(self):
        template = _minimal_template()
        engine = _mock_engine(
            [
                {"iri": "http://ex.org/p1", "name": "p1", "label": "P1", "range": ["http://ex.org/A"]},
                {"iri": "http://ex.org/p2", "name": "p2", "label": "P2", "range": ["http://ex.org/B"]},
            ],
            {
                "http://ex.org/A": [{"iri": "http://ex.org/a1", "name": "a1", "label": "A1"}],
                "http://ex.org/B": [{"iri": "http://ex.org/b1", "name": "b1", "label": "B1"}],
            },
        )
        result = expand_template_with_ontology(template, "http://ex.org/Doc", engine)
        assert len(result.sections[-1].groups) == 2
