"""017 — constrained ontology-guided Document Extraction Profile contracts."""

from __future__ import annotations

from app.services.extraction.document_profile import (
    DocumentPropertyBinding,
    _assign_columns,
    _binding_for_key,
    _match_quality,
    compile_ontology_bindings,
    normalize_source_key,
    parse_profile,
    read_document_profile,
)
from app.services.extraction.docx_structure import DocSection, DocStructure, DocTable

_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"


class _Engine:
    def get_data_properties_by_domain(self, class_iri):
        if class_iri == _DRUG + "DrugProduct":
            return [
                {
                    "iri": _DRUG + "dosageForm",
                    "name": "dosageForm",
                    "label": "制剂剂型",
                    "datatype": "string",
                    "aliases": ["制剂剂型"],
                },
                {
                    "iri": _DRUG + "isHighlySensitizing",
                    "name": "isHighlySensitizing",
                    "label": "是否高致敏药物",
                    "datatype": "boolean",
                    "aliases": ["是否高致敏药物"],
                },
            ]
        return []


def _structure(*, sections=None, tables=None, paragraphs=None):
    sections = sections or []
    tables = tables or []
    paragraphs = paragraphs or [p for sec in sections for p in sec.paras]
    return DocStructure(
        title="原料药 HRS-1597 临床备样生产信息",
        sections=sections,
        tables=tables,
        paragraphs=paragraphs,
        headings=[s.heading for s in sections],
    )


def _table(headers, rows, table_index=0):
    cells = [headers, *rows]
    return DocTable(
        headers=headers,
        rows=[dict(zip(headers, row)) for row in rows],
        cells=cells,
        table_index=table_index,
        header_row_count=1,
        row_indices=list(range(1, len(rows) + 1)),
    )


def test_equipment_choice_expression_expands_to_independent_candidates():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [["设备编号", "匹配设备"]]},
        }],
        "identity": {"aliases": ["设备编号", "匹配设备"], "split": "/"},
    })
    identifier_iri = _EQUIP + "equipmentCode"
    bindings = [DocumentPropertyBinding(
        property_iri=identifier_iri,
        label="设备编号",
        aliases=("设备编号", "匹配设备"),
        is_identifier=True,
    )]
    structure = _structure(tables=[
        _table(["匹配设备"], [["PF64216或PF64616"]])
    ])

    result = read_document_profile(
        structure, _EQUIP + "Equipment", profile, bindings
    )

    assert [candidate.identifier for candidate in result.candidates] == [
        "PF64216", "PF64616",
    ]
    assert {candidate.candidate_group for candidate in result.candidates} == {
        "PF64216|PF64616"
    }
    assert all(
        candidate.values[0].value == candidate.identifier
        for candidate in result.candidates
    )


def test_section_profile_normalizes_property_alias_and_program_identity():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "section_kv",
            "anchors": {"any_of": ["产品的基本性质", "基本性质"]},
            "endpoint_mode": "singleton",
        }],
        "identity": {
            "pattern": "[A-Z]{2,4}-[0-9]{3,5}",
            "fallback": "药物产品",
        },
        "property_aliases": {
            "dosageForm": ["剂型"],
            "isHighlySensitizing": ["是否是高致敏药物"],
        },
    })
    bindings = compile_ontology_bindings(
        _Engine(), _DRUG + "DrugProduct", profile
    )
    structure = _structure(sections=[
        DocSection(
            "产品的基本性质",
            2,
            ["1. 制剂剂型：口服速释片剂", "是否是高致敏药物：否"],
            heading_index=5,
            para_indices=[6, 7],
        ),
    ])

    result = read_document_profile(
        structure, _DRUG + "DrugProduct", profile, bindings
    )
    assert result.degraded_reason is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.identifier == "HRS-1597"
    props = {v.property_iri: v for v in candidate.values}
    assert props[_DRUG + "dosageForm"].value == "口服速释片剂"
    assert props[_DRUG + "isHighlySensitizing"].value is False
    assert props[_DRUG + "isHighlySensitizing"].source_ref["paragraph_index"] == 7


