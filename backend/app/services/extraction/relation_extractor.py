"""规则式关系（对象属性）抽取 —— 从研发文档重建本体的对象属性图 + 内容子图。

三阶段 NER（``document_annotator``）只产出「已归类实体 span + 数据属性三元组」，没有
文档级分类、没有对象属性/关系边，故无法重建本体里 CMCReport 那张对象属性图
（``describes`` / ``hasSynthesisRoute`` / ``usesEquipment`` …）。本模块是 ``annotate_word``
之上的**纯后处理器**，离线、确定性、表格/章节感知，分三步补出这张图：

1. **文档级分类**（``document_classifier``）：标题/章节/TOC 打分 → 文档类型（如 CMCReport）。
2. **两层注解驱动分派**（016）：``engine.get_relation_schema(doc_class, max_hops=4)``
   一次 BFS 取得完整关系图谱，按 ``_RANGE_OVERRIDES``（composite/多源策略）→
   ``_METHOD_STRATEGIES``（按 TTL ``extractionMethod`` 注解分派，含泛化 fallback）。
   新增本体类只需 TTL 注解，无需修改 Python。
3. **端点 finder**：按策略抽端点，每端点可带**嵌套数据属性**（``object_data_properties``）
   与**子关系**（``sub_relationships``，由 schema 边递归驱动或 composite 策略内部产出），
   并附**溯源**（``source_ref``）。子类→父类边查找通过 ``_build_class_hierarchy`` 实现。

数据属性回填复用 ``engine.get_data_properties_by_domain``（与三阶段阶段三同口径）；表格按
**表头签名**定位（题注在该文档不可靠）。全程无新增模型调用，开销可忽略。
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.services.extraction import document_classifier
from app.services.extraction.document_profile import (
    compile_ontology_bindings,
    parse_profile,
    read_document_profile,
)
from app.services.extraction.docx_structure import (
    DocStructure,
    parse_docx_structure,
)
from app.services.extraction.equipment_source import enrich_equipment_facts
from app.services.extraction.production_area_source import get_production_area_source
from app.services.reasoning.pde_conflict import detect_pde_conflict

logger = logging.getLogger(__name__)

# --- 本体 IRI（与 slpra-*.ttl 一致）----------------------------------------
_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
_EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"
_CLEAN = "https://ontology.pharma-gmp.cn/slpra/cleaning/"
_FACIL = "https://ontology.pharma-gmp.cn/slpra/facility/"

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
PRODUCTION_RISK_IRI = _DEV + "ProductionRiskAssessment"
DEGRADATION_PATHWAY_IRI = _DEV + "DegradationPathway"
CLINICAL_SAMPLE_PLAN_IRI = _DEV + "ClinicalSampleProductionPlan"
PRODUCTION_AREA_IRI = _FACIL + "ProductionArea"

# 对象属性 IRI（broad-domain 补挂用，本体未声明 rdfs:domain → 反查不到）。
USES_EQUIPMENT_IRI = _DEV + "usesEquipment"
HAS_STORAGE_CONDITION_IRI = _DEV + "hasStorageCondition"
HAS_DEGRADATION_PATHWAY_IRI = _DEV + "hasDegradationPathway"
PRODUCES_INTERMEDIATE_IRI = _DEV + "producesIntermediate"
PRODUCED_IN_AREA_IRI = _DEV + "producedInArea"

# 数据属性 IRI（多数内容类的 dprop 本体未声明 domain，无法经 by_domain 反查 → 直引常量）。
DP = {
    "processDescription": _DEV + "processDescription",
    "stepOrder": _DEV + "stepOrder",
    "yieldRangePercent": _DEV + "yieldRangePercent",
    "outputMassRange_kg": _DEV + "outputMassRange_kg",
    "riskCategory": _DEV + "riskCategory",
    "riskDescription": _DEV + "riskDescription",
    "controlMeasure": _DEV + "controlMeasure",
    "riskFactor": _DEV + "riskFactor",
    "preControlRiskLevel": _DEV + "preControlRiskLevel",
    "postControlRiskLevel": _DEV + "postControlRiskLevel",
    "riskTraceability": _DEV + "riskTraceability",
    "riskStatus": _DEV + "riskStatus",
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
    _synonym_cache: dict = field(default_factory=dict)
    _pattern_cache: dict = field(default_factory=dict)
    _hints_cache: dict = field(default_factory=dict)

    def extraction_hints(self, class_iri: str) -> dict:
        """``{method, anchors}`` via ``engine.get_extraction_hints``; cached."""
        if class_iri not in self._hints_cache:
            try:
                self._hints_cache[class_iri] = self.engine.get_extraction_hints(class_iri)
            except Exception:
                self._hints_cache[class_iri] = {"method": None, "anchors": []}
        return self._hints_cache[class_iri]

    def dp_patterns(self, class_iri: str) -> list[dict]:
        """``[{iri,label,pattern}]`` via ``engine.get_data_property_patterns``; cached。

        供 sentence_regex 类端点从 TTL 注解读字段正则（group(1)=值）。
        """
        if class_iri not in self._pattern_cache:
            try:
                self._pattern_cache[class_iri] = self.engine.get_data_property_patterns(class_iri)
            except Exception:
                self._pattern_cache[class_iri] = []
        return self._pattern_cache[class_iri]

    def subclass_synonyms(self, class_iri: str) -> dict[str, str]:
        """``{synonym → subclass_iri}`` via ``engine.get_subclass_synonyms``; cached."""
        if class_iri not in self._synonym_cache:
            try:
                self._synonym_cache[class_iri] = self.engine.get_subclass_synonyms(class_iri)
            except Exception:
                self._synonym_cache[class_iri] = {}
        return self._synonym_cache[class_iri]

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


def _classify_by_synonyms(text: str | None, synonyms: dict[str, str], fallback: str) -> str:
    """Match ``text`` against synonym keys (substring in either direction)."""
    if not text:
        return fallback
    for kw, iri in synonyms.items():
        if kw in text or text in kw:
            return iri
    return fallback


_EQUIPMENT_CANDIDATE_SEPARATOR_RE = re.compile(r"\s*(?:/|或|、)\s*")


def _equipment_endpoints_from_row(ctx: _Ctx, row: dict) -> list[dict]:
    """设备需求表一行 → 一个或多个候选 Equipment 端点。"""
    match = _row_get(row, "匹配设备")
    if not match:
        return []
    codes = list(dict.fromkeys(
        code.strip()
        for code in _EQUIPMENT_CANDIDATE_SEPARATOR_RE.split(match)
        if code.strip()
    ))
    if not codes:
        return []
    spec = _row_get(row, "设备规格")
    synonyms = ctx.subclass_synonyms(EQUIPMENT_IRI)
    cls_iri = _classify_by_synonyms(spec, synonyms, PROCESS_EQUIPMENT_IRI)
    candidate_group = "|".join(codes)
    endpoints: list[dict] = []
    for code in codes:
        props = [
            _dp(None, "设备规格", spec),
            _dp(None, "材质", _row_get(row, "材质")),
            _dp(None, "规格型号", _row_get(row, "规格型号")),
            _dp(None, "主残留物", _row_get(row, "主残留物")),
        ]
        if len(codes) > 1:
            props.extend([
                _dp(None, "候选设备组", candidate_group),
                _dp(None, "候选设备原文", match),
                _dp(None, "设备候选状态", "待 APS 排期确认"),
            ])
        endpoints.append(_endpoint(
            cls_iri,
            code,
            data_properties=props,
            source_ref="表 设备需求",
        ))
    return endpoints


def find_equipment(ctx: _Ctx) -> list[dict]:
    """usesEquipment→Equipment：解析设备需求表，按设备编号去重。"""
    out: list[dict] = []
    seen: set[str] = set()
    for row in _equipment_rows(ctx):
        for ep in _equipment_endpoints_from_row(ctx, row):
            if ep["text"] not in seen:
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
        for ep in _equipment_endpoints_from_row(ctx, row):
            if ep["text"] not in seen:
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


def find_production_risk(ctx: _Ctx) -> list[dict]:
    """Extract broader HazID production risk table (7 columns)."""
    tbl = ctx.structure.find_table("风险因素", "控制前")
    if not tbl:
        tbl = ctx.structure.find_table("风险因素", "控制措施", "风险状态")
    if not tbl:
        tbl = ctx.structure.find_table("HazID", "风险因素")
    if not tbl:
        tbl = ctx.structure.find_table("危害因素", "控制")
    if not tbl:
        return []
    out: list[dict] = []
    for row in tbl.rows:
        hazid = _row_get(row, "风险类型", "HazID", "危害识别")
        factor = _row_get(row, "风险因素", "危害因素")
        if not (hazid or factor):
            continue
        out.append(_endpoint(
            PRODUCTION_RISK_IRI, hazid or factor,
            data_properties=[
                _dp(DP["riskCategory"], "风险类型", hazid),
                _dp(DP["riskFactor"], "风险因素", factor),
                _dp(DP["preControlRiskLevel"], "控制前风险水平",
                    _row_get(row, "控制前", "风险控制前", "控制前风险水平")),
                _dp(DP["postControlRiskLevel"], "控制后风险水平",
                    _row_get(row, "控制后", "风险控制后", "控制后风险水平")),
                _dp(DP["controlMeasure"], "控制措施",
                    _row_get(row, "控制措施", "风险控制措施")),
                _dp(DP["riskTraceability"], "可追溯性",
                    _row_get(row, "可追溯性", "追溯", "风险控制措施可追溯性")),
                _dp(DP["riskStatus"], "风险状态",
                    _row_get(row, "风险状态", "状态")),
            ],
            source_ref="表 生产风险评估",
        ))
    return out


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


def find_degradation(ctx: _Ctx) -> list[dict]:
    """hasDegradationPathway→DegradationPathway：降解途径段，含条件/降解率/主要杂质。"""
    sec = ctx.structure.find_section("降解途径", "强制降解")
    out: list[dict] = []
    synonyms = ctx.subclass_synonyms(DEGRADATION_PATHWAY_IRI)
    if sec:
        for para in sec.paras:
            kv = _split_kv(para)
            if kv:
                prefix, desc = kv
                if not desc:          # 引导句（如「根据强制降解试验结果：」）无内容，跳过
                    continue
            else:
                prefix, desc = para[:4], para
            cls = _classify_by_synonyms(prefix, synonyms, DEGRADATION_PATHWAY_IRI)
            out.append(_endpoint(
                cls, prefix,
                data_properties=[
                    _dp(DP["degradationCondition"], "降解条件", desc),
                    _dp(DP["degradationPercent"], "降解百分比（%）", _pct(desc)),
                    _dp(DP["majorDegradant"], "主要降解杂质", _impurity(desc)),
                ],
                source_ref=f"§ {sec.heading}",
            ))
    return out


# 车间跨度正则——车间号拆分 + 外部事实源聚合逻辑，非字段抽取，不可泛化。
_PLAN_AREA_SPAN_RE = re.compile(r"在\s*([^，。]*?车间)")


def _find_anchor_sentence(structure: DocStructure, anchors: list[str]) -> str:
    """按 TTL extractionAnchor 关键词定位目标句。

    优先全文扫描含**全部**锚点的段落；未命中则**渐进松弛**——从 N-1 个锚点递减到 1 个，
    逐节扫描含该子集的段落。同一轮（相同子集大小）遍历所有组合，保证 RDF 多值注解
    遍历顺序不影响结果。最少需命中 1 个锚点。
    """
    from itertools import combinations

    if not anchors:
        return ""
    for p in structure.paragraphs:
        if all(a in p for a in anchors):
            return p
    for keep in range(len(anchors) - 1, 0, -1):
        for subset in combinations(anchors, keep):
            for sec in structure.sections:
                for para in sec.paras:
                    if all(a in para for a in subset):
                        return para
    return ""


def _extract_sentence_fields(ctx: _Ctx, class_iri: str, sentence: str) -> list[dict]:
    """Apply TTL ``extractionPattern`` regexes to a sentence, return data properties."""
    props: list[dict] = []
    for spec in ctx.dp_patterns(class_iri):
        try:
            m = re.search(spec["pattern"], sentence)
        except re.error:
            continue
        if m and m.groups():
            dp = _dp(spec["iri"], spec["label"], m.group(1))
            if dp:
                props.append(dp)
    return props


def find_production_plan(ctx: _Ctx) -> list[dict]:
    """hasProductionPlan→ClinicalSampleProductionPlan：计划句聚为一个备样生产计划端点，
    其下按车间号拆多条 producedInArea→ProductionArea 子关系。

    锚点关键词由 TTL ``extractionAnchor`` 注解驱动（不再硬编码）。字段正则由
    ``extractionPattern`` 注解驱动。「642/646车间」含两个车间，逐个经 mock 外部车间
    主数据源解析为 ProductionArea。
    """
    anchors = ctx.extraction_hints(CLINICAL_SAMPLE_PLAN_IRI).get("anchors", [])
    sentence = _find_anchor_sentence(ctx.structure, anchors)
    if not sentence:
        return []

    props = _extract_sentence_fields(ctx, CLINICAL_SAMPLE_PLAN_IRI, sentence)

    area_subs: list[dict] = []
    span = _PLAN_AREA_SPAN_RE.search(sentence)
    if span:
        source = get_production_area_source()
        seen: set[str] = set()
        for code in re.findall(r"\d{2,4}", span.group(1)):
            if code in seen:
                continue
            seen.add(code)
            fact = source.resolve(code)
            if fact is None:
                continue
            area_subs.append(_sub(
                PRODUCED_IN_AREA_IRI, "生产车间", ctx,
                _endpoint(PRODUCTION_AREA_IRI, fact.label, source="external",
                          data_properties=fact.data_properties,
                          source_ref="外部事实源：生产车间主数据"),
            ))

    if not props and not area_subs:
        return []
    return [_endpoint(
        CLINICAL_SAMPLE_PLAN_IRI, "临床备样生产计划",
        data_properties=props,
        sub_relationships=area_subs,
        source_ref="§ 简介",
    )]


# ---------------------------------------------------------------------------
# Strategy pattern: annotation-driven extraction dispatch
# ---------------------------------------------------------------------------

class ExtractionStrategy(ABC):
    @abstractmethod
    def find_endpoints(self, ctx: _Ctx, edge: dict) -> list[dict]:
        """Find endpoints for a schema edge.

        ``edge`` is one element from ``engine.get_relation_schema()``
        (keys: range_class_iri, range_extraction_hints, range_data_properties, …).
        """
        ...


class _LegacyFinder(ExtractionStrategy):
    """Wraps an existing ``(ctx) -> list[dict]`` finder as a strategy."""
    def __init__(self, fn):
        self._fn = fn

    def find_endpoints(self, ctx, edge):
        return self._fn(ctx)


class _ProfileStrategy(ExtractionStrategy):
    """Interpret the range class's constrained extraction Profile."""

    def find_endpoints(self, ctx: _Ctx, edge: dict) -> list[dict]:
        range_iri = edge["range_class_iri"]
        hints = edge.get("range_extraction_hints") or {}
        profile_raw = hints.get("profile")
        if hints.get("profile_error"):
            logger.warning(
                "invalid extraction Profile JSON for %s: %s",
                range_iri,
                hints["profile_error"],
            )
            return []
        if not profile_raw:
            return []
        try:
            profile = parse_profile(profile_raw)
            bindings = compile_ontology_bindings(ctx.engine, range_iri, profile)
        except Exception:
            logger.warning("invalid extraction Profile for %s", range_iri, exc_info=True)
            return []

        result = read_document_profile(
            ctx.structure, range_iri, profile, bindings
        )
        if result.degraded_reason:
            logger.warning(
                "extraction Profile degraded for %s: %s",
                range_iri,
                result.degraded_reason,
            )
            return []

        synonyms = ctx.subclass_synonyms(range_iri)
        endpoints: list[dict] = []
        for candidate in result.candidates:
            class_iri = _classify_by_synonyms(
                candidate.subclass_value, synonyms, range_iri
            )
            properties = []
            for value in candidate.values:
                item = {
                    "iri": value.property_iri,
                    "label": value.label,
                    "value": value.value,
                    "raw_value": value.raw_value,
                    "source_ref": value.source_ref,
                }
                if value.note:
                    item["note"] = value.note
                properties.append(item)
            if candidate.candidate_group:
                properties.extend([
                    _dp(None, "候选设备组", candidate.candidate_group),
                    _dp(None, "候选设备原文", candidate.candidate_expression),
                    _dp(None, "设备候选状态", "待 APS 排期确认"),
                ])
            endpoints.append(_endpoint(
                class_iri,
                candidate.identifier or profile.label or ctx.class_label(class_iri),
                source="ontology-profile",
                data_properties=properties,
                source_ref=candidate.source_ref,
            ))
        return endpoints


