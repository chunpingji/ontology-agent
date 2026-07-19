"""017 — constrained ontology-guided Document Extraction Profile contracts."""

from __future__ import annotations

from app.services.extraction.document_profile import (
    DocumentPropertyBinding,
    compile_ontology_bindings,
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
