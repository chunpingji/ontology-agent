"""规则式文档级分类 + 全量关系/属性抽取（纯本地、零模型）。

以合成的 ``DocStructure``（章节/表格签名）+ 桩引擎注入本体反查结果，断言：
- ``document_classifier.classify`` 打分择类 / 拒识；
- 各端点 finder 对代表性表格/段落产出正确端点 + 嵌套数据属性 + 子关系；
- ``extract_relationships`` 多端点 + broad-domain 补挂 + 分类门控。

真实反查 + 真实文档端到端见 ``verify_cmc_extraction.py``。
"""

from __future__ import annotations

from collections import defaultdict

import app.services.extraction.relation_extractor as rx
from app.services.extraction import document_classifier
from app.services.extraction.docx_structure import DocSection, DocStructure, DocTable
from app.services.extraction.relation_extractor import (
    CMC_REPORT_IRI,
    DRUG_PRODUCT_IRI,
    EQUIPMENT_IRI,
    _build_class_hierarchy,
    _classify_by_synonyms,
    _Ctx,
    _resolve_strategy,
    extract_relationships,
    find_cleaning,
    find_degradation,
    find_drug_product,
    find_equipment,
    find_production_plan,
    find_quality_risk,
    find_residue,
    find_safety_risk,
    find_shared_line,
    find_storage,
    find_synthesis_route,
)

_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_DESCRIBES_IRI = _DEV + "describes"
_REGDOC_IRI = document_classifier.REGULATORY_DOCUMENT_IRI


# --- 测试夹具：合成文档结构 + 桩引擎 ---------------------------------------
def _mk_table(cells):
    headers = cells[0]
    rows = []
    for raw in cells[1:]:
        row = {}
        for i, val in enumerate(raw):
            key = headers[i] if i < len(headers) and headers[i] else f"col{i}"
            row.setdefault(key, val)
        rows.append(row)
    return DocTable(headers=headers, rows=rows, cells=cells)


def _make_structure() -> DocStructure:
    sections = [
        DocSection("原料药HRS-1234临床备样生产信息", 1, []),
        DocSection("简介", 2, [
            "原料药HRS-1234计划于2026年03月在642/646车间进行临床样品的生产，"
            "本次计划生产1批，用于临床I期试验，预计批量0.42~9.22kg。",
        ]),
        DocSection("产品的基本性质", 2, [
            "制剂剂型：口服速释片剂",
            "给药途径：口服",
            "是否细胞毒药物：否",
            "性状：本品应为白色至黄色粉末",
            "PDE：1.80mg",
        ]),
        DocSection("工艺描述", 3, [
            "本品以起始物料 1234-4 经偶联反应得到中间体 1234-3，"
            "再经脱保护、成盐、酰胺缩合、脱苄基及精制，最终得到成品 HRS-1234。",
        ]),
        DocSection("设备清洗方法", 3, [
            "预清洗：用纯化水冲洗设备内壁",
            "主清洗：配制清洗剂循环清洗",
            "检查：目视检查无可见残留",
        ]),
        DocSection("安全评估", 3, [
            "易燃易爆：使用乙醇等易燃溶剂，需防爆",
        ]),
        DocSection("降解途径", 3, [
            "酸性降解：0.1M HCl 60℃ 24h，降解约5.2%，主要杂质为Imp-A",
            "光降解：ICH 光照条件，稳定，无明显降解",
        ]),
        DocSection("日剂量", 3, [
            "给药方案：每日一次（QD），口服",
            "最高剂量：200mg",
        ]),
    ]
    tables = [
        _mk_table([
            ["名称", "参考得量范围", "参考收率范围"],
            ["1234-3", "0.5~1.0kg", "80~90%"],
            ["HRS-1234粗品", "0.4~0.9kg", "70~85%"],
        ]),
        _mk_table([
            ["步骤", "设备规格", "岗位操作", "材质", "匹配设备", "规格型号", "主残留物"],
            ["1234-3生产", "500L反应釜", "投料", "316L", "RE64202/RE64602", "GLN-500", "1234-4"],
            ["1234-3生产", "离心机", "离心", "316L", "CT64611", "PGZ-1000", "母液"],
        ]),
        _mk_table([
            ["中间体/成品", "包装方式", "存放条件（温度、湿度、是否避光）", "有效期/复测期"],
            ["1234-3", "双层PE袋", "避光，密封，冷藏（2-8℃）保存", "12个月"],
        ]),
        _mk_table([
            ["名称", "物料酸碱性", "溶剂", "温度", "溶解度"],
            ["1234-3", "中性", "乙醇", "25℃", "溶解"],
            ["1234-3", "中性", "丙酮", "25℃", "易溶"],
            ["1234-1", "弱碱", "水", "25℃", "微溶"],
        ]),
        _mk_table([
            ["风险环节", "风险描述", "控制措施"],
            ["投料操作", "粉尘暴露", "佩戴防护并局部排风"],
        ]),
        _mk_table([
            ["风险环节", "质量风险", "控制措施"],
            ["精制", "残留溶剂超标", "延长干燥并检测"],
        ]),
        _mk_table([
            ["参数", "数值"],
            ["NOAEL（大鼠）", "30mg/kg/天"],
            ["F值（安全系数）", "10"],
            ["人体等效剂量（HED）", "4.8mg/kg"],
        ]),
    ]
    paragraphs = [s.heading for s in sections] + [p for s in sections for p in s.paras]
    headings = [s.heading for s in sections]
    return DocStructure(
        title="原料药HRS-1234临床备样生产信息",
        sections=sections,
        tables=tables,
        paragraphs=paragraphs,
        headings=headings,
    )