# ---------------------------------------------------------------------------
# Generic strategies — handle NEW range classes via TTL annotations only
# ---------------------------------------------------------------------------

def _generic_table_scan(ctx: _Ctx, edge: dict) -> list[dict]:
    """Anchor-driven table scan for range classes with no specialised finder."""
    anchors = (edge.get("range_extraction_hints") or {}).get("anchors", [])
    if not anchors:
        return []
    tbl = ctx.structure.find_table(*anchors)
    if not tbl:
        return []
    range_iri = edge["range_class_iri"]
    dp_map = ctx.dp_label_map(range_iri)
    endpoints: list[dict] = []
    for row in tbl.rows:
        props: list[dict] = []
        text_parts: list[str] = []
        for col, val in row.items():
            if not val:
                continue
            iri = dp_map.get(col) or dp_map.get(_strip_unit(col))
            dp = _dp(iri, col, val)
            if dp:
                props.append(dp)
            if not iri:
                text_parts.append(val)
        if props:
            endpoints.append(_endpoint(
                range_iri, " / ".join(text_parts) or anchors[0],
                data_properties=props,
                source_ref=f"表 {anchors[0]}",
            ))
    return endpoints


def _generic_section_kv(ctx: _Ctx, edge: dict) -> list[dict]:
    """Anchor-driven section KV scan for range classes with no specialised finder."""
    anchors = (edge.get("range_extraction_hints") or {}).get("anchors", [])
    if not anchors:
        return []
    sec = ctx.structure.find_section(*anchors)
    if not sec:
        return []
    range_iri = edge["range_class_iri"]
    dp_map = ctx.dp_label_map(range_iri)
    props: list[dict] = []
    for para in sec.paras:
        kv = _split_kv(para)
        if not kv:
            continue
        key, val = kv
        if not val:
            continue
        iri = dp_map.get(key) or dp_map.get(_strip_unit(key))
        dp = _dp(iri, key, val)
        if dp:
            props.append(dp)
    if not props:
        return []
    return [_endpoint(range_iri, anchors[0],
                      data_properties=props, source_ref=f"§ {anchors[0]}")]


