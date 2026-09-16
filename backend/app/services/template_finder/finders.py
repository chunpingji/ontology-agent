"""Display-only Finder port from b6f9bd4e55822e20dce0a2ef0fa8f6d9fdd472c1.

Explicit root, one Word IR; no models, PDE decisions, or external enrichment.
Whole-document legacy finders do not establish verified parent membership.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.extraction.docx_structure import (
    DocSection,
    DocStructure,
    DocTable,
)

from .profiles import (
    compile_ontology_bindings,
    parse_profile,
    read_document_profile,
)
from .sources import located_computed

logger = logging.getLogger(__name__)

# --- 本体 IRI（与 slpra-*.ttl 一致）----------------------------------------
_DEV = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
_DRUG = "https://ontology.pharma-gmp.cn/slpra/drug/"
_EQUIP = "https://ontology.pharma-gmp.cn/slpra/equipment/"
_CLEAN = "https://ontology.pharma-gmp.cn/slpra/cleaning/"

SYNTHESIS_ROUTE_IRI = _DEV + "SynthesisRoute"
SYNTHESIS_STEP_IRI = _DEV + "SynthesisStep"
PURIFICATION_IRI = _DEV + "Purification"
PROCESS_INTERMEDIATE_IRI = _DEV + "ProcessIntermediate"
CRUDE_PRODUCT_IRI = _DEV + "CrudeProduct"
EQUIPMENT_IRI = _EQUIP + "Equipment"
PROCESS_EQUIPMENT_IRI = _EQUIP + "ProcessEquipment"
SAFETY_RISK_IRI = _DEV + "SafetyRiskAssessment"
QUALITY_RISK_IRI = _DEV + "QualityRiskAssessment"
CLEANING_PROCESS_IRI = _CLEAN + "CleaningProcess"
STORAGE_CONDITION_IRI = _DEV + "StorageCondition"
PRODUCTION_RISK_IRI = _DEV + "ProductionRiskAssessment"
DEGRADATION_PATHWAY_IRI = _DEV + "DegradationPathway"
CLINICAL_SAMPLE_PLAN_IRI = _DEV + "ClinicalSampleProductionPlan"

# 对象属性 IRI（broad-domain 补挂用，本体未声明 rdfs:domain → 反查不到）。
USES_EQUIPMENT_IRI = _DEV + "usesEquipment"
PRODUCES_INTERMEDIATE_IRI = _DEV + "producesIntermediate"

# 数据属性 IRI（多数内容类的 dprop 本体未声明 domain，无法经 by_domain 反查 → 直引常量）。
DP = {
    "processName": _DEV + "processName",
    "processBasis": _DEV + "processBasis",
    "processDescription": _DEV + "processDescription",
    "stepOrder": _DEV + "stepOrder",
    "yieldRangePercent": _DEV + "yieldRangePercent",
    "outputMassRange_kg": _DEV + "outputMassRange_kg",
    "reactionConditions": _DEV + "reactionConditions",
    "inProcessControl": _DEV + "inProcessControl",
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
    return text[m.start(1) : m.end(1)].strip(), text[m.start(2) : m.end(2)].strip()


def _row_get(row: dict, *needles: str, default: str = "") -> str:
    for k, v in row.items():
        if any(n in k for n in needles):
            return v or default
    return default


def _pct(text: str) -> str | None:
    m = _PCT_RE.search(text or "")
    return text[m.start(1) : m.end(1)] if m else None


def _impurity(text: str) -> str | None:
    m = _IMP_RE.search(text or "")
    if m:
        return text[m.start(1) : m.end(1)]
    m = re.search(r"杂质[为:：]?\s*([^\s，。;；]+)", text or "")
    return text[m.start(1) : m.end(1)] if m else None


def _dp(iri: str | None, label: str, value: str, *, field_key: str | None = None) -> dict | None:
    """数据属性条目；空值返回 None（调用方 filter 掉）。"""
    if value is None or not str(value).strip():
        return None
    return {
        "iri": iri,
        **({"field_key": field_key} if field_key else {}),
        "label": label,
        "value": str(value).strip(),
        "source_ref": getattr(value, "source_ref", None),
        "computed": getattr(value, "computed", not hasattr(value, "source_ref")),
    }


def _endpoint(
    class_iri: str,
    text: str,
    *,
    source: str = "rule",
    data_properties: list | None = None,
    sub_relationships: list | None = None,
    source_ref: object | None = None,
) -> dict:
    return {
        "class_iri": class_iri,
        "text": text,
        "source": source,
        "data_properties": [d for d in (data_properties or []) if d],
        "sub_relationships": sub_relationships or [],
        "source_ref": getattr(text, "source_ref", None) or source_ref,
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
    codes = list(
        dict.fromkeys(
            code.strip() for code in _EQUIPMENT_CANDIDATE_SEPARATOR_RE.split(match) if code.strip()
        )
    )
    if not codes:
        return []
    spec = _row_get(row, "设备规格")
    synonyms = ctx.subclass_synonyms(EQUIPMENT_IRI)
    cls_iri = _classify_by_synonyms(spec, synonyms, PROCESS_EQUIPMENT_IRI)
    endpoints: list[dict] = []
    for code in codes:
        from .sources import LocatedText

        props = [
            _dp(
                None,
                "匹配设备编号",
                LocatedText(code, getattr(match, "source_ref", {})),
                field_key="equipment.id",
            ),
            _dp(None, "设备规格", spec, field_key="equipment.specification"),
            _dp(None, "材质", _row_get(row, "材质"), field_key="equipment.material"),
            _dp(None, "规格型号", _row_get(row, "规格型号"), field_key="equipment.model"),
            _dp(None, "主残留物", _row_get(row, "主残留物"), field_key="equipment.residue"),
        ]
        endpoints.append(
            _endpoint(
                cls_iri,
                code,
                data_properties=props,
                source_ref=getattr(match, "source_ref", None),
            )
        )
    return endpoints


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


@dataclass(frozen=True)
class _NarrativeMatch:
    """工艺叙述正文及其在原始 Word 中的精确结构坐标。"""

    text: str
    section: DocSection | None
    paragraph_index: int | None


def _is_process_narrative(text: str) -> bool:
    return (
        len(text) > 60
        and ("起始物料" in text or "中间体" in text)
        and ("得到" in text or "反应" in text)
    )


def _find_narrative(structure: DocStructure) -> _NarrativeMatch:
    """定位工艺文字描述，避开同级或前置的合成路线图。

    优先选择最深层、标题为「工艺描述」且真正包含叙述正文的章节；最后才
    使用无法提供坐标的全文兼容回退。
    """
    candidates: list[tuple[tuple[int, int, int], DocSection, int | None, str]] = []
    for sec in structure.sections:
        for offset, text in enumerate(sec.paras):
            if not _is_process_narrative(text):
                continue
            paragraph_index = sec.para_indices[offset] if offset < len(sec.para_indices) else None
            score = (sec.level, int("工艺描述" in sec.heading), len(text))
            candidates.append((score, sec, paragraph_index, text))

    if candidates:
        _, section, paragraph_index, text = max(candidates, key=lambda item: item[0])
        return _NarrativeMatch(text, section, paragraph_index)

    process_sections = [
        sec for sec in structure.sections if "工艺描述" in sec.heading and sec.paras
    ]
    if process_sections:
        section = max(
            process_sections,
            key=lambda sec: (sec.level, max(map(len, sec.paras))),
        )
        offset = max(range(len(section.paras)), key=lambda idx: len(section.paras[idx]))
        paragraph_index = (
            section.para_indices[offset] if offset < len(section.para_indices) else None
        )
        return _NarrativeMatch(section.paras[offset], section, paragraph_index)

    for text in structure.paragraphs:
        if _is_process_narrative(text):
            return _NarrativeMatch(text, None, None)
    return _NarrativeMatch("", None, None)


_PROCESS_STEP_HEADING_MARKERS = ("制备", "合成", "反应", "结晶", "纯化", "精制")
_PROCESS_CONTROL_MARKERS = ("HPLC", "检测", "检验", "中控", "质量控制")
_PROCESS_TABLE_HEADER_MARKERS = {"工序", "步骤", "操作", "操作步骤", "工艺步骤"}
_EXPLICIT_STEP_RE = re.compile(r"^\s*步骤\s*[0-9一二三四五六七八九十]+\s*[：:]\s*(?P<name>.+?)\s*$")


def _process_detail_rows(table: DocTable) -> list[tuple[int, str, str]]:
    """Return detailed operation rows from a table under a concrete process heading.

    Raw ``cells`` are intentional: many GMP process-detail tables have no header, and
    the generic table parser therefore treats their first operation (e.g. 投料) as a
    header.  Yield summaries are excluded both by heading and by their short values.
    """
    heading = table.section_heading or ""
    if not heading or not any(marker in heading for marker in _PROCESS_STEP_HEADING_MARKERS):
        return []
    if "得量" in heading or "收率" in heading or "路线图" in heading:
        return []

    rows: list[tuple[int, str, str]] = []
    for row_index, cells in enumerate(table.cells):
        if len(cells) < 2:
            continue
        operation = cells[0].strip()
        detail = located_computed(
            " ".join(cell.strip() for cell in cells[1:] if cell.strip()), cells[1:]
        )
        if not operation or not detail:
            continue
        if row_index == 0 and operation in _PROCESS_TABLE_HEADER_MARKERS:
            continue
        if len(operation) <= 40 and len(detail) >= 20:
            rows.append((row_index, operation, detail))
    return rows if len(rows) >= 2 else []


def _yield_values_for_step(
    yield_table: DocTable | None,
    step_name: str,
    drug_code: str,
) -> tuple[str, str, str]:
    """Match the yield-summary row to a real process step; never create a step from it."""
    if yield_table is None:
        return "", "", ""
    normalized_step = re.sub(r"\s+", "", step_name)
    normalized_drug = re.sub(r"\s+", "", drug_code)
    for row in yield_table.rows:
        output_name = _row_get(row, "名称")
        normalized_output = re.sub(r"\s+", "", output_name)
        if not normalized_output:
            continue
        if (
            normalized_output in normalized_step
            or normalized_step in normalized_output
            or normalized_output == normalized_drug
        ):
            return (
                output_name,
                _row_get(row, "得量"),
                _row_get(row, "收率"),
            )
    return "", "", ""


def _explicit_process_steps(
    structure: DocStructure,
) -> list[tuple[str, DocSection, int | None]]:
    """Explicit ``步骤1：…`` paragraphs under a process-description section."""
    matches: list[tuple[str, DocSection, int | None]] = []
    for section in structure.sections:
        if "工艺描述" not in section.heading:
            continue
        for offset, paragraph in enumerate(section.paras):
            match = _EXPLICIT_STEP_RE.match(paragraph)
            if not match:
                continue
            paragraph_index = (
                section.para_indices[offset] if offset < len(section.para_indices) else None
            )
            matches.append(
                (paragraph[match.start("name") : match.end("name")], section, paragraph_index)
            )
    return matches


def _normalize_process_name(heading: str, drug_code: str) -> str:
    """``HRS-1597结晶纯化`` → report/business name ``结晶纯化``."""
    name = heading.strip()
    if drug_code:
        name = re.sub(
            rf"^\s*{re.escape(drug_code)}\s*[-—_:：]*\s*",
            "",
            name,
            flags=re.IGNORECASE,
        )
    return name.strip() or heading.strip()


def _find_process_basis(structure: DocStructure) -> str:
    """Exact source heading used as the process basis (e.g. 3.1.1合成路线图)."""
    heading_candidates = [
        section.heading.strip() for section in structure.sections if "合成路线图" in section.heading
    ]
    if heading_candidates:
        return heading_candidates[0]
    # Some enterprise DOCX files render numbered subheadings with Normal style.
    # The shared IR then retains the locator as a short section paragraph rather
    # than a heading; accept only short lines so narrative mentions cannot become
    # the authoritative process basis.
    paragraph_candidates = [
        paragraph.strip()
        for section in structure.sections
        for paragraph in section.paras
        if "合成路线图" in paragraph and len(paragraph.strip()) <= 80
    ]
    return paragraph_candidates[0] if paragraph_candidates else ""


def _detailed_process_description(
    detailed_tables: list[tuple[DocTable, list[tuple[int, str, str]]]],
    drug_code: str,
) -> str:
    """Faithful, deterministic route description assembled from detailed process rows."""
    descriptions: list[str] = []
    for table, rows in detailed_tables:
        process_name = _normalize_process_name(table.section_heading or "", drug_code)
        details = "；".join(f"{operation}：{detail}" for _, operation, detail in rows)
        if details:
            descriptions.append(
                located_computed(
                    f"{process_name}：{details}" if process_name else details,
                    [detail for _, _, detail in rows],
                )
            )
    return located_computed("\n".join(descriptions), descriptions)


def find_synthesis_route(ctx: _Ctx) -> list[dict]:
    """hasSynthesisRoute→SynthesisRoute；详细工艺表定义步骤，收率表只补属性。"""
    narrative = _find_narrative(ctx.structure)
    yield_tbl = ctx.structure.find_table("参考得量范围", "参考收率范围")
    steps: list[dict] = []
    detailed_tables = [
        (table, rows) for table in ctx.structure.tables if (rows := _process_detail_rows(table))
    ]
    process_names = list(
        dict.fromkeys(
            _normalize_process_name(table.section_heading or "", ctx.drug_code)
            for table, _ in detailed_tables
            if table.section_heading
        )
    )
    process_name = "、".join(name for name in process_names if name)
    process_basis = _find_process_basis(ctx.structure)
    process_description = (
        _detailed_process_description(detailed_tables, ctx.drug_code) or narrative.text
    )
    route_props = [
        _dp(DP["processName"], "工艺名称", process_name),
        _dp(DP["processBasis"], "工艺依据", process_basis),
        _dp(DP["processDescription"], "工艺描述", process_description),
    ]
    if detailed_tables:
        for i, (table, operation_rows) in enumerate(detailed_tables, start=1):
            step_name = table.section_heading or f"工艺步骤{i}"
            output_name, output_mass, yield_range = _yield_values_for_step(
                yield_tbl, step_name, ctx.drug_code
            )
            conditions: list[str] = []
            controls: list[str] = []
            for _, operation, detail in operation_rows:
                line = located_computed(f"{operation}：{detail}", [operation, detail])
                target = (
                    controls
                    if any(marker in operation.upper() for marker in _PROCESS_CONTROL_MARKERS)
                    else conditions
                )
                target.append(line)

            step_subs = _step_equipment(ctx, step_name)
            if output_name:
                inter_cls = CRUDE_PRODUCT_IRI if "粗品" in output_name else PROCESS_INTERMEDIATE_IRI
                step_subs.append(
                    _sub(
                        PRODUCES_INTERMEDIATE_IRI,
                        "产出中间体",
                        ctx,
                        _endpoint(inter_cls, output_name),
                    )
                )
            source_ref = {
                "kind": "table",
                "section": step_name,
                "heading_index": table.heading_index,
                "table": table.table_index,
                "table_path": table.table_path,
            }
            step_class = (
                PURIFICATION_IRI
                if any(marker in step_name for marker in ("纯化", "精制"))
                else SYNTHESIS_STEP_IRI
            )
            steps.append(
                _endpoint(
                    step_class,
                    step_name,
                    data_properties=[
                        _dp(DP["stepOrder"], "步骤序号", str(i)),
                        _dp(
                            DP["reactionConditions"],
                            "反应条件/详细步骤",
                            located_computed("\n".join(conditions), conditions),
                        ),
                        _dp(
                            DP["inProcessControl"],
                            "过程控制",
                            located_computed("\n".join(controls), controls),
                        ),
                        _dp(DP["outputMassRange_kg"], "得量范围（kg）", output_mass),
                        _dp(DP["yieldRangePercent"], "收率范围（%）", yield_range),
                    ],
                    sub_relationships=step_subs,
                    source_ref=source_ref,
                )
            )
    else:
        for i, (step_name, section, paragraph_index) in enumerate(
            _explicit_process_steps(ctx.structure), start=1
        ):
            output_name, output_mass, yield_range = _yield_values_for_step(
                yield_tbl, step_name, ctx.drug_code
            )
            step_subs = _step_equipment(ctx, output_name or step_name)
            if output_name:
                inter_cls = CRUDE_PRODUCT_IRI if "粗品" in output_name else PROCESS_INTERMEDIATE_IRI
                step_subs.append(
                    _sub(
                        PRODUCES_INTERMEDIATE_IRI,
                        "产出中间体",
                        ctx,
                        _endpoint(inter_cls, output_name),
                    )
                )
            steps.append(
                _endpoint(
                    SYNTHESIS_STEP_IRI,
                    step_name,
                    data_properties=[
                        _dp(DP["stepOrder"], "步骤序号", str(i)),
                        _dp(DP["outputMassRange_kg"], "得量范围（kg）", output_mass),
                        _dp(DP["yieldRangePercent"], "收率范围（%）", yield_range),
                    ],
                    sub_relationships=step_subs,
                    source_ref={
                        "kind": "paragraph",
                        "section": section.heading,
                        "heading_index": section.heading_index,
                        "paragraph_index": paragraph_index,
                    },
                )
            )

    step_subs_all = [_sub(_DEV + "hasStep", "包含步骤", ctx, s) for s in steps]
    source_ref: dict[str, object]
    if detailed_tables:
        source_table = detailed_tables[0][0]
        source_ref = {
            "kind": "table",
            "section": source_table.section_heading,
            "heading_index": source_table.heading_index,
            "table": source_table.table_index,
            "table_path": source_table.table_path,
        }
    else:
        source_ref = {"kind": "section"}
    if not detailed_tables and narrative.section is not None:
        source_ref["section"] = narrative.section.heading
        source_ref["heading_index"] = narrative.section.heading_index
    if not detailed_tables and narrative.paragraph_index is not None:
        source_ref["paragraph_index"] = narrative.paragraph_index

    route_label = (
        f"{ctx.drug_code} {process_name}工艺路线" if process_name else f"{ctx.drug_code} 合成路线"
    )
    return [
        _endpoint(
            SYNTHESIS_ROUTE_IRI,
            route_label,
            data_properties=route_props,
            sub_relationships=step_subs_all,
            source_ref=source_ref,
        )
    ]


def _risk_endpoints(
    ctx: _Ctx,
    class_iri: str,
    desc_col: str,
    table_needles: tuple,
    section_needles: tuple,
    source_ref: str,
) -> list[dict]:
    out: list[dict] = []
    tbl = ctx.structure.find_table(*table_needles)
    if tbl:
        for row in tbl.rows:
            stage = _row_get(row, "风险环节")
            desc = _row_get(row, desc_col)
            if not (stage or desc):
                continue
            out.append(
                _endpoint(
                    class_iri,
                    stage or desc,
                    data_properties=[
                        _dp(DP["riskCategory"], "风险环节", stage, field_key="risk.category"),
                        _dp(DP["riskDescription"], "风险描述", desc, field_key="risk.description"),
                        _dp(
                            DP["controlMeasure"],
                            "控制措施",
                            _row_get(row, "控制措施"),
                            field_key="risk.control_measure",
                        ),
                    ],
                    source_ref=source_ref,
                )
            )
    # 并入章节列举段（安全评估特有；质量风险一般无对应章节）。
    if section_needles:
        sec = ctx.structure.find_section(*section_needles)
        if sec:
            for para in sec.paras:
                kv = _split_kv(para)
                if kv and kv[1]:  # 跳过「…风险包括：」引导句（冒号后无描述）
                    out.append(
                        _endpoint(
                            class_iri,
                            kv[0],
                            data_properties=[
                                _dp(
                                    DP["riskDescription"],
                                    "风险描述",
                                    kv[1],
                                    field_key="risk.description",
                                )
                            ],
                            source_ref=f"§ {sec.heading}",
                        )
                    )
    return out


def find_safety_risk(ctx: _Ctx) -> list[dict]:
    return _risk_endpoints(
        ctx,
        SAFETY_RISK_IRI,
        "风险描述",
        ("风险环节", "风险描述"),
        ("安全评估",),
        "表 安全风险",
    )


def find_quality_risk(ctx: _Ctx) -> list[dict]:
    return _risk_endpoints(
        ctx,
        QUALITY_RISK_IRI,
        "质量风险",
        ("风险环节", "质量风险"),
        (),
        "表 质量风险",
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
        out.append(
            _endpoint(
                PRODUCTION_RISK_IRI,
                hazid or factor,
                data_properties=[
                    _dp(DP["riskCategory"], "风险类型", hazid, field_key="risk.category"),
                    # These HazID columns have no declared datatype properties;
                    # retain source-backed display values without inventing IRIs.
                    _dp(None, "风险因素", factor, field_key="risk.factors"),
                    _dp(
                        None,
                        "控制前风险水平",
                        _row_get(row, "控制前", "风险控制前", "控制前风险水平"),
                        field_key="risk.pre_control",
                    ),
                    _dp(
                        None,
                        "控制后风险水平",
                        _row_get(row, "控制后", "风险控制后", "控制后风险水平"),
                        field_key="risk.post_control",
                    ),
                    _dp(
                        DP["controlMeasure"],
                        "控制措施",
                        _row_get(row, "控制措施", "风险控制措施"),
                        field_key="risk.control_measure",
                    ),
                    _dp(
                        None,
                        "可追溯性",
                        _row_get(row, "可追溯性", "追溯", "风险控制措施可追溯性"),
                        field_key="risk.traceability",
                    ),
                    _dp(
                        None, "风险状态", _row_get(row, "风险状态", "状态"), field_key="risk.status"
                    ),
                ],
                source_ref="表 生产风险评估",
            )
        )
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
            if not desc:  # 引导句（如「…设备清洗方法如下：」）无描述，跳过
                continue
            out.append(
                _endpoint(
                    CLEANING_PROCESS_IRI,
                    name,
                    data_properties=[_dp(None, "清洗描述", desc)],
                    source_ref=f"§ {sec.heading}",
                )
            )
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
            out.append(
                _endpoint(
                    STORAGE_CONDITION_IRI,
                    name or "存放条件",
                    data_properties=[
                        _dp(DP["packagingType"], "包装方式", _row_get(row, "包装方式")),
                        _dp(DP["storageTemperature"], "存放温度", cond),
                        _dp(
                            DP["lightProtection"],
                            "是否避光",
                            located_computed(
                                "是" if "避光" in cond else ("未说明" if cond else ""), [cond]
                            ),
                        ),
                        _dp(DP["shelfLife"], "有效期/复测期", _row_get(row, "有效期", "复测期")),
                    ],
                    source_ref="表 存放条件",
                )
            )
    return out


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
                if not desc:  # 引导句（如「根据强制降解试验结果：」）无内容，跳过
                    continue
            else:
                prefix, desc = para[:4], para
            cls = _classify_by_synonyms(prefix, synonyms, DEGRADATION_PATHWAY_IRI)
            out.append(
                _endpoint(
                    cls,
                    prefix,
                    data_properties=[
                        _dp(DP["degradationCondition"], "降解条件", desc),
                        _dp(DP["degradationPercent"], "降解百分比（%）", _pct(desc)),
                        _dp(DP["majorDegradant"], "主要降解杂质", _impurity(desc)),
                    ],
                    source_ref=f"§ {sec.heading}",
                )
            )
    return out


# 车间跨度正则——车间号拆分 + 外部事实源聚合逻辑，非字段抽取，不可泛化。


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
            dp = _dp(spec["iri"], spec["label"], sentence[m.start(1) : m.end(1)])
            if dp:
                props.append(dp)
    return props


def find_production_plan(ctx: _Ctx) -> list[dict]:
    """Read the explicit production-plan sentence; no external area enrichment."""
    anchors = ctx.extraction_hints(CLINICAL_SAMPLE_PLAN_IRI).get("anchors", [])
    sentence = _find_anchor_sentence(ctx.structure, anchors)
    if not sentence:
        return []

    props = _extract_sentence_fields(ctx, CLINICAL_SAMPLE_PLAN_IRI, sentence)

    if not props:
        return []
    return [
        _endpoint(
            CLINICAL_SAMPLE_PLAN_IRI,
            "临床备样生产计划",
            data_properties=props,
            source_ref=getattr(sentence, "source_ref", None),
        )
    ]


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
            raise ValueError(f"invalid Finder Profile: {range_iri}")
        if not profile_raw:
            return []
        profile = parse_profile(profile_raw)
        bindings = compile_ontology_bindings(ctx.engine, range_iri, profile)

        result = read_document_profile(ctx.structure, range_iri, profile, bindings)
        if result.degraded_reason:
            raise ValueError(result.degraded_reason)

        synonyms = ctx.subclass_synonyms(range_iri)
        endpoints: list[dict] = []
        for candidate in result.candidates:
            class_iri = _classify_by_synonyms(candidate.subclass_value, synonyms, range_iri)
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
            endpoints.append(
                _endpoint(
                    class_iri,
                    candidate.identifier or profile.label or ctx.class_label(class_iri),
                    source="ontology-profile",
                    data_properties=properties,
                    source_ref=candidate.source_ref,
                )
            )
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
            endpoints.append(
                _endpoint(
                    range_iri,
                    " / ".join(text_parts) or anchors[0],
                    data_properties=props,
                    source_ref=f"表 {anchors[0]}",
                )
            )
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
    return [_endpoint(range_iri, anchors[0], data_properties=props, source_ref=f"§ {anchors[0]}")]


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
        endpoints.append(_endpoint(range_iri, text, source_ref=f"§ {anchors[0]}"))
    return endpoints


# ---------------------------------------------------------------------------
# Method strategy: dispatches by range class (specialised) or generic fallback
# ---------------------------------------------------------------------------


class _MethodStrategy(ExtractionStrategy):
    """Per-method dispatcher: known ranges → specialised finder, unknown → generic."""

    def __init__(self, finders: dict[str, object], generic: object | None = None):
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
    "table_scan": _MethodStrategy(
        {
            STORAGE_CONDITION_IRI: find_storage,
            SAFETY_RISK_IRI: find_safety_risk,
            QUALITY_RISK_IRI: find_quality_risk,
            PRODUCTION_RISK_IRI: find_production_risk,
        },
        generic=_generic_table_scan,
    ),
    "section_kv": _MethodStrategy(
        {
            CLEANING_PROCESS_IRI: find_cleaning,
        },
        generic=_generic_section_kv,
    ),
    "section_paragraph": _MethodStrategy(
        {
            DEGRADATION_PATHWAY_IRI: find_degradation,
        },
        generic=_generic_section_paragraph,
    ),
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


def _make_edge(
    ctx: _Ctx, doc_class: str, subject_label: str, subject_text: str, schema_edge: dict, ep: dict
) -> dict:
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
                        ctx,
                        ep["class_iri"],
                        edges_by_domain,
                        class_hierarchy,
                        depth + 1,
                        max_depth,
                        child_path,
                    )
                subs.append(
                    _sub(
                        edge["predicate_iri"],
                        edge["predicate_label"],
                        ctx,
                        ep,
                    )
                )
    return subs


def _build_class_hierarchy(schema_edges: list[dict]) -> dict[str, set[str]]:
    """Build {subclass_iri → {ancestor range class IRIs}} from schema edges."""
    child_to_parents: dict[str, set[str]] = {}
    for e in schema_edges:
        parent_iri = e["range_class_iri"]
        for sub in e.get("range_subclasses") or []:
            child_to_parents.setdefault(sub["iri"], set()).add(parent_iri)
    return child_to_parents


def extract_relationships(engine, structure: DocStructure, root_class_iri: str) -> dict:
    """Run the isolated baseline strategies against one already parsed source."""
    classification = {
        "doc_class_iri": root_class_iri,
        "label": engine.get_class_label(root_class_iri),
        "score": 0,
        "source": "explicit",
        "signals": [],
    }
    doc_class_iri = classification["doc_class_iri"]
    drug_code = _find_drug_code(structure)
    ctx = _Ctx(structure=structure, drug_code=drug_code, engine=engine)

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
        seed_path = frozenset(
            {
                (schema_edge["predicate_iri"], schema_edge["range_class_iri"]),
            }
        )
        for ep in strategy.find_endpoints(ctx, schema_edge):
            if ep["class_iri"] not in _RANGE_OVERRIDES:
                ep["sub_relationships"] = _extract_sub_relationships(
                    ctx,
                    ep["class_iri"],
                    edges_by_domain,
                    class_hierarchy,
                    path_edges=seed_path,
                )
            edges.append(_make_edge(ctx, doc_class_iri, subject_label, drug_code, schema_edge, ep))

    return {"doc_class": classification, "relationships": edges}