_CMC_OBJ_PROPS = [
    {"iri": _DESCRIBES_IRI, "name": "describes", "label": "描述", "range": [DRUG_PRODUCT_IRI]},
    {"iri": _DEV + "hasSynthesisRoute", "name": "hasSynthesisRoute", "label": "有合成路线",
     "range": [_DEV + "SynthesisRoute"]},
    {"iri": _DEV + "hasCleaningMethod", "name": "hasCleaningMethod", "label": "有清洁方法",
     "range": ["https://ontology.pharma-gmp.cn/slpra/cleaning/CleaningProcess"]},
    {"iri": _DEV + "hasCleaningResidue", "name": "hasCleaningResidue", "label": "有清洁残留",
     "range": ["https://ontology.pharma-gmp.cn/slpra/drug/Residue"]},
    {"iri": _DEV + "hasSafetyRiskAssessment", "name": "hasSafetyRiskAssessment",
     "label": "有安全风险评估", "range": [_DEV + "SafetyRiskAssessment"]},
    {"iri": _DEV + "hasQualityRiskAssessment", "name": "hasQualityRiskAssessment",
     "label": "有质量风险评估", "range": [_DEV + "QualityRiskAssessment"]},
    {"iri": _DEV + "hasSharedLineData", "name": "hasSharedLineData", "label": "有共线数据",
     "range": [_DEV + "SharedLineAssessmentData"]},
    {"iri": _DEV + "hasProductionPlan", "name": "hasProductionPlan", "label": "含备样生产计划",
     "range": [_DEV + "ClinicalSampleProductionPlan"]},
]


_RANGE_METHOD_MAP = {
    DRUG_PRODUCT_IRI: "section_kv",
    _DEV + "SafetyRiskAssessment": "table_scan",
    _DEV + "QualityRiskAssessment": "table_scan",
    "https://ontology.pharma-gmp.cn/slpra/cleaning/CleaningProcess": "section_kv",
    "https://ontology.pharma-gmp.cn/slpra/drug/Residue": "table_scan",
    _DEV + "SharedLineAssessmentData": "composite",
    _DEV + "SynthesisRoute": "composite",
    _DEV + "ClinicalSampleProductionPlan": "sentence_regex",
    EQUIPMENT_IRI: "table_scan",
    _DEV + "StorageCondition": "table_scan",
    _DEV + "DegradationPathway": "section_paragraph",
}


_RANGE_PROFILE_MAP = {
    DRUG_PRODUCT_IRI: {
        "version": 1,
        "sources": [{
            "locator": "section_kv",
            "anchors": {"any_of": ["产品的基本性质", "基本性质"]},
        }],
        "identity": {
            "pattern": "[A-Z]{2,4}-[0-9]{3,5}",
            "fallback": "药物产品",
        },
        "property_aliases": {
            "appearance": ["性状"],
            "isCytotoxic": ["是否细胞毒药物"],
        },
    },
    EQUIPMENT_IRI: {
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["设备规格", "设备名称"]},
                {"any_of": ["匹配设备", "设备编号"]},
            ]},
        }],
        "identity": {"aliases": ["设备编号", "匹配设备"], "split": "/"},
        "property_aliases": {
            "equipmentID": ["设备编号", "匹配设备"],
            "equipmentName": ["设备名称", "设备规格"],
            "modelSpecification": ["规格型号"],
        },
        "subclass_by": "equipmentName",
    },
    rx.RESIDUE_IRI: {
        "version": 1,
        "sources": [{
            "locator": "table_rows",
            "headers": {"all_of": [
                {"any_of": ["名称", "中间体及成品", "中间体/成品"]},
                {"any_of": ["溶解度"]},
            ]},
        }],
        "identity": {"aliases": ["名称", "中间体及成品", "中间体/成品"]},
        "property_aliases": {
            "residueSolvent": ["溶剂"],
            "residueSolubility": ["溶解度"],
            "residueSolubilityTemperature": ["温度"],
        },
    },
    rx.SHARED_LINE_IRI: {
        "version": 1,
        "sources": [{
            "locator": "table_singleton",
            "headers": {"all_of": [
                {"any_of": ["参数"]},
                {"any_of": ["数值"]},
            ]},
            "orientation": "kv",
            "key_aliases": ["参数"],
            "value_aliases": ["数值"],
        }],
        "identity": {"fallback": "共线评估数据"},
        "property_aliases": {
            "noael_mg_per_kg_per_day": ["NOAEL"],
            "safetyFactor": ["F值", "安全系数"],
            "humanEquivalentDose_mg_per_kg": ["人体等效剂量", "HED"],
        },
    },
}