def _generic_section_paragraph(ctx: _Ctx, edge: dict) -> list[dict]:
    """Anchor-driven section paragraph scan for range classes with no specialised finder."""
    anchors = (edge.get("range_extraction_hints") or {}).get("anchors", [])
    if not anchors:
        return []
    sec = ctx.structure.find_section(*anchors)
    if not sec:
        return []
    range_iri = edge["range_class_iri"]
    endpoints: list[dict] = []
    for para in sec.paras:
        text = para.strip()
        if not text:
            continue
        endpoints.append(_endpoint(range_iri, text,
                                   source_ref=f"§ {anchors[0]}"))
    return endpoints


# ---------------------------------------------------------------------------
# Method strategy: dispatches by range class (specialised) or generic fallback
# ---------------------------------------------------------------------------

class _MethodStrategy(ExtractionStrategy):
    """Per-method dispatcher: known ranges → specialised finder, unknown → generic."""
    def __init__(self, finders: dict[str, object],
                 generic: object | None = None):
        self._finders = finders
        self._generic = generic

    def find_endpoints(self, ctx: _Ctx, edge: dict) -> list[dict]:
        fn = self._finders.get(edge["range_class_iri"])
        if fn:
            return fn(ctx)
        if self._generic:
            return self._generic(ctx, edge)
        return []