def test_section_profile_reads_descendants_until_next_sibling_section():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "section_kv",
            "anchors": {"any_of": ["产品的基本性质"]},
        }],
        "identity": {"pattern": "[A-Z]{2,4}-[0-9]{3,5}"},
    })
    bindings = compile_ontology_bindings(
        _Engine(), _DRUG + "DrugProduct", profile
    )
    structure = _structure(
        sections=[
            DocSection("产品的基本性质", 1, [], heading_index=5),
            DocSection(
                "产品的结构",
                2,
                ["制剂剂型：口服片剂", "是否是高致敏药物：否"],
                heading_index=6,
                para_indices=[7, 8],
            ),
            DocSection(
                "工艺",
                1,
                ["制剂剂型：不应跨越同级章节"],
                heading_index=9,
                para_indices=[10],
            ),
        ],
        paragraphs=["HRS-1597"],
    )

    result = read_document_profile(
        structure, _DRUG + "DrugProduct", profile, bindings
    )

    assert len(result.candidates) == 1
    props = {value.property_iri: value for value in result.candidates[0].values}
    assert props[_DRUG + "dosageForm"].value == "口服片剂"
    assert props[_DRUG + "isHighlySensitizing"].value is False
    assert props[_DRUG + "dosageForm"].source_ref["section"] == "产品的结构"


def test_equipment_header_alias_groups_are_or_within_and():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["设备规格", "设备名称"]},
                {"any_of": ["匹配设备", "设备编号"]},
            ]},
        }],
        "identity": {"aliases": ["设备编号", "匹配设备"], "split": "/"},
    })
    bindings = [
        DocumentPropertyBinding(
            _EQUIP + "equipmentName", "设备名称", ("设备名称", "设备规格")
        ),
        DocumentPropertyBinding(
            _EQUIP + "equipmentID", "设备编号", ("设备编号", "匹配设备"),
            is_identifier=True,
        ),
        DocumentPropertyBinding(
            _EQUIP + "modelSpecification", "规格型号", ("规格型号",)
        ),
    ]
    old = _table(
        ["设备规格", "匹配设备", "规格型号"],
        [["500L反应釜", "RE64202/RE64602", "GLN-500"]],
    )
    new = _table(
        ["设备名称", "设备编号", "规格型号"],
        [["500L反应釜", "RE64202", "GLN-500"]],
        table_index=1,
    )
    result = read_document_profile(
        _structure(tables=[old, new]), _EQUIP + "Equipment", profile, bindings
    )
    assert [c.identifier for c in result.candidates] == ["RE64202", "RE64202"]
    assert {c.source_ref["table"] for c in result.candidates} == {0, 1}


def test_residue_identity_accepts_three_column_aliases():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["名称", "中间体及成品", "中间体/成品"]},
                {"any_of": ["溶解度"]},
            ]},
        }],
        "identity": {"aliases": ["名称", "中间体及成品", "中间体/成品"]},
    })
    binding = DocumentPropertyBinding(
        _DEV + "residueSolubility", "残留物溶解度", ("溶解度",)
    )
    tables = [
        _table([name, "溶解度"], [[f"物料-{idx}", "易溶"]], idx)
        for idx, name in enumerate(["名称", "中间体及成品", "中间体/成品"])
    ]
    result = read_document_profile(
        _structure(tables=tables), _DRUG + "Residue", profile, [binding]
    )
    assert [c.identifier for c in result.candidates] == ["物料-0", "物料-1", "物料-2"]


def test_wide_toxicology_rows_preserve_pairing_and_strict_pde():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["活性成分(API)", "API"]},
                {"any_of": ["试验项目"]},
                {"any_of": ["NOAEL"]},
                {"any_of": ["PDE (mg/天)", "PDE"]},
            ]},
        }],
        "identity": {"fields": ["activeIngredient", "studyType"]},
    })
    bindings = [
        DocumentPropertyBinding(_DEV + "activeIngredient", "活性成分", ("活性成分(API)", "API")),
        DocumentPropertyBinding(_DEV + "studyType", "试验项目", ("试验项目",)),
        DocumentPropertyBinding(
            _DEV + "noael_mg_per_kg_per_day", "NOAEL", ("NOAEL",),
            transform_type="cast", transform_config={
                "to": "decimal", "number_pattern": "[-+]?[0-9]+(?:[.][0-9]+)?",
            },
        ),
        DocumentPropertyBinding(
            _DEV + "pde_mg_per_day", "PDE", ("PDE (mg/天)", "PDE"),
            transform_type="cast", transform_config={
                "to": "decimal",
                "number_pattern": "[-+]?[0-9]+(?:[.][0-9]+)?",
                "accepted_units": ["mg/天", "mg/day"],
                "reject_invalid": True,
            },
            reject_invalid=True,
        ),
        DocumentPropertyBinding(_DEV + "f1", "F1", ("F1",), transform_type="cast",
                                transform_config={"to": "decimal"}),
        DocumentPropertyBinding(_DEV + "sourceReference", "来源", ("来源",)),
        DocumentPropertyBinding(_DEV + "oebBand", "OEB", ("OEB",)),
    ]
    headers = ["活性成分(API)", "试验项目", "NOAEL", "F1", "PDE (mg/天)", "来源", "OEB"]
    table = _table(headers, [
        ["HRS-1597", "大鼠28天", "30", "5", "100 mg/天", "研究A", "OEB 3"],
        ["HRS-5678", "犬90天", "12.5", "2", "7.5 mg/天", "研究B", "OEB 4"],
    ], table_index=4)
    result = read_document_profile(
        _structure(tables=[table]), _DEV + "SharedLineAssessmentData", profile, bindings
    )

    assert [c.identifier for c in result.candidates] == [
        "HRS-1597 / 大鼠28天", "HRS-5678 / 犬90天"
    ]
    first = {v.property_iri: v.value for v in result.candidates[0].values}
    second = {v.property_iri: v.value for v in result.candidates[1].values}
    assert first[_DEV + "pde_mg_per_day"] == 100.0
    assert first[_DEV + "sourceReference"] == "研究A"
    assert second[_DEV + "pde_mg_per_day"] == 7.5
    assert second[_DEV + "sourceReference"] == "研究B"

    definition = _table(
        ["活性成分(API)", "试验项目", "NOAEL", "PDE"],
        [["HRS-1597", "定义", "30", "允许日暴露量"]],
    )
    rejected = read_document_profile(
        _structure(tables=[definition]), _DEV + "SharedLineAssessmentData",
        profile, bindings,
    )
    props = {v.property_iri for v in rejected.candidates[0].values}
    assert _DEV + "pde_mg_per_day" not in props
    assert any("PDE" in note for note in rejected.candidates[0].notes)