def _schema_edge(pred_iri, pred_label, range_iri, *, domain=CMC_REPORT_IRI, hop=1):
    """Build a minimal schema edge dict for _FakeEngine.get_relation_schema."""
    method = _RANGE_METHOD_MAP.get(range_iri)
    return {
        "hop": hop,
        "predicate_iri": pred_iri,
        "predicate_label": pred_label,
        "domain_class_iri": domain,
        "domain_class_label": "CMC报告",
        "range_class_iri": range_iri,
        "range_class_label": range_iri.rsplit("/", 1)[-1],
        "range_subclasses": [],
        "range_data_properties": [],
        "range_extraction_hints": {
            "method": method,
            "anchors": [],
            "profile": _RANGE_PROFILE_MAP.get(range_iri),
        },
    }


_EQUIP_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"

_FAKE_EQUIP_SYNONYMS = {
    "反应釜": _EQUIP_NS + "Reactor",
    "反应器": _EQUIP_NS + "Reactor",
    "离心机": _EQUIP_NS + "Centrifuge",
    "鼓风干燥": _EQUIP_NS + "HotAirBlastDryer",
    "旋蒸": _EQUIP_NS + "RotaryEvaporator",
}

_FAKE_DEGRADATION_SYNONYMS = {
    "酸降解": _DEV + "AcidDegradation",
    "酸": _DEV + "AcidDegradation",
    "碱降解": _DEV + "AlkalineDegradation",
    "碱": _DEV + "AlkalineDegradation",
    "氧化降解": _DEV + "OxidativeDegradation",
    "氧化": _DEV + "OxidativeDegradation",
    "光降解": _DEV + "PhotoDegradation",
    "光": _DEV + "PhotoDegradation",
    "热降解": _DEV + "ThermalDegradation",
    "温降解": _DEV + "ThermalDegradation",
    "热": _DEV + "ThermalDegradation",
    "温": _DEV + "ThermalDegradation",
    "湿降解": _DEV + "HumidityDegradation",
    "湿": _DEV + "HumidityDegradation",
}