# Range-override strategies (composite/multi-source, logic not generalisable).
_RANGE_OVERRIDES: dict[str, ExtractionStrategy] = {
    SYNTHESIS_ROUTE_IRI: _LegacyFinder(find_synthesis_route),
    CLINICAL_SAMPLE_PLAN_IRI: _LegacyFinder(find_production_plan),
}

# Method-based strategies (extractionMethod annotation → strategy).
_METHOD_STRATEGIES: dict[str, ExtractionStrategy] = {
    "table_scan": _MethodStrategy({
        STORAGE_CONDITION_IRI: find_storage,
        SAFETY_RISK_IRI: find_safety_risk,
        QUALITY_RISK_IRI: find_quality_risk,
        PRODUCTION_RISK_IRI: find_production_risk,
    }, generic=_generic_table_scan),
    "section_kv": _MethodStrategy({
        CLEANING_PROCESS_IRI: find_cleaning,
    }, generic=_generic_section_kv),
    "section_paragraph": _MethodStrategy({
        DEGRADATION_PATHWAY_IRI: find_degradation,
    }, generic=_generic_section_paragraph),
}


def _resolve_strategy(edge: dict) -> ExtractionStrategy | None:
    hints = edge.get("range_extraction_hints") or {}
    if hints.get("profile") or hints.get("profile_error"):
        return _ProfileStrategy()
    override = _RANGE_OVERRIDES.get(edge["range_class_iri"])
    if override:
        return override
    method = hints.get("method")
    if method:
        return _METHOD_STRATEGIES.get(method)
    return None