def test_table_singleton_kv_supports_legacy_toxicology_shape():
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_singleton",
            "headers": {"all_of": [
                {"any_of": ["参数"]}, {"any_of": ["数值"]},
            ]},
            "orientation": "kv",
            "key_aliases": ["参数"],
            "value_aliases": ["数值"],
        }],
        "identity": {"fallback": "共线评估数据"},
        "property_aliases": {
            "noael_mg_per_kg_per_day": ["NOAEL"],
        },
    })
    bindings = [
        DocumentPropertyBinding(
            _DEV + "noael_mg_per_kg_per_day",
            "NOAEL（mg/kg/天）",
            ("NOAEL",),
        )
    ]
    table = _table(["参数", "数值"], [["NOAEL（大鼠）", "30mg/kg/天"]])
    result = read_document_profile(
        _structure(tables=[table]), _DEV + "SharedLineAssessmentData", profile, bindings
    )
    assert result.candidates[0].identifier == "共线评估数据"
    assert result.candidates[0].values[0].raw_value == "30mg/kg/天"


def test_numeric_questionnaire_headers_do_not_match_domain_table_profiles():
    """Numbered questionnaire columns must not behave as wildcard headers."""
    safety_questionnaire = _table(
        [
            "1",
            "反应是否为异常放热，反应速率异常的应在表格下方文字说明。",
            "是 / 否",
            "否",
        ],
        [["2", "反应是否产生大量气体？", "是 / 否", "否"]],
        table_index=8,
    )
    profiles = [
        parse_profile({
            "version": 1,
            "sources": [{
                "locator": "table_rows",
                "headers": {"all_of": [
                    {"any_of": ["设备规格", "设备名称"]},
                    {"any_of": ["匹配设备", "设备编号"]},
                ]},
            }],
        }),
        parse_profile({
            "version": 1,
            "sources": [{
                "locator": "table_rows",
                "headers": {"all_of": [
                    {"any_of": ["名称", "中间体及成品", "中间体/成品"]},
                    {"any_of": ["溶解度", "溶解性"]},
                ]},
            }],
        }),
        parse_profile({
            "version": 1,
            "sources": [
                {
                    "locator": "table_rows",
                    "headers": {"all_of": [
                        {"any_of": ["活性成分(API)", "活性成分", "API"]},
                        {"any_of": ["试验项目", "试验类型", "毒理研究"]},
                    ]},
                },
                {
                    "locator": "table_singleton",
                    "headers": {"all_of": [
                        {"any_of": ["参数"]},
                        {"any_of": ["数值", "值"]},
                    ]},
                    "orientation": "kv",
                    "key_aliases": ["参数"],
                    "value_aliases": ["数值", "值"],
                },
            ],
        }),
    ]

    structure = _structure(tables=[safety_questionnaire])
    for profile in profiles:
        result = read_document_profile(
            structure, _DEV + "UnrelatedDomainClass", profile, []
        )
        assert result.candidates == []