class _FakeEngine:
    """桩引擎：注入分类候选 + CMCReport schema 反查 + DrugProduct 数据属性。"""

    def get_subclass_synonyms(self, class_iri):
        if class_iri == EQUIPMENT_IRI:
            return dict(_FAKE_EQUIP_SYNONYMS)
        if class_iri == _DEV + "DegradationPathway":
            return dict(_FAKE_DEGRADATION_SYNONYMS)
        return {}

    def get_subclasses(self, class_iri):
        if class_iri != _REGDOC_IRI:
            return []
        return [
            {"iri": CMC_REPORT_IRI, "label": "CMC 报告"},
            {"iri": _DEV + "StabilityStudyReport", "label": "稳定性研究报告"},
        ]

    def get_object_properties_by_domain(self, class_iri):
        return list(_CMC_OBJ_PROPS) if class_iri == CMC_REPORT_IRI else []

    def get_relation_schema(self, class_iri, max_hops=4):
        if class_iri != CMC_REPORT_IRI:
            return []
        edges = []
        for prop in _CMC_OBJ_PROPS:
            for rng in prop.get("range", []):
                edges.append(_schema_edge(prop["iri"], prop["label"], rng))
        edges.append(_schema_edge(
            _DEV + "usesEquipment", "使用设备", EQUIPMENT_IRI))
        edges.append(_schema_edge(
            _DEV + "hasStorageCondition", "存放条件",
            _DEV + "StorageCondition"))
        edges.append(_schema_edge(
            _DEV + "hasDegradationPathway", "含降解途径",
            _DEV + "DegradationPathway"))
        return edges

    def get_data_properties_by_domain(self, class_iri):
        if class_iri == DRUG_PRODUCT_IRI:
            return [
                {"iri": _DEV.replace("drug-development", "drug") + "dosageForm",
                 "name": "dosageForm", "label": "制剂剂型"},
                {"iri": _DEV.replace("drug-development", "drug") + "routeOfAdministration",
                 "name": "routeOfAdministration", "label": "给药途径"},
                {"iri": _DEV.replace("drug-development", "drug") + "isCytotoxic",
                 "name": "isCytotoxic", "label": "是否细胞毒药物",
                 "datatype": "boolean"},
                {"iri": _DEV.replace("drug-development", "drug") + "appearance",
                 "name": "appearance", "label": "性状", "datatype": "string"},
            ]
        if class_iri == EQUIPMENT_IRI:
            return [
                {"iri": _EQUIP_NS + "equipmentID", "name": "equipmentID",
                 "label": "设备编号", "datatype": "string"},
                {"iri": _EQUIP_NS + "equipmentName", "name": "equipmentName",
                 "label": "设备名称", "datatype": "string"},
                {"iri": _EQUIP_NS + "modelSpecification",
                 "name": "modelSpecification", "label": "规格型号",
                 "datatype": "string"},
            ]
        if class_iri == rx.RESIDUE_IRI:
            return [
                {"iri": _DEV + "residueSolvent", "name": "residueSolvent",
                 "label": "残留物清洗溶剂", "datatype": "string"},
                {"iri": _DEV + "residueSolubility", "name": "residueSolubility",
                 "label": "残留物溶解度", "datatype": "string"},
                {"iri": _DEV + "residueSolubilityTemperature",
                 "name": "residueSolubilityTemperature",
                 "label": "溶解度测试温度", "datatype": "string"},
            ]
        if class_iri == rx.SHARED_LINE_IRI:
            return [
                {"iri": _DEV + "noael_mg_per_kg_per_day",
                 "name": "noael_mg_per_kg_per_day",
                 "label": "NOAEL（mg/kg/天）", "datatype": "decimal"},
                {"iri": _DEV + "safetyFactor", "name": "safetyFactor",
                 "label": "安全系数（F值）", "datatype": "decimal"},
                {"iri": _DEV + "humanEquivalentDose_mg_per_kg",
                 "name": "humanEquivalentDose_mg_per_kg",
                 "label": "人体等效剂量（mg/kg）", "datatype": "decimal"},
            ]
        return []

    def get_data_property_patterns(self, class_iri):
        # 模拟 TTL extractionPattern 注解经 rdflib 解析后的字段正则（单捕获组 group(1)=值）。
        # 反斜杠与真实 TTL round-trip 后一致（\\s → \s）；raw-string 等价书写。
        if class_iri == _DEV + "ClinicalSampleProductionPlan":
            return [
                {"iri": _DEV + "plannedProductionDate", "label": "计划生产时间",
                 "pattern": r"计划于\s*(\d{4}年\d{1,2}月)"},
                {"iri": _DEV + "plannedBatchCount", "label": "计划生产批次数",
                 "pattern": r"计划生产\s*(\d+)\s*批"},
                {"iri": _DEV + "productionPurpose", "label": "生产用途",
                 "pattern": r"用于\s*([^，。；]+)"},
                {"iri": _DEV + "plannedBatchSizeMin_kg", "label": "预计批量下限（kg）",
                 "pattern": r"(?:预计)?(?:生产)?批量(?:范围)?\s*([\d.]+)\s*[~～—至\-]"},
                {"iri": _DEV + "plannedBatchSizeMax_kg", "label": "预计批量上限（kg）",
                 "pattern": r"[~～—至\-]\s*([\d.]+)\s*(?:kg|公斤)"},
            ]
        return []

    def get_extraction_hints(self, class_iri):
        if class_iri == _DEV + "ClinicalSampleProductionPlan":
            return {"method": "sentence_regex", "anchors": ["计划", "批", "车间"]}
        return {"method": None, "anchors": []}

    def get_class_detail(self, iri):
        return {"label": iri.rsplit("/", 1)[-1]}

    def get_class_label(self, iri):
        return iri.rsplit("/", 1)[-1] if iri else ""


def _ctx(structure=None):
    return _Ctx(structure=structure or _make_structure(), drug_code="HRS-1234",
                engine=_FakeEngine(), triples=[])


# --- 文档级分类 -------------------------------------------------------------
def test_classify_picks_cmc_report():
    cls = document_classifier.classify(_make_structure(), _FakeEngine())
    assert cls is not None
    assert cls["doc_class_iri"] == CMC_REPORT_IRI
    assert cls["score"] >= 3
    assert any("原料药" == s or "工艺描述" == s for s in cls["signals"])


def test_classify_rejects_unrelated():
    empty = DocStructure("会议纪要", [DocSection("会议纪要", 1, ["与会人员名单"])],
                         [], ["会议纪要", "与会人员名单"], ["会议纪要"])
    assert document_classifier.classify(empty, _FakeEngine()) is None


def test_classify_returns_none_without_candidates():
    class _NoSub(_FakeEngine):
        def get_subclasses(self, class_iri):
            return []
    assert document_classifier.classify(_make_structure(), _NoSub()) is None


def test_classify_scans_cmc_signals_after_first_twelve_paragraphs():
    paragraphs = [f"普通前言段落 {idx}" for idx in range(12)]
    paragraphs.extend([
        "原料药信息",
        "工艺描述",
        "合成路线",
        "设备需求",
        "设备清洗",
        "共线评估",
        "参考得量",
        "参考收率",
        "降解途径",
        "临床备样生产",
        "CMC",
    ])
    structure = DocStructure(
        "内部报告",
        [],
        [],
        paragraphs,
        [],
    )
    result = document_classifier.classify(structure, _FakeEngine())
    assert result is not None
    assert result["doc_class_iri"] == CMC_REPORT_IRI
    assert len(result["signals"]) >= 11


def test_explicit_document_type_builds_authoritative_classification():
    result = document_classifier.classification_for_iri(
        CMC_REPORT_IRI, _FakeEngine()
    )
    assert result is not None
    assert result["doc_class_iri"] == CMC_REPORT_IRI
    assert result["source"] == "explicit"
    assert result["signals"] == ["显式文档类型"]