def _make_edge(ctx: _Ctx, doc_class: str, subject_label: str, subject_text: str,
               schema_edge: dict, ep: dict) -> dict:
    range_iri = ep["class_iri"]
    return {
        "subject_class_iri": doc_class,
        "subject_class_label": subject_label,
        "subject_text": subject_text,
        "predicate_iri": schema_edge["predicate_iri"],
        "predicate_label": schema_edge["predicate_label"],
        "object_class_iri": range_iri,
        "object_class_label": ctx.class_label(range_iri),
        "object_text": ep["text"],
        "object_source": ep["source"],
        "object_data_properties": ep["data_properties"],
        "sub_relationships": ep["sub_relationships"],
        "source_ref": ep.get("source_ref"),
    }


def _attach_pde_conflict(doc_class: str, edges: list[dict]) -> None:
    """CMCReport：在**每个**「共线评估数据」端点上检测并挂载「推导 vs 原文」PDE 冲突（供人工裁决）。

    原文 PDE 与推导毒理参数均取自**真实抽取**的共线评估端点，种属可从端点标题（object_text）回退；
    无真实端点或参数不足 → 不产出冲突（不再合成 mock 端点/mock PDE，推导侧亦不回退 mock）。命中冲突
    → 就地写入 ``edge["conflict"]``（就地修改 ``edges``）；异常由调用点 try/except 兜住。
    """
    if doc_class != CMC_REPORT_IRI:
        return
    for shared in [e for e in edges if e.get("object_class_iri") == SHARED_LINE_IRI]:
        dps = shared.get("object_data_properties") or []
        conflict = detect_pde_conflict(dps, title=str(shared.get("object_text") or ""))
        if conflict:
            shared["conflict"] = conflict


