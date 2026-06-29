"""规则式关系（对象属性）抽取 —— 从研发文档重建本体的对象属性图 + 内容子图。

三阶段 NER（``document_annotator``）只产出「已归类实体 span + 数据属性三元组」，没有
文档级分类、没有对象属性/关系边，故无法重建本体里 CMCReport 那张对象属性图
（``describes`` / ``hasSynthesisRoute`` / ``usesEquipment`` …）。本模块是 ``annotate_word``
之上的**纯后处理器**，离线、确定性、表格/章节感知，分三步补出这张图：

1. **文档级分类**（``document_classifier``）：标题/章节/TOC 打分 → 文档类型（如 CMCReport）。
2. **本体驱动分发**：``engine.get_object_properties_by_domain(doc_class)`` 反查该类对象属性
   （绕过 owlready2 ``get_class_properties`` bug）+ 显式补挂 broad-domain 属性（本体未声明
   domain 的 ``usesEquipment`` / ``hasStorageCondition`` / ``hasDegradationPathway``）。
3. **端点 finder**：按对象属性的 ``range`` 查 finder，每个 finder 返回**一到多个**端点；
   每端点可带**嵌套数据属性**（``object_data_properties``）与**子关系**（``sub_relationships``，
   如合成步骤→设备/中间体），并附**溯源**（``source_ref``）。

数据属性回填复用 ``engine.get_data_properties_by_domain``（与三阶段阶段三同口径）；表格按
**表头签名**定位（题注在该文档不可靠）。全程无新增模型调用，开销可忽略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.extraction import document_classifier
from app.services.extraction.docx_structure import (
    DocStructure,
    parse_docx_structure,
)

# --- 本体 IRI（与 slpra-*.ttl 一致）----------------------------------------
_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
_EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"
_CLEAN = "https://ontology.pharma-gmp.cn/slpra/cleaning/"

CMC_REPORT_IRI = _DEV + "CMCReport"
DRUG_PRODUCT_IRI = _DRUG + "DrugProduct"
SYNTHESIS_ROUTE_IRI = _DEV + "SynthesisRoute"
SYNTHESIS_STEP_IRI = _DEV + "SynthesisStep"
PROCESS_INTERMEDIATE_IRI = _DEV + "ProcessIntermediate"
CRUDE_PRODUCT_IRI = _DEV + "CrudeProduct"
EQUIPMENT_IRI = _EQUIP + "Equipment"
PROCESS_EQUIPMENT_IRI = _EQUIP + "ProcessEquipment"
SAFETY_RISK_IRI = _DEV + "SafetyRiskAssessment"
QUALITY_RISK_IRI = _DEV + "QualityRiskAssessment"
CLEANING_PROCESS_IRI = _CLEAN + "CleaningProcess"
RESIDUE_IRI = _DRUG + "Residue"
SHARED_LINE_IRI = _DEV + "SharedLineAssessmentData"
STORAGE_CONDITION_IRI = _DEV + "StorageCondition"
DEGRADATION_PATHWAY_IRI = _DEV + "DegradationPathway"

# 对象属性 IRI（broad-domain 补挂用，本体未声明 rdfs:domain → 反查不到）。
USES_EQUIPMENT_IRI = _DEV + "usesEquipment"
HAS_STORAGE_CONDITION_IRI = _DEV + "hasStorageCondition"
HAS_DEGRADATION_PATHWAY_IRI = _DEV + "hasDegradationPathway"
PRODUCES_INTERMEDIATE_IRI = _DEV + "producesIntermediate"

# 数据属性 IRI（多数内容类的 dprop 本体未声明 domain，无法经 by_domain 反查 → 直引常量）。
DP = {
    "processDescription": _DEV + "processDescription",
    "stepOrder": _DEV + "stepOrder",
    "yieldRangePercent": _DEV + "yieldRangePercent",
    "outputMassRange_kg": _DEV + "outputMassRange_kg",
    "riskCategory": _DEV + "riskCategory",
    "riskDescription": _DEV + "riskDescription",
    "controlMeasure": _DEV + "controlMeasure",
    "residueSolvent": _DEV + "residueSolvent",
    "residueSolubility": _DEV + "residueSolubility",
    "residueSolubilityTemperature": _DEV + "residueSolubilityTemperature",
    "storageTemperature": _DEV + "storageTemperature",
    "storageHumidity": _DEV + "storageHumidity",
    "lightProtection": _DEV + "lightProtection",
    "packagingType": _DEV + "packagingType",
    "shelfLife": _DEV + "shelfLife",
    "degradationCondition": _DEV + "degradationCondition",
    "degradationPercent": _DEV + "degradationPercent",
    "majorDegradant": _DEV + "majorDegradant",
    "noael_mg_per_kg_per_day": _DEV + "noael_mg_per_kg_per_day",
    "noaelSpecies": _DEV + "noaelSpecies",
    "noaelDuration": _DEV + "noaelDuration",
    "safetyFactor": _DEV + "safetyFactor",
    "humanEquivalentDose_mg_per_kg": _DEV + "humanEquivalentDose_mg_per_kg",
    "proposedStartingDose_mg": _DEV + "proposedStartingDose_mg",
    "proposedMaxDose_mg": _DEV + "proposedMaxDose_mg",
    "dosingRegimen": _DEV + "dosingRegimen",
    "pde_mg_per_day": _DRUG + "pde_mg_per_day",
}

# broad-domain 对象属性（本体未声明 domain）→ 对 CMCReport 显式补挂。
_SUPPLEMENTAL_CMC_PROPS = [
    {"iri": USES_EQUIPMENT_IRI, "label": "使用设备", "name": "usesEquipment",
     "range": [EQUIPMENT_IRI]},
    {"iri": HAS_STORAGE_CONDITION_IRI, "label": "存放条件", "name": "hasStorageCondition",
     "range": [STORAGE_CONDITION_IRI]},
    {"iri": HAS_DEGRADATION_PATHWAY_IRI, "label": "含降解途径", "name": "hasDegradationPathway",
     "range": [DEGRADATION_PATHWAY_IRI]},
]

# 设备规格关键词 → Equipment 本体子类。
_EQUIP_CLASS_BY_SPEC = [
    ("反应釜", _EQUIP + "Reactor"),
    ("反应器", _EQUIP + "Reactor"),
    ("离心机", _EQUIP + "Centrifuge"),
    ("鼓风干燥", _EQUIP + "HotAirBlastDryer"),
    ("旋蒸", _EQUIP + "RotaryEvaporator"),
]

# 降解途径前缀关键词 → DegradationPathway 本体子类。
_DEGRADATION_CLASS = [
    ("酸", _DEV + "AcidDegradation"),
    ("碱", _DEV + "AlkalineDegradation"),
    ("氧化", _DEV + "OxidativeDegradation"),
    ("光", _DEV + "PhotoDegradation"),
    ("热", _DEV + "ThermalDegradation"),
    ("温", _DEV + "ThermalDegradation"),
    ("湿", _DEV + "HumidityDegradation"),
]

# 药物程序代号：HRS-1234 / ABC-12345 一类「字母前缀-数字」编号。
_DRUG_CODE_RE = re.compile(r"[A-Z]{2,4}-\d{3,5}")
_KV_RE = re.compile(r"^\s*([^：:]{1,40})[：:]\s*(.*)$")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_IMP_RE = re.compile(r"(Imp[-‐]?\w+)", re.IGNORECASE)


# --- 抽取上下文 ------------------------------------------------------------
@dataclass
class _Ctx:
    structure: DocStructure
    drug_code: str
    engine: object
    triples: list[dict]
    label_cache: dict = field(default_factory=dict)
    _dp_maps: dict = field(default_factory=dict)

    def class_label(self, iri: str) -> str:
        if iri in self.label_cache:
            return self.label_cache[iri]
        lbl = None
        try:
            detail = self.engine.get_class_detail(iri)
            if detail:
                lbl = detail.label_zh or detail.label_en
        except Exception:
            pass
        lbl = lbl or iri.rsplit("/", 1)[-1]
        self.label_cache[iri] = lbl
        return lbl

    def dp_label_map(self, class_iri: str) -> dict[str, str]:
        """``{数据属性标签 → iri}``（含去单位归一键），供键值/列名回填匹配。"""
        if class_iri in self._dp_maps:
            return self._dp_maps[class_iri]
        out: dict[str, str] = {}
        try:
            for p in self.engine.get_data_properties_by_domain(class_iri):
                lbl = p.get("label") or p.get("name")
                if lbl:
                    out[lbl] = p["iri"]
                    out[_strip_unit(lbl)] = p["iri"]
        except Exception:
            pass
        self._dp_maps[class_iri] = out
        return out


# --- 小工具 ----------------------------------------------------------------
def _strip_unit(label: str) -> str:
    """去掉标签尾部括号单位：``"最小治疗剂量 (mg)"`` → ``"最小治疗剂量"``。"""
    return re.sub(r"\s*[（(].*?[)）]\s*$", "", label or "").strip()


def _split_kv(text: str) -> tuple[str, str] | None:
    m = _KV_RE.match(text or "")
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def _row_get(row: dict, *needles: str, default: str = "") -> str:
    for k, v in row.items():
        if any(n in k for n in needles):
            return v or default
    return default


def _pct(text: str) -> str | None:
    m = _PCT_RE.search(text or "")
    return m.group(1) if m else None


def _impurity(text: str) -> str | None:
    m = _IMP_RE.search(text or "")
    if m:
        return m.group(1)
    m = re.search(r"杂质[为:：]?\s*([^\s，。;；]+)", text or "")
    return m.group(1) if m else None


def _dp(iri: str | None, label: str, value: str) -> dict | None:
    """数据属性条目；空值返回 None（调用方 filter 掉）。"""
    if value is None or not str(value).strip():
        return None
    return {"iri": iri, "label": label, "value": str(value).strip()}


def _endpoint(
    class_iri: str,
    text: str,
    *,
    source: str = "rule",
    data_properties: list | None = None,
    sub_relationships: list | None = None,
    source_ref: str | None = None,
) -> dict:
    return {
        "class_iri": class_iri,
        "text": text,
        "source": source,
        "data_properties": [d for d in (data_properties or []) if d],
        "sub_relationships": sub_relationships or [],
        "source_ref": source_ref,
    }


def _find_drug_code(structure: DocStructure) -> str:
    m = _DRUG_CODE_RE.search(structure.title)
    if m:
        return m.group()
    for p in structure.paragraphs[:12]:
        m = _DRUG_CODE_RE.search(p)
        if m:
            return m.group()
    return structure.title


# --- 端点 finder（每类一规则，针对该文档结构实测校准）--------------------
def find_drug_product(ctx: _Ctx) -> list[dict]:
    """describes→DrugProduct：端点为药物程序代号；data props 取自「产品的基本性质」键值段。"""
    label_map = ctx.dp_label_map(DRUG_PRODUCT_IRI)
    label_map.setdefault("PDE", DP["pde_mg_per_day"])

    # 优先用三阶段真归类出的 DrugProduct 实体文本，否则用标题代号。
    text, source = ctx.drug_code, "lexical"
    for t in ctx.triples:
        if t.get("entity_class_iri") == DRUG_PRODUCT_IRI and t.get("entity_text"):
            text, source = t["entity_text"], "typed"
            break

    sec = ctx.structure.find_section("产品的基本性质", "基本性质")
    props: list[dict] = []
    if sec:
        for para in sec.paras:
            kv = _split_kv(para)
            if not kv:
                continue
            key, val = kv
            if not val:
                continue
            iri = label_map.get(key) or label_map.get(_strip_unit(key))
            props.append(_dp(iri, key, val))
    return [_endpoint(DRUG_PRODUCT_IRI, text, source=source,
                      data_properties=props, source_ref="§ 产品的基本性质")]


def _equipment_rows(ctx: _Ctx) -> list[dict]:
    tbl = ctx.structure.find_table("设备规格", "匹配设备")
    return tbl.rows if tbl else []


def _equip_class(spec: str) -> str:
    for kw, iri in _EQUIP_CLASS_BY_SPEC:
        if kw in (spec or ""):
            return iri
    return PROCESS_EQUIPMENT_IRI


def _equipment_endpoint_from_row(row: dict) -> dict | None:
    """设备需求表一行 → Equipment 端点；``匹配设备`` 取首选编号（斜杠分隔为备选）。"""
    match = _row_get(row, "匹配设备")
    if not match:
        return None
    code = match.split("/")[0].strip()
    if not code:
        return None
    spec = _row_get(row, "设备规格")
    props = [
        _dp(None, "设备规格", spec),
        _dp(None, "材质", _row_get(row, "材质")),
        _dp(None, "规格型号", _row_get(row, "规格型号")),
        _dp(None, "主残留物", _row_get(row, "主残留物")),
    ]
    return _endpoint(_equip_class(spec), code, data_properties=props,
                     source_ref="表 设备需求")


def find_equipment(ctx: _Ctx) -> list[dict]:
    """usesEquipment→Equipment：解析设备需求表，按设备编号去重。"""
    out: list[dict] = []
    seen: set[str] = set()
    for row in _equipment_rows(ctx):
        ep = _equipment_endpoint_from_row(row)
        if ep and ep["text"] not in seen:
            seen.add(ep["text"])
            out.append(ep)
    return out


def _step_equipment(ctx: _Ctx, step_name: str) -> list[dict]:
    """设备需求表中「步骤」前缀匹配该合成步骤的设备子关系（步骤内按编号去重）。"""
    norm = re.sub(r"\s+", "", step_name)
    subs: list[dict] = []
    seen: set[str] = set()
    for row in _equipment_rows(ctx):
        step_cell = re.sub(r"\s+", "", _row_get(row, "步骤"))
        if not step_cell.startswith(norm):
            continue
        ep = _equipment_endpoint_from_row(row)
        if ep and ep["text"] not in seen:
            seen.add(ep["text"])
            subs.append(_sub(USES_EQUIPMENT_IRI, "使用设备", ctx, ep))
    return subs


def _sub(predicate_iri: str, predicate_label: str, ctx: _Ctx, ep: dict) -> dict:
    """端点 → 子关系条目（递归携带其自身子关系：route→step→设备/中间体）。"""
    return {
        "predicate_iri": predicate_iri,
        "predicate_label": predicate_label,
        "object_class_iri": ep["class_iri"],
        "object_class_label": ctx.class_label(ep["class_iri"]),
        "object_text": ep["text"],
        "object_source": ep["source"],
        "object_data_properties": ep["data_properties"],
        "sub_relationships": ep.get("sub_relationships", []),
        "source_ref": ep.get("source_ref"),
    }


def _find_narrative(structure: DocStructure) -> str:
    """工艺描述叙述段：含「起始物料/中间体」且较长者。"""
    for p in structure.paragraphs:
        if len(p) > 60 and ("起始物料" in p or "中间体" in p) and ("得到" in p or "反应" in p):
            return p
    sec = structure.find_section("工艺描述")
    if sec and sec.paras:
        return max(sec.paras, key=len)
    return ""


def find_synthesis_route(ctx: _Ctx) -> list[dict]:
    """hasSynthesisRoute→SynthesisRoute：1 条路线；步骤取自得量收率表，关联设备/中间体。"""
    narrative = _find_narrative(ctx.structure)
    route_props = [_dp(DP["processDescription"], "工艺描述", narrative)]

    yield_tbl = ctx.structure.find_table("参考得量范围", "参考收率范围")
    steps: list[dict] = []
    if yield_tbl:
        for i, row in enumerate(yield_tbl.rows, start=1):
            name = _row_get(row, "名称")
            if not name:
                continue
            inter_cls = CRUDE_PRODUCT_IRI if "粗品" in name else PROCESS_INTERMEDIATE_IRI
            step_subs = _step_equipment(ctx, name)
            step_subs.append(_sub(
                PRODUCES_INTERMEDIATE_IRI, "产出中间体", ctx,
                _endpoint(inter_cls, name),
            ))
            steps.append(_endpoint(
                SYNTHESIS_STEP_IRI, f"步骤{i}：{name}",
                data_properties=[
                    _dp(DP["stepOrder"], "步骤序号", str(i)),
                    _dp(DP["outputMassRange_kg"], "得量范围（kg）", _row_get(row, "得量")),
                    _dp(DP["yieldRangePercent"], "收率范围（%）", _row_get(row, "收率")),
                ],
                sub_relationships=step_subs,
                source_ref="表 得量收率范围",
            ))

    step_subs_all = [_sub(_DEV + "hasStep", "包含步骤", ctx, s) for s in steps]
    return [_endpoint(
        SYNTHESIS_ROUTE_IRI, f"{ctx.drug_code} 合成路线",
        data_properties=route_props,
        sub_relationships=step_subs_all,
        source_ref="§ 工艺 / 工艺描述",
    )]


def _risk_endpoints(ctx: _Ctx, class_iri: str, desc_col: str,
                    table_needles: tuple, section_needles: tuple,
                    source_ref: str) -> list[dict]:
    out: list[dict] = []
    tbl = ctx.structure.find_table(*table_needles)
    if tbl:
        for row in tbl.rows:
            stage = _row_get(row, "风险环节")
            desc = _row_get(row, desc_col)
            if not (stage or desc):
                continue
            out.append(_endpoint(
                class_iri, stage or desc,
                data_properties=[
                    _dp(DP["riskCategory"], "风险环节", stage),
                    _dp(DP["riskDescription"], "风险描述", desc),
                    _dp(DP["controlMeasure"], "控制措施", _row_get(row, "控制措施")),
                ],
                source_ref=source_ref,
            ))
    # 并入章节列举段（安全评估特有；质量风险一般无对应章节）。
    if section_needles:
        sec = ctx.structure.find_section(*section_needles)
        if sec:
            for para in sec.paras:
                kv = _split_kv(para)
                if kv and kv[1]:          # 跳过「…风险包括：」引导句（冒号后无描述）
                    out.append(_endpoint(
                        class_iri, kv[0],
                        data_properties=[_dp(DP["riskDescription"], "风险描述", kv[1])],
                        source_ref=f"§ {sec.heading}",
                    ))
    return out


def find_safety_risk(ctx: _Ctx) -> list[dict]:
    return _risk_endpoints(
        ctx, SAFETY_RISK_IRI, "风险描述",
        ("风险环节", "风险描述"), ("安全评估",), "表 安全风险",
    )


def find_quality_risk(ctx: _Ctx) -> list[dict]:
    return _risk_endpoints(
        ctx, QUALITY_RISK_IRI, "质量风险",
        ("风险环节", "质量风险"), (), "表 质量风险",
    )


def find_cleaning(ctx: _Ctx) -> list[dict]:
    """hasCleaningMethod→CleaningProcess：设备清洗方法各步。

    定位「设备清洗方法」节而非「设备清洗残留物…」节——后者同含「设备清洗」且在文档中
    先出现，故 needle 取「清洗方法」避免贪婪误匹配（实测校准）。跳过「…如下：」引导句
    （冒号后无步骤描述）。
    """
    sec = ctx.structure.find_section("设备清洗方法", "清洗方法")
    out: list[dict] = []
    if sec:
        for para in sec.paras:
            kv = _split_kv(para)
            if not kv:
                continue
            name, desc = kv
            if not desc:          # 引导句（如「…设备清洗方法如下：」）无描述，跳过
                continue
            out.append(_endpoint(
                CLEANING_PROCESS_IRI, name,
                data_properties=[_dp(None, "清洗描述", desc)],
                source_ref=f"§ {sec.heading}",
            ))
    return out


def find_residue(ctx: _Ctx) -> list[dict]:
    """hasCleaningResidue→Residue：溶解度表残留物名，按名称去重。"""
    tbl = ctx.structure.find_table("物料酸碱性", "溶解度")
    out: list[dict] = []
    seen: set[str] = set()
    if tbl:
        for row in tbl.rows:
            name = _row_get(row, "名称")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(_endpoint(
                RESIDUE_IRI, name,
                data_properties=[
                    _dp(None, "物料酸碱性", _row_get(row, "酸碱性")),
                    _dp(DP["residueSolvent"], "残留物清洗溶剂", _row_get(row, "溶剂")),
                    _dp(DP["residueSolubility"], "残留物溶解度", _row_get(row, "溶解度")),
                    _dp(DP["residueSolubilityTemperature"], "溶解度测试温度",
                        _row_get(row, "温度")),
                ],
                source_ref="表 溶解度残留",
            ))
    return out


def find_storage(ctx: _Ctx) -> list[dict]:
    """hasStorageCondition→StorageCondition：存放条件表每行。"""
    tbl = ctx.structure.find_table("存放条件", "有效期")
    out: list[dict] = []
    if tbl:
        for row in tbl.rows:
            name = _row_get(row, "中间体", "成品", "名称")
            cond = _row_get(row, "存放条件")
            if not (name or cond):
                continue
            out.append(_endpoint(
                STORAGE_CONDITION_IRI, name or "存放条件",
                data_properties=[
                    _dp(DP["packagingType"], "包装方式", _row_get(row, "包装方式")),
                    _dp(DP["storageTemperature"], "存放温度", cond),
                    _dp(DP["lightProtection"], "是否避光",
                        "是" if "避光" in cond else ("否" if cond else "")),
                    _dp(DP["shelfLife"], "有效期/复测期", _row_get(row, "有效期", "复测期")),
                ],
                source_ref="表 存放条件",
            ))
    return out


_SHARED_LINE_PARAM_MAP = [
    ("NOAEL", DP["noael_mg_per_kg_per_day"]),
    ("种属", DP["noaelSpecies"]),
    ("安全系数", DP["safetyFactor"]),
    ("F值", DP["safetyFactor"]),
    ("HED", DP["humanEquivalentDose_mg_per_kg"]),
    ("等效剂量", DP["humanEquivalentDose_mg_per_kg"]),
    ("起始剂量", DP["proposedStartingDose_mg"]),
    ("最高剂量", DP["proposedMaxDose_mg"]),
    ("周期", DP["noaelDuration"]),
]


def find_shared_line(ctx: _Ctx) -> list[dict]:
    """hasSharedLineData→SharedLineAssessmentData：毒性参数表 + 日剂量段 + PDE，聚为一端点。"""
    props: list[dict] = []

    tbl = ctx.structure.find_table("参数", "数值")
    if tbl:
        for row in tbl.rows:
            param = _row_get(row, "参数")
            value = _row_get(row, "数值")
            if not (param and value):
                continue
            iri = next((i for kw, i in _SHARED_LINE_PARAM_MAP if kw in param), None)
            props.append(_dp(iri, param, value))

    sec = ctx.structure.find_section("日剂量", "给药")
    if sec:
        for para in sec.paras:
            kv = _split_kv(para)
            if not kv:
                continue
            key, val = kv
            if "给药方案" in key:
                props.append(_dp(DP["dosingRegimen"], key, val))
            elif "起始剂量" in key:
                props.append(_dp(DP["proposedStartingDose_mg"], key, val))
            elif "最高剂量" in key:
                props.append(_dp(DP["proposedMaxDose_mg"], key, val))
            elif "周期" in key:
                props.append(_dp(None, key, val))

    for para in ctx.structure.paragraphs:
        kv = _split_kv(para)
        if kv and kv[0].strip().upper() == "PDE" and kv[1]:
            props.append(_dp(DP["pde_mg_per_day"], "PDE", kv[1]))
            break

    props = [p for p in props if p]
    if not props:
        return []
    return [_endpoint(SHARED_LINE_IRI, "共线评估数据",
                      data_properties=props, source_ref="§ 共线评估 / 表 毒性参数")]


def _degradation_class(prefix: str) -> str:
    for kw, iri in _DEGRADATION_CLASS:
        if kw in (prefix or ""):
            return iri
    return DEGRADATION_PATHWAY_IRI


def find_degradation(ctx: _Ctx) -> list[dict]:
    """hasDegradationPathway→DegradationPathway：降解途径段，含条件/降解率/主要杂质。"""
    sec = ctx.structure.find_section("降解途径", "强制降解")
    out: list[dict] = []
    if sec:
        for para in sec.paras:
            kv = _split_kv(para)
            if kv:
                prefix, desc = kv
                if not desc:          # 引导句（如「根据强制降解试验结果：」）无内容，跳过
                    continue
            else:
                prefix, desc = para[:4], para
            out.append(_endpoint(
                _degradation_class(prefix), prefix,
                data_properties=[
                    _dp(DP["degradationCondition"], "降解条件", desc),
                    _dp(DP["degradationPercent"], "降解百分比（%）", _pct(desc)),
                    _dp(DP["majorDegradant"], "主要降解杂质", _impurity(desc)),
                ],
                source_ref=f"§ {sec.heading}",
            ))
    return out


# range 类 IRI → 端点 finder。
_ENDPOINT_FINDERS = {
    DRUG_PRODUCT_IRI: find_drug_product,
    SYNTHESIS_ROUTE_IRI: find_synthesis_route,
    EQUIPMENT_IRI: find_equipment,
    SAFETY_RISK_IRI: find_safety_risk,
    QUALITY_RISK_IRI: find_quality_risk,
    CLEANING_PROCESS_IRI: find_cleaning,
    RESIDUE_IRI: find_residue,
    SHARED_LINE_IRI: find_shared_line,
    STORAGE_CONDITION_IRI: find_storage,
    DEGRADATION_PATHWAY_IRI: find_degradation,
}


def _make_edge(ctx: _Ctx, doc_class: str, subject_label: str, subject_text: str,
               prop: dict, range_iri: str, ep: dict) -> dict:
    return {
        "subject_class_iri": doc_class,
        "subject_class_label": subject_label,
        "subject_text": subject_text,
        "predicate_iri": prop["iri"],
        "predicate_label": prop.get("label") or prop.get("name"),
        "object_class_iri": range_iri,
        "object_class_label": ctx.class_label(range_iri),
        "object_text": ep["text"],
        "object_source": ep["source"],
        "object_data_properties": ep["data_properties"],
        "sub_relationships": ep["sub_relationships"],
        "source_ref": ep.get("source_ref"),
    }


def extract_relationships(
    engine,
    file_path: str | Path,
    triples: list[dict],
    doc_class: dict | None = None,
) -> dict:
    """文档级分类 + 全量关系/属性抽取（仅 Word）。

    ``doc_class`` 若已提供（``_compute_annotation`` 分类前置），跳过重复分类。
    返回 ``{"doc_class": {...} | None, "relationships": [edge, ...]}``。``doc_class`` 为
    分类结果（``{doc_class_iri, label, score, signals}``，可解释）；每条 edge 形如::

        {subject_class_iri, subject_class_label, subject_text,
         predicate_iri, predicate_label, object_class_iri, object_class_label,
         object_text, object_source, object_data_properties, sub_relationships, source_ref}

    自解析文档结构（``parse_docx_structure``）→ 分类 → 反查对象属性（+ 对 CMCReport 补挂
    broad-domain 属性）→ 按 range 调端点 finder → 连边。无识别文档类型 → ``doc_class=None``、
    ``relationships=[]``（优雅降级）。
    """
    structure = parse_docx_structure(file_path)
    classification = doc_class if doc_class is not None else document_classifier.classify(structure, engine)
    if not classification:
        return {"doc_class": None, "relationships": []}

    doc_class = classification["doc_class_iri"]
    drug_code = _find_drug_code(structure)
    ctx = _Ctx(structure=structure, drug_code=drug_code, engine=engine, triples=triples)

    props = list(engine.get_object_properties_by_domain(doc_class))
    if doc_class == CMC_REPORT_IRI:
        present = {p["iri"] for p in props}
        props += [p for p in _SUPPLEMENTAL_CMC_PROPS if p["iri"] not in present]

    subject_label = classification.get("label") or ctx.class_label(doc_class)
    edges: list[dict] = []
    for prop in props:
        for range_iri in prop.get("range", []):
            finder = _ENDPOINT_FINDERS.get(range_iri)
            if not finder:
                continue
            for ep in finder(ctx):
                edges.append(_make_edge(ctx, doc_class, subject_label,
                                        drug_code, prop, range_iri, ep))

    return {"doc_class": classification, "relationships": edges}