def test_explicit_document_type_preserves_document_evidence_score():
    result = document_classifier.classification_for_iri(
        CMC_REPORT_IRI, _FakeEngine(), structure=_make_structure()
    )
    assert result["doc_class_iri"] == CMC_REPORT_IRI
    assert result["source"] == "explicit+automatic"
    assert result["score"] >= 3
    assert "原料药" in result["signals"]
    assert "显式文档类型" in result["signals"]


# --- 各端点 finder ----------------------------------------------------------
def test_find_drug_product_maps_kv_to_dprops():
    eps = find_drug_product(_ctx())
    assert len(eps) == 1
    ep = eps[0]
    assert ep["text"] == "HRS-1234"
    labels = {d["label"]: d for d in ep["data_properties"]}
    assert labels["制剂剂型"]["value"] == "口服速释片剂"
    assert labels["制剂剂型"]["iri"]  # 经本体数据属性匹配，带 iri
    assert labels["性状"]["iri"].endswith("appearance")
    assert labels["PDE"]["iri"]  # PDE 手动映射到 pde_mg_per_day


def test_find_drug_product_prefers_typed_endpoint():
    ctx = _ctx()
    ctx.triples = [{"entity_class_iri": DRUG_PRODUCT_IRI, "entity_text": "HRS-1234 片"}]
    ep = find_drug_product(ctx)[0]
    assert ep["source"] == "typed"
    assert ep["text"] == "HRS-1234 片"


def test_find_equipment_dedups_by_primary_code():
    eps = find_equipment(_ctx())
    codes = [e["text"] for e in eps]
    assert codes == ["RE64202", "CT64611"]  # 斜杠取首选编号，按编号去重
    reactor = next(e for e in eps if e["text"] == "RE64202")
    assert reactor["class_iri"].endswith("Reactor")
    centrifuge = next(e for e in eps if e["text"] == "CT64611")
    assert centrifuge["class_iri"].endswith("Centrifuge")


def test_find_synthesis_route_builds_steps_with_subrelations():
    route = find_synthesis_route(_ctx())[0]
    assert any(d["label"] == "工艺描述" and "起始物料" in d["value"]
               for d in route["data_properties"])
    steps = route["sub_relationships"]
    assert len(steps) == 2
    step1 = steps[0]
    assert step1["predicate_label"] == "包含步骤"
    assert step1["object_text"].startswith("步骤1")
    assert any(d["label"] == "收率范围（%）" for d in step1["object_data_properties"])
    # 第一步（1234-3）递归携带 usesEquipment + producesIntermediate 子关系。
    nested = step1["sub_relationships"]
    preds = {s["predicate_label"] for s in nested}
    assert "使用设备" in preds and "产出中间体" in preds
    equip = {s["object_text"] for s in nested if s["predicate_label"] == "使用设备"}
    assert equip == {"RE64202", "CT64611"}
    inter = next(s for s in nested if s["predicate_label"] == "产出中间体")
    assert inter["object_text"] == "1234-3"


def test_find_safety_and_quality_risk():
    safety = find_safety_risk(_ctx())
    # 表格 1 行 + 章节 1 段
    assert len(safety) == 2
    assert any("投料操作" in e["text"] for e in safety)
    assert any("易燃易爆" in e["text"] for e in safety)
    quality = find_quality_risk(_ctx())
    assert len(quality) == 1
    q = quality[0]
    assert {d["label"] for d in q["data_properties"]} >= {"风险环节", "风险描述", "控制措施"}


def test_find_cleaning_steps():
    eps = find_cleaning(_ctx())
    assert {e["text"] for e in eps} == {"预清洗", "主清洗", "检查"}


def test_find_residue_dedups_by_name():
    eps = find_residue(_ctx())
    names = [e["text"] for e in eps]
    assert names == ["1234-3", "1234-1"]  # 1234-3 出现两次 → 去重保留首个


def test_find_storage_light_protection():
    eps = find_storage(_ctx())
    assert len(eps) == 1
    dps = {d["label"]: d["value"] for d in eps[0]["data_properties"]}
    assert dps["是否避光"] == "是"
    assert dps["有效期/复测期"] == "12个月"


def test_find_shared_line_aggregates():
    eps = find_shared_line(_ctx())
    assert len(eps) == 1
    labels = {d["label"] for d in eps[0]["data_properties"]}
    assert "NOAEL（大鼠）" in labels
    assert "给药方案" in labels
    assert "PDE" in labels


def test_find_degradation_classifies_and_parses():
    eps = find_degradation(_ctx())
    acid = next(e for e in eps if "酸" in e["text"])
    assert acid["class_iri"].endswith("AcidDegradation")
    dps = {d["label"]: d["value"] for d in acid["data_properties"]}
    assert dps["降解百分比（%）"] == "5.2"
    assert dps["主要降解杂质"] == "Imp-A"
    photo = next(e for e in eps if "光" in e["text"])
    assert photo["class_iri"].endswith("PhotoDegradation")