def _extract_sub_relationships(
    ctx: _Ctx,
    parent_class_iri: str,
    edges_by_domain: dict[str, list[dict]],
    class_hierarchy: dict[str, set[str]],
    depth: int = 0,
    max_depth: int = 3,
    path_edges: frozenset[tuple[str, str]] = frozenset(),
) -> list[dict]:
    """Schema-driven recursive sub_relationship discovery.

    For each schema edge where ``domain == parent_class_iri`` (or an ancestor
    of parent_class_iri per the class hierarchy), dispatch the matching strategy
    and recursively discover children.  Composite strategies (SynthesisRoute,
    ProductionPlan) produce their own sub_relationships and are not recursed
    into further.

    ``path_edges`` is the set of ``(predicate_iri, range_class_iri)`` edges
    already traversed on the path from the document root down to
    ``parent_class_iri``.  A schema edge is skipped when its key is already on
    that path — this breaks self-referential domains (e.g.
    ``DegradationPathway ─hasDegradationPathway→ DegradationPathway``) whose
    range finder scans the whole document independent of the parent, which
    would otherwise re-attach every sibling endpoint under each endpoint
    combinatorially up to ``max_depth``.  Legitimate multi-hop chains are
    unaffected: ``path_edges`` is a per-branch copy constraining only the
    current ancestor chain, so a predicate reached via a different path is
    still explored.
    """
    if depth >= max_depth:
        return []
    candidate_domains = {parent_class_iri}
    candidate_domains.update(class_hierarchy.get(parent_class_iri, set()))
    seen_edges: set[tuple[str, str]] = set()
    subs: list[dict] = []
    for domain in candidate_domains:
        for edge in edges_by_domain.get(domain, []):
            # 去重键刻意用 (predicate, range) 而非 (domain, predicate, range)：
            # _resolve_strategy 仅按 (range, extractionMethod) 分派，且现有 finder 一律
            # 全文档扫描、与 domain 无关——故同一 (predicate, range) 经不同 domain 到达时
            # 命中同一 finder、产出完全相同的端点。把 domain 计入键会放宽去重、重新引入
            # 重复端点。（仅当未来 finder 改为按父端点作用域取数时，才需将 domain 纳入。）
            edge_key = (edge["predicate_iri"], edge["range_class_iri"])
            if edge_key in seen_edges or edge_key in path_edges:
                continue
            seen_edges.add(edge_key)
            strategy = _resolve_strategy(edge)
            if not strategy:
                continue
            child_path = path_edges | {edge_key}
            for ep in strategy.find_endpoints(ctx, edge):
                if ep["class_iri"] not in _RANGE_OVERRIDES:
                    ep["sub_relationships"] = _extract_sub_relationships(
                        ctx, ep["class_iri"], edges_by_domain, class_hierarchy,
                        depth + 1, max_depth, child_path,
                    )
                subs.append(_sub(
                    edge["predicate_iri"], edge["predicate_label"], ctx, ep,
                ))
    return subs


