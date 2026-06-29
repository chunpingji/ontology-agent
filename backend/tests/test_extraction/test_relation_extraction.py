"""规则式文档级分类 + 全量关系/属性抽取（纯本地、零模型）。

以合成的 ``DocStructure``（章节/表格签名）+ 桩引擎注入本体反查结果，断言：
- ``document_classifier.classify`` 打分择类 / 拒识；
- 各端点 finder 对代表性表格/段落产出正确端点 + 嵌套数据属性 + 子关系；
- ``extract_relationships`` 多端点 + broad-domain 补挂 + 分类门控。

真实反查 + 真实文档端到端见 ``verify_cmc_extraction.py``。
"""

from __future__ import annotations

import app.services.extraction.relation_extractor as rx
from app.services.extraction.docx_structure import DocSection, DocStructure, DocTable
from app.services.extraction import document_classifier
from app.services.extraction.relation_extractor import (
    CMC_REPORT_IRI,
    DRUG_PRODUCT_IRI,
    EQUIPMENT_IRI,
    _Ctx,
    extract_relationships,
    find_cleaning,
    find_degradation,
    find_drug_product,
    find_equipment,
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
]


class _FakeEngine:
    """桩引擎：注入分类候选 + CMCReport 对象属性反查 + DrugProduct 数据属性。"""

    def get_subclasses(self, class_iri):
        if class_iri != _REGDOC_IRI:
            return []
        return [
            {"iri": CMC_REPORT_IRI, "label": "CMC 报告"},
            {"iri": _DEV + "StabilityStudyReport", "label": "稳定性研究报告"},
        ]

    def get_object_properties_by_domain(self, class_iri):
        return list(_CMC_OBJ_PROPS) if class_iri == CMC_REPORT_IRI else []

    def get_data_properties_by_domain(self, class_iri):
        if class_iri == DRUG_PRODUCT_IRI:
            return [
                {"iri": _DEV.replace("drug-development", "drug") + "dosageForm",
                 "name": "dosageForm", "label": "制剂剂型"},
                {"iri": _DEV.replace("drug-development", "drug") + "routeOfAdministration",
                 "name": "routeOfAdministration", "label": "给药途径"},
                {"iri": _DEV.replace("drug-development", "drug") + "isCytotoxic",
                 "name": "isCytotoxic", "label": "是否细胞毒药物"},
            ]
        return []

    def get_class_detail(self, iri):
        return {"label": iri.rsplit("/", 1)[-1]}


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


# --- 各端点 finder ----------------------------------------------------------
def test_find_drug_product_maps_kv_to_dprops():
    eps = find_drug_product(_ctx())
    assert len(eps) == 1
    ep = eps[0]
    assert ep["text"] == "HRS-1234"
    labels = {d["label"]: d for d in ep["data_properties"]}
    assert labels["制剂剂型"]["value"] == "口服速释片剂"
    assert labels["制剂剂型"]["iri"]  # 经本体数据属性匹配，带 iri
    assert labels["性状"]["iri"] is None  # 无对应数据属性 → raw
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


# --- extract_relationships 总装 --------------------------------------------
def test_extract_relationships_full_graph(monkeypatch):
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: _make_structure())
    graph = extract_relationships(_FakeEngine(), "原料药 HRS-1234.docx", triples=[])
    assert graph["doc_class"]["doc_class_iri"] == CMC_REPORT_IRI
    preds = {e["predicate_iri"].rsplit("/", 1)[-1] for e in graph["relationships"]}
    # domain 反查 7 条 + broad-domain 补挂 3 条（usesEquipment/storage/degradation）。
    assert "describes" in preds
    assert "usesEquipment" in preds
    assert "hasStorageCondition" in preds
    assert "hasDegradationPathway" in preds
    assert "hasSafetyRiskAssessment" in preds
    # equipment 边数 = 去重后设备数。
    equip_edges = [e for e in graph["relationships"]
                   if e["object_class_iri"] == EQUIPMENT_IRI]
    assert len(equip_edges) == 2


def test_extract_relationships_gated_by_classification(monkeypatch):
    empty = DocStructure("通知", [DocSection("通知", 1, ["放假通知"])], [],
                         ["通知", "放假通知"], ["通知"])
    monkeypatch.setattr(rx, "parse_docx_structure", lambda _p: empty)
    graph = extract_relationships(_FakeEngine(), "通知.docx", triples=[])
    assert graph["doc_class"] is None
    assert graph["relationships"] == []