def test_find_production_plan_parses_and_splits_workshops():
    eps = find_production_plan(_ctx())
    assert len(eps) == 1
    ep = eps[0]
    assert ep["class_iri"] == rx.CLINICAL_SAMPLE_PLAN_IRI
    assert ep["text"] == "临床备样生产计划"
    dps = {d["label"]: d["value"] for d in ep["data_properties"]}
    assert dps["计划生产时间"] == "2026年03月"
    assert dps["计划生产批次数"] == "1"
    assert dps["生产用途"] == "临床I期试验"
    assert dps["预计批量下限（kg）"] == "0.42"
    assert dps["预计批量上限（kg）"] == "9.22"
    # 「642/646车间」→ 两条 producedInArea 子关系（经 mock 外部车间事实源解析）。
    subs = ep["sub_relationships"]
    assert [s["predicate_label"] for s in subs] == ["生产车间", "生产车间"]
    assert {s["object_text"] for s in subs} == {"642车间", "646车间"}
    assert all(s["object_source"] == "external" for s in subs)
    assert all(s["object_class_iri"] == rx.PRODUCTION_AREA_IRI for s in subs)
    # 车间携外部事实源数据属性（车间编号/洁净区设置/所属厂房/部门代码/…）。
    area_labels = {d["label"] for d in subs[0]["object_data_properties"]}
    assert {"车间编号", "洁净区设置", "所属厂房", "部门代码"} <= area_labels


def test_find_production_plan_missing_sentence_returns_empty():
    empty = DocStructure(
        "报告", [DocSection("报告", 1, ["无计划信息"])], [],
        ["报告", "无计划信息"], ["报告"],
    )
    assert find_production_plan(_ctx(empty)) == []


def test_find_production_plan_fields_are_annotation_driven():
    """字段正则纯由 engine.get_data_property_patterns 提供：注解为空 → 无字段属性，
    但车间子关系仍走外部事实源产出（证明字段抽取零硬编码、由 TTL 驱动）。"""
    class _NoPatterns(_FakeEngine):
        def get_data_property_patterns(self, class_iri):
            return []

    ctx = _Ctx(structure=_make_structure(), drug_code="HRS-1234",
               engine=_NoPatterns(), triples=[])
    eps = find_production_plan(ctx)
    assert len(eps) == 1
    assert eps[0]["data_properties"] == []          # 无注解 → 零字段
    assert len(eps[0]["sub_relationships"]) == 2     # 车间子关系不受影响


def test_find_production_plan_custom_pattern_drives_extraction():
    """替换注解正则即改变抽取结果——无需改 Python（Schema-Driven 核心保证）。"""
    class _CustomPattern(_FakeEngine):
        def get_data_property_patterns(self, class_iri):
            return [{"iri": _DEV + "productionPurpose", "label": "生产用途",
                     "pattern": r"用于(临床[^，。；]*)"}]

    ctx = _Ctx(structure=_make_structure(), drug_code="HRS-1234",
               engine=_CustomPattern(), triples=[])
    dps = {d["label"]: d["value"] for d in find_production_plan(ctx)[0]["data_properties"]}
    assert dps == {"生产用途": "临床I期试验"}


def test_no_hardcoded_plan_field_regexes():
    """字段级 _PLAN_*_RE 常量已删除（仅保留车间跨度正则 _PLAN_AREA_SPAN_RE）。"""
    for dead in ("_PLAN_DATE_RE", "_PLAN_BATCH_RE", "_PLAN_PURPOSE_RE", "_PLAN_SIZE_RE"):
        assert not hasattr(rx, dead), f"{dead} 应已迁至 TTL extractionPattern 注解"
    assert hasattr(rx, "_PLAN_AREA_SPAN_RE")


# --- extract_relationships 总装 --------------------------------------------
def test_extract_relationships_full_graph(monkeypatch):
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: _make_structure())
    graph = extract_relationships(_FakeEngine(), "原料药 HRS-1234.docx", triples=[])
    assert graph["doc_class"]["doc_class_iri"] == CMC_REPORT_IRI
    preds = {e["predicate_iri"].rsplit("/", 1)[-1] for e in graph["relationships"]}
    # schema-driven: 8 domain 边 + 3 曾 broad-domain 边（现有 domain 声明，BFS 自然覆盖）。
    assert "describes" in preds
    assert "usesEquipment" in preds
    assert "hasStorageCondition" in preds
    assert "hasDegradationPathway" in preds
    assert "hasSafetyRiskAssessment" in preds
    assert "hasProductionPlan" in preds
    # equipment 边数 = 去重后设备数。
    equip_edges = [
        e for e in graph["relationships"]
        if e["predicate_iri"].endswith("usesEquipment")
    ]
    assert len(equip_edges) == 2
    assert {e["object_class_iri"].rsplit("/", 1)[-1] for e in equip_edges} == {
        "Reactor", "Centrifuge",
    }


