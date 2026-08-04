"""013: LLM narrative generation for risk assessment reports (US3).

Generates style-consistent prose content for:
  - ``subject_description`` — assessment subject overview
  - Per-dimension risk narratives
  - ``conclusion`` — risk assessment conclusion

Uses extracted facts as the sole data source (SC-006) and template prose
sections as few-shot style examples (FR-008).  Output is transient — not
persisted (FR-007).  MUST NOT influence deterministic evaluation (FR-009).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.services.llm.local_client import chat_with_schema
from app.services.reporting.ast_template import coverage_key
from app.services.reporting.placeholder_util import PLACEHOLDER_RE, substitute
from app.services.reporting.source_ref import format_source_ref

logger = logging.getLogger(__name__)

# A section's 行文 Prompt references its data fields as ``{{占位符}}`` whose text is the
# slot label (see :func:`generate_section_prompt`). The regex + single-pass substitution
# live in :mod:`placeholder_util` (shared with the deterministic risk matrix); this alias
# preserves the historical private name for local references.
_PLACEHOLDER_RE = PLACEHOLDER_RE

_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject_description": {"type": "string"},
        "conclusion": {"type": "string"},
        "dimension_narratives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string"},
                    "narrative": {"type": "string"},
                },
                "required": ["dimension", "narrative"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["subject_description", "conclusion", "dimension_narratives"],
    "additionalProperties": False,
}


def generate_narratives(
    edges: list[dict],
    template,
    client,
) -> dict[str, str]:
    """Generate narrative content from extracted facts.

    Returns a dict mapping field names to generated text, e.g.::

        {
            "subject_description": "...",
            "conclusion": "...",
            "narrative.人员": "...",
        }

    Returns ``{}`` on any failure.
    """
    facts_text = _format_facts(edges)
    if not facts_text.strip():
        return {}

    style_context = _build_style_context(template)

    system = (
        "你是 GMP 合规风险评估报告撰写专家。根据提供的文档抽取数据（事实），"
        "生成风险评估报告的叙述性内容。语言风格应与参考模板保持一致。"
        "仅基于提供的事实生成内容，不要编造数据。"
    )
    user = (
        f"## 抽取事实\n\n{facts_text}\n\n"
        f"## 参考模板风格\n\n{style_context}\n\n"
        "请生成以下内容：\n"
        "1. subject_description: 评估对象描述（一段话）\n"
        "2. conclusion: 风险评估结论（一段话）\n"
        "3. dimension_narratives: 每个风险维度的叙述（与评估表维度对应）"
    )

    result = chat_with_schema(
        client,
        system=system,
        user=user,
        schema=_NARRATIVE_SCHEMA,
        schema_name="narrative_generation",
    )
    if result is None:
        logger.warning("Narrative generation LLM call failed")
        return {}

    narratives: dict[str, str] = {}
    if result.get("subject_description"):
        narratives["subject_description"] = result["subject_description"]
    if result.get("conclusion"):
        narratives["conclusion"] = result["conclusion"]
    for dim in result.get("dimension_narratives", []):
        key = f"narrative.{dim.get('dimension', '')}"
        if dim.get("narrative"):
            narratives[key] = dim["narrative"]

    return narratives


# --------------------------------------------------------------------------- #
# 015: per-section 行文 Prompt — design-time authoring + report-time generation
# --------------------------------------------------------------------------- #

_SECTION_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
    "additionalProperties": False,
}

_SECTION_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


def generate_section_prompt(
    client,
    *,
    section_title: str,
    slot_labels: list[str],
    sample_text: str,
) -> str:
    """Design-time: derive a reusable 行文 Prompt for one section from the sample.

    The returned prompt is a natural-language writing instruction that references
    the section's data fields (as ``{{占位符}}``). It is saved into the section's
    ``prompt`` field; at report time :func:`generate_section_narratives` calls the
    LLM with it to fuse the section's slot values into prose. Returns ``""`` on
    failure.
    """
    labels_text = "、".join(l for l in slot_labels if l) or "（无显式数据字段）"
    system = (
        "你是 GMP 合规报告模板设计专家。为报告的某个章节设计「行文 Prompt」——"
        "一段可复用的写作指令，描述该章节应如何按行文规范组织内容"
        "（自然语言叙述 + 必要的表格/图），并用 {{占位符}} 引用数据字段。"
        "只输出写作指令本身，不要输出示例正文。"
    )
    user = (
        f"## 章节标题\n{section_title}\n\n"
        f"## 该章节的数据字段（插槽标签）\n{labels_text}\n\n"
        f"## 样例文档（供参考行文风格与结构）\n{sample_text[:4000]}\n\n"
        "请生成该章节的行文 Prompt：说明写作口吻、结构、需引用哪些字段"
        "（用 {{字段名}} 表示），以及表格/图的呈现方式。"
    )

    result = chat_with_schema(
        client,
        system=system,
        user=user,
        schema=_SECTION_PROMPT_SCHEMA,
        schema_name="section_prompt",
    )
    if result is None:
        logger.warning("Section prompt generation LLM call failed")
        return ""
    return result.get("prompt", "") or ""


def _field_value_from_edges(label: str, edges: list[dict]) -> str | None:
    """Resolve a slot's value from the extraction edges by data-property label —
    the fallback when the deterministic manifest carries no value for it (e.g. a
    semantic slot, or a label the coverage scoping didn't surface). ``None`` if absent."""
    for e in edges:
        for dp in e.get("object_data_properties") or []:
            if dp.get("label") == label and dp.get("value") not in (None, ""):
                return str(dp["value"])
    return None


def _format_field_values(field_values: list[tuple[str, str | None]]) -> str:
    """The section's ``{{占位符}} → 取值`` substitution table for the LLM. Fields with no
    resolvable value are shown as 「待补充」 so the model states them honestly rather than
    echoing the raw placeholder."""
    lines = [
        f"- {{{{{label}}}}} → {value if value not in (None, '') else '（待补充）'}"
        for label, value in field_values
    ]
    return "\n".join(lines) if lines else "（本章节无显式字段取值）"


def _substitute_placeholders(
    text: str, field_values: list[tuple[str, str | None]]
) -> str:
    """Deterministic safety net: replace any ``{{label}}`` still present in the LLM
    output with the resolved slot value (or 「待补充」). Guarantees no raw ``{{...}}``
    leaks into the rendered report even when the model ignores the substitution
    instruction — the exact defect users hit ("大量占位符没有替换")."""
    by_label = {
        str(label).strip(): (str(value).strip() if value not in (None, "") else "")
        for label, value in field_values
    }
    return substitute(text, by_label, missing=lambda _label: "（待补充）")


def generate_section_narratives(
    edges: list[dict],
    template,
    client,
    *,
    assessment_rows: list | None = None,
    manifest: Any | None = None,
    engine: Any | None = None,
    skip_semantic_sections: bool = False,
    progress_fn: Callable[[dict, int, int], None] | None = None,
) -> list[dict]:
    """Report-time: generate prose for each section that carries a 行文 ``prompt``.

    Returns a list of ``{section_id, title, text}`` in template order. Sections
    without a prompt (or with empty LLM output) are skipped. Returns ``[]`` on
    total failure. MUST NOT influence deterministic evaluation (FR-009) — this is
    additive narrative only.

    ``assessment_rows`` (deterministic ``RiskRow[]``) and ``manifest``
    (``CoverageManifest``) are injected as READ-ONLY context so the LLM can quote the
    already-decided risk levels and coverage status verbatim instead of guessing
    (docs/declarative-rule-report-generator-binding-design.md §5.4). Both ``None`` ⇒
    the user prompt is byte-identical to the pre-§5.4 behaviour.

    ``skip_semantic_sections`` (report path): a section's 行文 narrative and its
    semantic slots are two renderings of the SAME ``Section.prompt`` (016+ semantic
    slots project the section's prompt + coverage). When both exist, the docx would
    render both — duplicate prose AND a duplicate LLM call. With this flag on, a
    section that has ≥1 ``semantic`` slot is skipped here (its content comes from
    :func:`generate_semantic_slots`). Off by default so the design-time single-section
    preview (:func:`preview_section_narrative`) still renders a semantic section's
    prompt verbatim.
    """
    if not hasattr(template, "sections"):
        return []

    from app.services.reporting.fact_sources import FactContext

    all_facts_text = _format_facts(edges)
    rules_text = _format_rule_results(assessment_rows, edges=edges) if assessment_rows else ""
    fact_ctx = FactContext(
        edges=edges,
        facts=None,
        assessment_rows=assessment_rows or [],
        engine=engine,
    )
    # Deterministic slot values, keyed by slot_id, from the coverage manifest — the
    # source for filling a section's ``{{占位符}}`` (fix: placeholders leaking into prose).
    manifest_by_slot = {s.slot_id: s for s in getattr(manifest, "slots", None) or []}
    results: list[dict] = []
    system = (
        "你是 GMP 合规报告撰写专家。根据给定的「行文 Prompt」与「抽取事实」，"
        "生成该章节的正文内容（自然语言叙述，可含 Markdown 表格）。"
        "仅基于提供的事实，不要编造数据；缺失的数据据实说明。"
    )
    narrative_sections = [
        sec for sec in template.sections
        if getattr(sec, "prompt", None) and str(sec.prompt).strip()
        and not (
            skip_semantic_sections
            and any(
                getattr(slot.source, "kind", None) == "semantic"
                for grp in sec.groups
                for slot in grp.slots
            )
        )
    ]
    total_sections = len(narrative_sections)
    for sec in narrative_sections:
        prompt = getattr(sec, "prompt", None)
        # 016+: when the section declares ontology coverage, scope the facts to those
        # relationships — per binding, under its own label — exactly like semantic slots
        # (:func:`generate_semantic_slots`). A section covering several relations (e.g.
        # 报告会签 = 评估小组 + 审批人小组) then feeds each roster under its own heading, so
        # the LLM never bleeds one roster into another. Sections without coverage keep the
        # full fact dump (byte-identical to pre-016 behaviour).
        coverage = getattr(sec, "coverage", None) or []
        if coverage:
            ont_parts: list[str] = []
            for b in coverage:
                label = getattr(b, "label", None) or coverage_key(b)
                if getattr(b, "kind", None) == "fact_source":
                    facts_str = _format_fact_source(b, fact_ctx)
                else:
                    facts_str = _format_coverage_facts(b, edges, engine)
                if facts_str:
                    ont_parts.append(f"### {label}\n{facts_str}")
            facts_text = "\n\n".join(ont_parts) if ont_parts else all_facts_text
        elif assessment_rows:
            facts_text = _auto_group_facts(edges)
        else:
            facts_text = all_facts_text
        slot_labels = [
            slot.label
            for grp in sec.groups
            for slot in grp.slots
        ]
        labels_text = "、".join(l for l in slot_labels if l) or "（无显式数据字段）"
        # The 行文 Prompt references data fields as {{占位符}} (== slot labels). Resolve
        # each field's value (manifest → edge-label fallback) and hand the LLM an explicit
        # {{占位符}}→取值 table so it substitutes real values; a deterministic post-pass
        # then guarantees no raw {{...}} survives. Built only when the prompt carries
        # placeholders → byte-identical prompt for placeholder-free sections (golden-master
        # parity: the value table and reminder never appear otherwise).
        has_placeholders = "{{" in str(prompt)
        field_values: list[tuple[str, str | None]] = []
        if has_placeholders:
            for grp in sec.groups:
                for slot in grp.slots:
                    if not slot.label:
                        continue
                    sc = manifest_by_slot.get(slot.slot_id)
                    value = sc.value if sc and sc.value else None
                    if value is None:
                        value = _field_value_from_edges(slot.label, edges)
                    field_values.append((slot.label, value))
        coverage_text = (
            _format_coverage_status(manifest, sec.section_id) if manifest else ""
        )
        user_parts = [
            f"## 行文 Prompt\n{prompt}",
            f"## 本章节数据字段\n{labels_text}",
        ]
        if has_placeholders:
            user_parts.append(
                "## 字段取值（请将行文 Prompt 中的 {{占位符}} 替换为下列对应取值；"
                "无取值的字段据实说明为「待补充」，正文中不得保留任何 {{...}} 占位符）\n"
                + _format_field_values(field_values)
            )
        user_parts.append(f"## 抽取事实\n{facts_text}")
        if rules_text:
            user_parts.append(
                f"## 风险评估结果（确定性结论，必须原样引用，不可自行推断）\n{rules_text}"
            )
        if coverage_text:
            user_parts.append(f"## 本章节覆盖状态\n{coverage_text}")
        # Keep the closing instruction byte-identical when no deterministic context is
        # injected, so golden-master section-narrative output is unchanged.
        closing = "请按行文 Prompt 生成本章节正文。"
        if rules_text:
            closing += "引用风险评估结果时必须与上述确定性结果严格一致。"
        if has_placeholders:
            closing += "务必用「字段取值」中的实际取值替换所有 {{占位符}}，正文中不得出现任何 {{...}}。"
        user_parts.append(closing)
        user = "\n\n".join(user_parts)
        result = chat_with_schema(
            client,
            system=system,
            user=user,
            schema=_SECTION_NARRATIVE_SCHEMA,
            schema_name="section_narrative",
        )
        text = (result or {}).get("content", "") if result else ""
        if has_placeholders:
            text = _substitute_placeholders(text, field_values)
        if text and text.strip():
            entry = {
                "section_id": sec.section_id,
                "title": sec.title,
                "text": text,
            }
            results.append(entry)
            if progress_fn:
                progress_fn(dict(entry), len(results), total_sections)
    return results


def preview_section_narrative(
    edges: list[dict],
    section,
    client,
    *,
    assessment_rows: list | None = None,
    manifest: Any | None = None,
    engine: Any | None = None,
) -> str:
    """Design-time preview: run ONE section's 行文 Prompt through the report-time
    narrative path against real extracted ``edges`` (not sample text), returning
    the prose it would produce.

    ``section`` is a ReportTemplate ``Section`` whose ``prompt`` already holds the
    (possibly-unsaved) value under test. Reuses :func:`generate_section_narratives`
    VERBATIM via a one-section template, so the preview is byte-faithful to what the
    real report renders. Returns ``""`` when the section has no prompt or the LLM
    yields nothing.
    """
    from app.services.reporting.ast_template import ReportTemplate

    mini = ReportTemplate(template_id="__preview__", sections=[section])
    results = generate_section_narratives(
        edges,
        mini,
        client,
        assessment_rows=assessment_rows,
        manifest=manifest,
        engine=engine,
    )
    return results[0]["text"] if results else ""


# --------------------------------------------------------------------------- #
# 016+: semantic slots — LLM synthesis projecting Section.coverage + Section.prompt
# --------------------------------------------------------------------------- #

_SEMANTIC_SLOT_SCHEMA = _SECTION_NARRATIVE_SCHEMA  # {content: str}

_SEMANTIC_SLOTS_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["slot_id", "content"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["slots"],
    "additionalProperties": False,
}


def _extract_entity_context(edges: list[dict]) -> str:
    """Extract key entity references (drug, workshop, equipment) for risk-row context."""
    from app.services.extraction.equipment_source import (
        iter_equipment_edges,
        workshop_from_enrichment,
        workshop_from_loose_scan,
    )

    drug_name = ""
    for e in edges:
        if "DrugProduct" in (e.get("object_class_iri") or ""):
            drug_name = str(e.get("object_text") or e.get("subject_text") or "")
            break

    _EQ_WS_RE = re.compile(r"[A-Za-z]{1,3}(642|644|646)\d{2,}")
    eq_codes: list[str] = []
    workshops: set[str] = set()
    for eq in iter_equipment_edges(edges):
        code = str(eq.get("object_text") or "").strip()
        if code and code not in eq_codes:
            eq_codes.append(code)
        raw_ref = eq.get("source_ref")
        # 车间归组优先级（与 risk_report_generator._detect_workshop **完全一致**，共享解析器）：
        # 档案富化权威标签 → 设备编号锚定 → 业务文本兜底扫描。权威解析读 format_source_ref 展示串
        # （其中含标签文本，且不会把 raw dict 交给 re.search——那正是「got 'dict'」崩溃的原因）；
        # 兜底扫描传**原始** source_ref，只扫业务文本键、不扫结构坐标（Codex round-3 #1）。
        ws = workshop_from_enrichment(format_source_ref(raw_ref))
        if not ws and code:
            m = _EQ_WS_RE.fullmatch(code)
            if m:
                ws = f"{m.group(1)}车间"
        if not ws:
            ws = workshop_from_loose_scan(raw_ref)
        if ws:
            workshops.add(ws)

    parts: list[str] = []
    if drug_name:
        parts.append(f"- 评估药物：{drug_name}")
    if workshops:
        parts.append(f"- 生产车间：{'、'.join(sorted(workshops))}")
    if eq_codes:
        summary = "、".join(eq_codes[:6])
        if len(eq_codes) > 6:
            summary += f"等共{len(eq_codes)}台"
        parts.append(f"- 使用设备：{summary}")
    return "\n".join(parts)


def _format_rule_results(rows: list | None, edges: list[dict] | None = None) -> str:
    """Format deterministic risk rows as read-only LLM context (FR-009). Capped so a
    long matrix can't blow the local model's context window."""
    if not rows:
        return "（无风险评估结果）"
    parts: list[str] = []
    if edges:
        entity_ctx = _extract_entity_context(edges)
        if entity_ctx:
            parts.append(f"### 评估对象上下文\n{entity_ctx}\n")
    for row in rows:
        hazid = getattr(row, "hazid", "") or ""
        factors = getattr(row, "contributing_factors", "") or ""
        line = f"- [{hazid}] {factors}：初始风险 = {getattr(row, 'pre_control_level', '')}"
        post = getattr(row, "post_control_level", "")
        if post:
            line += f" → 残余风险 = {post}（{getattr(row, 'status', '')}）"
        measures = getattr(row, "control_measures", "")
        if measures:
            line += f"\n  控制措施：{measures}"
        parts.append(line)
    return "\n".join(parts[:30])


_RISK_ENTITY_GROUPS: list[tuple[str, list[str]]] = [
    ("评估对象（药物产品）", ["DrugProduct"]),
    ("使用设备", ["Equipment", "ProcessEquipment"]),
    ("生产区域", ["ProductionArea"]),
    ("共线评估数据", ["SharedLineAssessmentData"]),
    ("生产风险评估（源文件）", ["ProductionRiskAssessment"]),
    ("安全风险", ["SafetyRiskAssessment"]),
    ("质量风险", ["QualityRiskAssessment"]),
    ("合成路线与工艺", ["SynthesisRoute", "SynthesisStep",
                        "ProcessIntermediate", "CrudeProduct"]),
    ("清洗与残留", ["CleaningProcess", "Residue"]),
    ("存放条件", ["StorageCondition"]),
]


def _auto_group_facts(edges: list[dict]) -> str:
    """Group edges by entity type under labeled headings for risk sections."""
    grouped: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []
    for edge in edges[:100]:
        obj_iri = edge.get("object_class_iri", "")
        local_name = obj_iri.rsplit("/", 1)[-1] if "/" in obj_iri else obj_iri
        matched = False
        for label, needles in _RISK_ENTITY_GROUPS:
            if any(n in local_name for n in needles):
                grouped.setdefault(label, []).append(edge)
                matched = True
                break
        if not matched:
            ungrouped.append(edge)
    parts: list[str] = []
    budget = {"remaining": _MAX_TOTAL_SUBFACT_LINES, "truncated": False}
    for label, needles in _RISK_ENTITY_GROUPS:
        grp_edges = grouped.get(label)
        if not grp_edges:
            continue
        lines: list[str] = []
        for e in grp_edges:
            lines.append(_format_fact_line(e, 0))
            _append_sub_facts(e, lines, depth=1, budget=budget)
        parts.append(f"### {label}\n" + "\n".join(lines))
    if ungrouped:
        lines = []
        for e in ungrouped:
            lines.append(_format_fact_line(e, 0))
            _append_sub_facts(e, lines, depth=1, budget=budget)
        parts.append("### 其他信息\n" + "\n".join(lines))
    if budget["truncated"]:
        parts.append(f"…（嵌套子关系总数超过 {_MAX_TOTAL_SUBFACT_LINES} 行上限，其余已省略）")
    return "\n\n".join(parts) if parts else "（无提取到的事实数据）"


def _format_coverage_status(manifest: Any | None, section_id: str) -> str:
    """Format the coverage positions loosely associated with a section as read-only
    context. Best-effort match on ``slot_id`` (coverage positions are not section-keyed)."""
    if manifest is None or not section_id:
        return ""
    status_zh = {
        "filled": "已填充", "inferred": "推理得出",
        "missing_required": "缺失(必填)", "blank_optional": "空白(可选)",
        "manual": "待人工填写", "dismissed": "已忽略",
    }
    slots = [
        s for s in getattr(manifest, "slots", [])
        if section_id in s.slot_id or s.slot_id.startswith(section_id)
    ]
    if not slots:
        return ""
    return "\n".join(
        f"- {s.label}: {status_zh.get(s.status, s.status)}"
        + (f" = {s.value}" if s.value else "")
        for s in slots
    )


def _format_coverage_facts(binding: Any, edges: list[dict], engine: Any | None) -> str:
    """The extraction facts scoped to one ``ontology_relation`` coverage binding —
    the *associated ontology* graph slice feeding a semantic slot. ``""`` when the
    relationship's target type is absent from the edges."""
    from app.services.reporting.coverage_validator import coverage_scoped_edges

    scoped = coverage_scoped_edges(binding, edges, engine)
    return _format_facts(scoped) if scoped else ""


def _format_fact_source(binding: Any, ctx: Any) -> str:
    """The read-only rows a ``fact_source`` coverage binding resolves to (016+).
    ``""`` when the source is unknown / not-yet-implemented / empty."""
    from app.services.reporting.fact_sources import resolve_fact_source

    rows = resolve_fact_source(binding, ctx)
    return "\n".join(
        f"- {r.get('label', '')}: {r.get('value', '')}"
        for r in rows
        if r.get("value")
    )


def generate_semantic_slots(
    edges: list[dict],
    template,
    client,
    *,
    facts: Any | None = None,  # accepted for symmetry / future providers
    assessment_rows: list | None = None,
    manifest: Any | None = None,
    engine: Any | None = None,
) -> list[dict]:
    """Report-time: synthesize each ``source.kind=='semantic'`` slot's text.

    A semantic slot is a PROJECTION of its Section (016+): the LLM fuses
      1. ``prompt``           — slot's own, or the parent ``Section.prompt``,
      2. *associated ontology* — extraction facts scoped to the section's
         ``coverage`` relationships selected by ``coverage_refs`` ([]=all), and
      3. deterministic rule results (``assessment_rows``) — READ-ONLY (FR-009).

    Returns ``[{slot_id, section_id, text}]`` in document order. Slots with neither a
    prompt nor any selected coverage are skipped; empty LLM output is skipped. Never
    influences deterministic evaluation.

    **Batched**: slots within the same section are sent in a single LLM call
    (one call per section instead of one per slot) to reduce total LLM round-trips.
    """
    if not hasattr(template, "sections"):
        return []

    from app.services.reporting.fact_sources import FactContext

    facts_text_all = _format_facts(edges)
    rules_text = _format_rule_results(assessment_rows, edges=edges) if assessment_rows else ""
    fact_ctx = FactContext(
        edges=edges,
        facts=facts,
        assessment_rows=assessment_rows or [],
        engine=engine,
    )
    results: list[dict] = []
    system = (
        "你是 GMP 合规报告撰写专家。根据「行文 Prompt」「关联本体（源文档关系图谱事实）」"
        "与「确定性风险评估结论」，合成各语义化插槽的正文内容（自然语言叙述，可含 "
        "Markdown 表格）。仅基于提供的信息，不要编造数据；引用风险评估结论时必须与给定的"
        "确定性结果严格一致，不得自行推断风险等级。"
    )

    for sec in template.sections:
        coverage = getattr(sec, "coverage", []) or []
        cov_by_key = {coverage_key(b): b for b in coverage}

        # ── Pass 1: collect eligible semantic slots with their context ──
        slot_entries: list[dict] = []
        for grp in sec.groups:
            for slot in grp.slots:
                src = slot.source
                if getattr(src, "kind", None) != "semantic":
                    continue
                prompt = getattr(src, "prompt", None) or getattr(sec, "prompt", None)
                refs = getattr(src, "coverage_refs", None) or []
                selected = (
                    [cov_by_key[k] for k in refs if k in cov_by_key]
                    if refs
                    else list(coverage)
                )
                if (not prompt or not str(prompt).strip()) and not selected:
                    continue

                ont_parts: list[str] = []
                for b in selected:
                    label = getattr(b, "label", None) or coverage_key(b)
                    if getattr(b, "kind", None) == "fact_source":
                        facts_str = _format_fact_source(b, fact_ctx)
                    else:
                        facts_str = _format_coverage_facts(b, edges, engine)
                    if facts_str:
                        ont_parts.append(f"### {label}\n{facts_str}")
                if ont_parts:
                    ontology_text = "\n\n".join(ont_parts)
                elif assessment_rows:
                    ontology_text = _auto_group_facts(edges)
                else:
                    ontology_text = facts_text_all

                slot_entries.append({
                    "slot_id": slot.slot_id,
                    "label": slot.label,
                    "prompt": prompt or "（无显式行文指令，请依据关联本体与事实源客观叙述）",
                    "ontology_text": ontology_text,
                })

        if not slot_entries:
            continue

        # ── Pass 2: batch LLM call for all slots in this section ──
        slot_blocks: list[str] = []
        for entry in slot_entries:
            block = (
                f"### 插槽 `{entry['slot_id']}` — {entry['label']}\n"
                f"**行文 Prompt:** {entry['prompt']}\n\n"
                f"**关联本体:**\n{entry['ontology_text']}"
            )
            slot_blocks.append(block)

        user_parts = [
            f"请为以下 {len(slot_entries)} 个语义化插槽分别生成正文。"
            f"返回的 slots 数组中每个元素的 slot_id 必须与下方给出的 slot_id 严格一致。\n",
            "\n\n---\n\n".join(slot_blocks),
        ]
        if rules_text:
            user_parts.append(
                f"## 风险评估结果（确定性结论，必须原样引用，不可自行推断）\n{rules_text}"
            )
        user = "\n\n".join(user_parts)

        result = chat_with_schema(
            client,
            system=system,
            user=user,
            schema=_SEMANTIC_SLOTS_BATCH_SCHEMA,
            schema_name="semantic_slots_batch",
        )

        if not result or not result.get("slots"):
            continue

        returned = {s["slot_id"]: s["content"] for s in result["slots"] if s.get("content", "").strip()}
        for entry in slot_entries:
            text = returned.get(entry["slot_id"], "")
            if text and text.strip():
                results.append({
                    "slot_id": entry["slot_id"],
                    "section_id": sec.section_id,
                    "label": entry["label"],
                    "text": text,
                })
    return results


# Deepest nesting we descend into ``sub_relationships`` when formatting facts. The
# extractor caps relationship nesting at a few hops; this also guards against cyclic data.
_MAX_FACT_DEPTH = 4
# Per-parent cap on nested sub-relationship lines, so one large subtree (e.g. a long
# synthesis route) can't crowd sibling top-level edges out of the LLM context.
_MAX_SUBFACTS_PER_PARENT = 20
# Global cap on *nested* sub-fact lines across the whole fact list. Top-level edges are
# always emitted (they are the primary signal); only ``sub_relationships`` expansion draws
# this budget down. Restores a hard global bound the per-parent cap alone can't give —
# depth×breadth nesting can add detail but can never blow past the LLM context window.
_MAX_TOTAL_SUBFACT_LINES = 300


def _format_fact_line(edge: dict, depth: int) -> str:
    """Render one edge as a bullet line.

    ``depth == 0`` reproduces the original single-level format byte-for-byte. ``depth > 0``
    is a nested sub-relationship: indented, carrying its connecting predicate label and, for
    external master data, its provenance — so the LLM keeps the parent→child context (e.g.
    临床备样生产计划 → 生产车间 → 642车间).
    """
    obj_class = edge.get("object_class_iri", "")
    obj_text = edge.get("object_text", "")
    if depth == 0:
        subj_text = edge.get("subject_text", "")
        line = f"- {obj_class}: {subj_text} → {obj_text}"
    else:
        indent = "  " * depth
        pred = edge.get("predicate_label", "") or edge.get("predicate_iri", "")
        line = f"{indent}- 关系「{pred}」: {obj_class} → {obj_text}"
    for dp in edge.get("object_data_properties") or []:
        label = dp.get("label", "")
        value = dp.get("value", "")
        if label and value:
            line += f" ({label}: {value})"
    if depth > 0 and edge.get("object_source") == "external":
        src = format_source_ref(edge.get("source_ref"))
        if src:
            line += f" 〔{src}〕"
    return line


def _append_sub_facts(parent: dict, parts: list[str], depth: int, budget: dict) -> None:
    """Recursively append a parent edge's ``sub_relationships`` as indented fact lines.

    ``budget`` is a mutable ``{"remaining": int, "truncated": bool}`` cell shared across the
    whole recursion: it caps *total* nested lines so a deep/wide relation tree can neither
    overflow the LLM context nor starve later top-level facts of it. Non-dict items are
    filtered out before counting, so the per-parent "omitted" tally reflects only the valid
    sub-relationships that were actually dropped.
    """
    if depth > _MAX_FACT_DEPTH:
        return
    subs = parent.get("sub_relationships")
    if not isinstance(subs, list):
        return
    valid = [sub for sub in subs if isinstance(sub, dict)]
    if not valid:
        return
    if budget["remaining"] <= 0:
        # Real sub-facts exist but the global budget is spent — flag so the caller emits
        # the truncation marker (rather than dropping them silently at the parent boundary).
        budget["truncated"] = True
        return
    emitted = 0
    for sub in valid:
        if budget["remaining"] <= 0:
            budget["truncated"] = True
            return
        if emitted >= _MAX_SUBFACTS_PER_PARENT:
            parts.append("  " * depth + f"- …（另有 {len(valid) - emitted} 项子关系已省略）")
            break
        parts.append(_format_fact_line(sub, depth))
        emitted += 1
        budget["remaining"] -= 1
        _append_sub_facts(sub, parts, depth + 1, budget)


def _format_facts(edges: list[dict]) -> str:
    """Format extraction edges into a readable fact list for the LLM context.

    Descends into each edge's ``sub_relationships`` (nested master-data facts — e.g. a
    production plan's 生产车间 rows sourced from external master data) so they reach the
    model, indented under their parent with the connecting predicate + provenance. Edges
    that carry no ``sub_relationships`` render identically to the pre-nesting formatter.

    The historical "first 100 top-level edges" budget is preserved, and every top-level edge
    is emitted before any of its nested detail. Nested *fact* lines draw down a separate
    global budget (:data:`_MAX_TOTAL_SUBFACT_LINES`); once exhausted, remaining sub-facts are
    dropped and a single global truncation marker is appended. Truncation-hint lines (the
    per-parent "另有 N 项" notes and the global marker) are not charged to the budget, so the
    total stays a small constant — at most 100 top-level + _MAX_TOTAL_SUBFACT_LINES nested +
    a bounded handful of hint lines — regardless of relation-tree depth or breadth.
    """
    parts: list[str] = []
    budget = {"remaining": _MAX_TOTAL_SUBFACT_LINES, "truncated": False}
    for edge in edges[:100]:
        parts.append(_format_fact_line(edge, 0))
        _append_sub_facts(edge, parts, depth=1, budget=budget)
    if budget["truncated"]:
        parts.append(f"- …（嵌套子关系总数超过 {_MAX_TOTAL_SUBFACT_LINES} 行上限，其余已省略）")
    return "\n".join(parts)


def _build_style_context(template) -> str:
    """Extract prose sections from template as few-shot style examples."""
    parts: list[str] = []
    if hasattr(template, "sections"):
        for sec in template.sections:
            parts.append(f"### {sec.title}")
            for grp in sec.groups:
                parts.append(f"  {grp.title}")
    if not parts:
        parts.append("（模板无可用参考内容）")
    return "\n".join(parts[:30])