def _build_class_hierarchy(schema_edges: list[dict]) -> dict[str, set[str]]:
    """Build {subclass_iri → {ancestor range class IRIs}} from schema edges."""
    child_to_parents: dict[str, set[str]] = {}
    for e in schema_edges:
        parent_iri = e["range_class_iri"]
        for sub in e.get("range_subclasses") or []:
            child_to_parents.setdefault(sub["iri"], set()).add(parent_iri)
    return child_to_parents


def extract_relationships(
    engine,
    file_path: str | Path,
    triples: list[dict],
    doc_class: dict | None = None,
    source_filename: str | None = None,
) -> dict:
    """Schema-driven document classification + relation/property extraction.

    ``doc_class`` 若已提供（``_compute_annotation`` 分类前置），跳过重复分类。
    返回 ``{"doc_class": {...} | None, "relationships": [edge, ...]}``。``doc_class`` 为
    分类结果（``{doc_class_iri, label, score, signals}``，可解释）；每条 edge 形如::

        {subject_class_iri, subject_class_label, subject_text,
         predicate_iri, predicate_label, object_class_iri, object_class_label,
         object_text, object_source, object_data_properties, sub_relationships, source_ref}

    自解析文档结构（``parse_docx_structure``）→ 分类 → 调 ``get_relation_schema`` 取
    完整关系图谱 → 按 range 调策略抽端点 → 连边。无识别文档类型 → ``doc_class=None``、
    ``relationships=[]``（优雅降级）。
    """
    structure = (
        parse_docx_structure(file_path, source_filename=source_filename)
        if source_filename
        else parse_docx_structure(file_path)
    )
    classification = (
        doc_class
        if doc_class is not None
        else document_classifier.classify(structure, engine)
    )
    if not classification:
        return {"doc_class": None, "relationships": []}

    doc_class_iri = classification["doc_class_iri"]
    drug_code = _find_drug_code(structure)
    ctx = _Ctx(structure=structure, drug_code=drug_code, engine=engine, triples=triples)

    schema_edges = engine.get_relation_schema(doc_class_iri, max_hops=4)

    edges_by_domain: dict[str, list[dict]] = defaultdict(list)
    for se in schema_edges:
        edges_by_domain[se["domain_class_iri"]].append(se)
    class_hierarchy = _build_class_hierarchy(schema_edges)

    subject_label = classification.get("label") or ctx.class_label(doc_class_iri)
    edges: list[dict] = []
    for schema_edge in edges_by_domain.get(doc_class_iri, []):
        strategy = _resolve_strategy(schema_edge)
        if not strategy:
            continue
        seed_path = frozenset({
            (schema_edge["predicate_iri"], schema_edge["range_class_iri"]),
        })
        for ep in strategy.find_endpoints(ctx, schema_edge):
            if ep["class_iri"] not in _RANGE_OVERRIDES:
                ep["sub_relationships"] = _extract_sub_relationships(
                    ctx, ep["class_iri"], edges_by_domain, class_hierarchy,
                    path_edges=seed_path,
                )
            edges.append(_make_edge(ctx, doc_class_iri, subject_label,
                                    drug_code, schema_edge, ep))

    try:
        _attach_pde_conflict(doc_class_iri, edges)
    except Exception:
        logger.debug("PDE 冲突检测跳过", exc_info=True)

    try:
        enrich_equipment_facts(edges)  # 抽取期富化（写入标注）；报告期会幂等再跑一次
    except Exception:
        logger.debug("设备档案富化整体跳过", exc_info=True)

    return {"doc_class": classification, "relationships": edges}