def test_explicit_cmc_iri_drives_root_schema_with_document_evidence(monkeypatch):
    structure = _make_structure()
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: structure)
    classification = document_classifier.classification_for_iri(
        CMC_REPORT_IRI, _FakeEngine(), structure=structure
    )

    graph = extract_relationships(
        _FakeEngine(), "HRS-1234.docx", triples=[], doc_class=classification
    )

    assert graph["doc_class"]["score"] > 0
    assert graph["relationships"]
    assert all(
        edge["subject_class_iri"] == CMC_REPORT_IRI
        for edge in graph["relationships"]
    )


def test_profile_strategy_precedes_and_replaces_drug_class_dispatch(monkeypatch):
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: _make_structure())
    graph = extract_relationships(_FakeEngine(), "HRS-1234.docx", triples=[])
    product = next(
        edge for edge in graph["relationships"]
        if edge["predicate_iri"] == _DESCRIBES_IRI
    )
    props = {
        item["iri"].rsplit("/", 1)[-1]: item["value"]
        for item in product["object_data_properties"]
    }
    assert product["object_source"] == "ontology-profile"
    assert product["object_text"] == "HRS-1234"
    assert props["dosageForm"] == "口服速释片剂"
    assert props["appearance"] == "本品应为白色至黄色粉末"
    assert props["isCytotoxic"] is False
    assert DRUG_PRODUCT_IRI not in rx._METHOD_STRATEGIES["section_kv"]._finders


def test_extract_relationships_gated_by_classification(monkeypatch):
    empty = DocStructure("通知", [DocSection("通知", 1, ["放假通知"])], [],
                         ["通知", "放假通知"], ["通知"])
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: empty)
    graph = extract_relationships(_FakeEngine(), "通知.docx", triples=[])
    assert graph["doc_class"] is None
    assert graph["relationships"] == []


# --- Phase F: annotation dispatch, generic strategies, hierarchy tests --------

def test_resolve_strategy_range_override():
    """_RANGE_OVERRIDES entries take priority over method strategies."""
    edge = _schema_edge(_DEV + "hasSynthesisRoute", "合成路线",
                        _DEV + "SynthesisRoute")
    strategy = _resolve_strategy(edge)
    assert strategy is not None


def test_resolve_strategy_method_dispatch():
    """extractionMethod annotation drives _METHOD_STRATEGIES dispatch."""
    edge = _schema_edge(_DEV + "usesEquipment", "使用设备", EQUIPMENT_IRI)
    strategy = _resolve_strategy(edge)
    assert strategy is not None
    edge_none = {
        "range_class_iri": "https://example.org/Unknown",
        "range_extraction_hints": {"method": None, "anchors": []},
    }
    assert _resolve_strategy(edge_none) is None


def test_resolve_strategy_unknown_method_returns_none():
    edge = {
        "range_class_iri": "https://example.org/Unknown",
        "range_extraction_hints": {"method": "nonexistent_method", "anchors": []},
    }
    assert _resolve_strategy(edge) is None


def test_invalid_profile_does_not_fall_back_to_class_specific_dispatch():
    edge = _schema_edge(_DESCRIBES_IRI, "描述", DRUG_PRODUCT_IRI)
    edge["range_extraction_hints"] = {
        "method": "section_kv",
        "profile": None,
        "profile_error": "invalid JSON",
    }
    strategy = _resolve_strategy(edge)
    assert strategy is not None
    assert strategy.find_endpoints(_ctx(), edge) == []


def test_classify_by_synonyms_exact_match():
    syns = {"反应釜": "iri:Reactor", "离心机": "iri:Centrifuge"}
    assert _classify_by_synonyms("反应釜", syns, "iri:Fallback") == "iri:Reactor"


def test_classify_by_synonyms_substring_match():
    syns = {"酸": "iri:Acid", "碱": "iri:Alkaline"}
    assert _classify_by_synonyms("酸性降解", syns, "iri:Fallback") == "iri:Acid"


def test_classify_by_synonyms_reverse_substring():
    syns = {"鼓风干燥": "iri:Dryer"}
    assert _classify_by_synonyms("鼓风", syns, "iri:Fallback") == "iri:Dryer"


def test_classify_by_synonyms_fallback():
    syns = {"反应釜": "iri:Reactor"}
    assert _classify_by_synonyms("未知设备", syns, "iri:Fallback") == "iri:Fallback"


def test_classify_by_synonyms_none_text():
    syns = {"反应釜": "iri:Reactor"}
    assert _classify_by_synonyms(None, syns, "iri:Fallback") == "iri:Fallback"


def test_build_class_hierarchy_from_schema_edges():
    edges = [
        {
            "range_class_iri": EQUIPMENT_IRI,
            "range_subclasses": [
                {"iri": _EQUIP_NS + "Reactor", "label": "反应器"},
                {"iri": _EQUIP_NS + "Centrifuge", "label": "离心机"},
            ],
        },
        {
            "range_class_iri": _DEV + "DegradationPathway",
            "range_subclasses": [
                {"iri": _DEV + "AcidDegradation", "label": "酸降解"},
            ],
        },
    ]
    hierarchy = _build_class_hierarchy(edges)
    assert EQUIPMENT_IRI in hierarchy.get(_EQUIP_NS + "Reactor", set())
    assert EQUIPMENT_IRI in hierarchy.get(_EQUIP_NS + "Centrifuge", set())
    assert _DEV + "DegradationPathway" in hierarchy.get(_DEV + "AcidDegradation", set())
    assert hierarchy.get("nonexistent", set()) == set()