def test_composite_alias_does_not_steal_single_column():
    """Regression: composite alias 'NOAEL动物种属' must NOT grab a bare 'NOAEL' column.

    When the table has only one NOAEL column, the exact-match binding (noael)
    must win; species/duration bindings whose aliases merely contain 'NOAEL' as
    a reverse substring must be excluded by the mutual-exclusion column assigner.
    """
    profile = parse_profile({
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["NOAEL"]},
                {"any_of": ["PDE", "PDE (mg/天)"]},
            ]},
        }],
    })
    bindings = [
        DocumentPropertyBinding(
            _DEV + "noael_mg_per_kg_per_day", "NOAEL", ("NOAEL", "NOAEL（大鼠）", "NOAEL（犬）"),
            transform_type="cast", transform_config={"to": "decimal",
                "number_pattern": r"[-+]?\d*\.?\d+"},
        ),
        DocumentPropertyBinding(
            _DEV + "noaelSpecies", "种属", ("动物种属", "种属", "NOAEL动物种属"),
        ),
        DocumentPropertyBinding(
            _DEV + "noaelDuration", "试验周期", ("试验周期", "给药周期", "NOAEL试验周期"),
        ),
        DocumentPropertyBinding(
            _DEV + "pde_mg_per_day", "PDE", ("PDE (mg/天)", "PDE"),
            transform_type="cast", transform_config={"to": "decimal",
                "number_pattern": r"[-+]?\d*\.?\d+"},
        ),
    ]
    table = _table(
        ["NOAEL", "PDE (mg/天)"],
        [["1000", "100"]],
    )
    result = read_document_profile(
        _structure(tables=[table]), _DEV + "SharedLineAssessmentData",
        profile, bindings,
    )
    assert len(result.candidates) == 1
    props = {v.property_iri: v for v in result.candidates[0].values}
    assert _DEV + "noael_mg_per_kg_per_day" in props
    assert props[_DEV + "noael_mg_per_kg_per_day"].value == 1000.0
    assert _DEV + "pde_mg_per_day" in props
    assert _DEV + "noaelSpecies" not in props
    assert _DEV + "noaelDuration" not in props


def test_match_quality_tiers():
    """Unit: _match_quality returns correct tier and specificity."""
    n = normalize_source_key
    assert _match_quality(n("NOAEL"), n("NOAEL")) == (3, len(n("NOAEL")))
    q_rev = _match_quality(n("NOAEL"), n("NOAEL动物种属"))
    assert q_rev == (1, len(n("NOAEL")))
    assert _match_quality(n("NOAEL 动物种属"), n("NOAEL")) == (2, len(n("NOAEL")))
    assert _match_quality(n("溶解度"), n("溶解度")) == (3, len(n("溶解度")))
    assert _match_quality(n("无关"), n("NOAEL")) is None


def test_binding_for_key_specificity():
    """Singleton-kv: parameter 'NOAEL动物种属' must bind to species (exact), not noael (forward)."""
    noael_binding = DocumentPropertyBinding(
        _DEV + "noael_mg_per_kg_per_day", "NOAEL", ("NOAEL",),
    )
    species_binding = DocumentPropertyBinding(
        _DEV + "noaelSpecies", "种属", ("动物种属", "NOAEL动物种属"),
    )
    result = _binding_for_key("NOAEL动物种属", [noael_binding, species_binding])
    assert result is species_binding

    result2 = _binding_for_key("NOAEL", [noael_binding, species_binding])
    assert result2 is noael_binding


def test_assign_columns_column_side_tie_abstains():
    """Two bindings with identical aliases competing for the same column → neither wins."""
    b1 = DocumentPropertyBinding(_DEV + "prop1", "P1", ("A",))
    b2 = DocumentPropertyBinding(_DEV + "prop2", "P2", ("A",))
    assignment = _assign_columns(["A"], [b1, b2])
    assert assignment == {}


def test_assign_columns_no_degradation_after_ambiguity():
    """Binding whose best-tier candidates are tied must NOT fall back to a lower tier."""
    b = DocumentPropertyBinding(_DEV + "prop", "P", ("A", "XY"))
    assignment = _assign_columns(["AB", "AC", "X"], [b])
    assert assignment == {}


def test_assign_columns_order_invariant():
    """Column assignment must not depend on binding list order."""
    noael = DocumentPropertyBinding(_DEV + "noael", "NOAEL", ("NOAEL",))
    species = DocumentPropertyBinding(_DEV + "species", "种属", ("NOAEL动物种属",))
    a1 = _assign_columns(["NOAEL"], [noael, species])
    a2 = _assign_columns(["NOAEL"], [species, noael])
    assert a1[0] == "NOAEL"
    assert 1 not in a1
    assert a2[1] == "NOAEL"
    assert 0 not in a2