def test_build_class_hierarchy_empty():
    assert _build_class_hierarchy([]) == {}
    edges = [{"range_class_iri": EQUIPMENT_IRI, "range_subclasses": []}]
    assert _build_class_hierarchy(edges) == {}


def _degradation_self_loop_schema():
    """Schema with a self-referential DegradationPathway edge + populated subclasses.

    Reproduces the production shape that the real ontology emits once
    ``hasDegradationPathway`` declares ``rdfs:domain owl:unionOf(CMCReport,
    DegradationPathway)``: a hop-1 edge CMCReport→DegradationPathway *and* a
    self-loop DegradationPathway→DegradationPathway, both carrying the subclass
    list.  The stock ``_schema_edge`` helper can't express this (it hardcodes
    ``range_subclasses=[]`` and never a self-loop), which is exactly why the
    blowup slipped past the existing suite.
    """
    deg = _DEV + "DegradationPathway"
    has_deg = _DEV + "hasDegradationPathway"
    subclasses = [
        {"iri": _DEV + "AcidDegradation", "label": "酸降解"},
        {"iri": _DEV + "PhotoDegradation", "label": "光降解"},
    ]
    top_edge = _schema_edge(has_deg, "含降解途径", deg)
    top_edge["range_subclasses"] = subclasses
    self_edge = _schema_edge(has_deg, "含降解途径", deg, domain=deg)
    self_edge["range_subclasses"] = subclasses

    edges_by_domain = defaultdict(list)
    edges_by_domain[CMC_REPORT_IRI].append(top_edge)
    edges_by_domain[deg].append(self_edge)
    class_hierarchy = _build_class_hierarchy([top_edge, self_edge])

    structure = DocStructure(
        "原料药HRS-1234",
        [DocSection("降解途径", 3, [
            "酸性降解：0.1M HCl 60℃ 24h，降解约5.2%，主要杂质为Imp-A",
            "光降解：ICH 光照条件，稳定，无明显降解",
        ])],
        [], [], [],
    )
    ctx = _Ctx(structure=structure, drug_code="HRS-1234",
               engine=_FakeEngine(), triples=[])
    return top_edge, edges_by_domain, class_hierarchy, ctx


def test_degradation_self_loop_recursion_is_guarded():
    """Regression: self-referential domain + whole-doc finder must NOT recurse.

    ``find_degradation`` returns every pathway in the document regardless of
    parent, so the ``DegradationPathway ─hasDegradationPathway→
    DegradationPathway`` self-loop would otherwise re-attach every sibling
    pathway under each endpoint, combinatorially up to ``max_depth`` (measured:
    28 spurious nested entries for a 2-pathway doc).  The cycle-guard seeds the
    top call with the producing edge, so on the real (seeded) path the self-loop
    is skipped and endpoints carry zero sub_relationships.
    """
    top_edge, edges_by_domain, class_hierarchy, ctx = _degradation_self_loop_schema()

    # Mirror extract_relationships: dispatch the hop-1 edge, then recurse seeded.
    strategy = _resolve_strategy(top_edge)
    endpoints = strategy.find_endpoints(ctx, top_edge)
    assert len(endpoints) == 2  # 酸性降解, 光降解 — the two real pathways

    seed = frozenset({(top_edge["predicate_iri"], top_edge["range_class_iri"])})
    for ep in endpoints:
        subs = rx._extract_sub_relationships(
            ctx, ep["class_iri"], edges_by_domain, class_hierarchy,
            path_edges=seed,
        )
        assert subs == []  # self-loop on ancestor path → no re-attachment


def test_cycle_guard_engages_on_repeat_not_blanket():
    """The guard breaks the cycle on *repeat*, it does not blanket-block recursion.

    Called WITHOUT the top-level seed, the first self-loop hop is allowed (the
    edge is not yet on the path), producing the sibling pathways one level deep;
    the guard then fires at depth 2 via the propagated ``child_path``, so nothing
    nests deeper.  This distinguishes the cycle-guard from a coarse
    "never recurse into DegradationPathway" rule and proves ``child_path``
    propagation works.
    """
    top_edge, edges_by_domain, class_hierarchy, ctx = _degradation_self_loop_schema()
    strategy = _resolve_strategy(top_edge)
    endpoints = strategy.find_endpoints(ctx, top_edge)

    for ep in endpoints:
        subs = rx._extract_sub_relationships(
            ctx, ep["class_iri"], edges_by_domain, class_hierarchy,
        )
        assert len(subs) == 2  # one hop of siblings allowed before the repeat
        for s in subs:
            assert s["sub_relationships"] == []  # depth-2 repeat is guarded
