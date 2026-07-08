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
from typing import Any

from app.services.llm.local_client import chat_with_schema
from app.services.reporting.ast_template import coverage_key

logger = logging.getLogger(__name__)

# A section's 行文 Prompt references its data fields as ``{{占位符}}`` whose text is the
# slot label (see :func:`generate_section_prompt`). This matches one such token so the
# section narrator can substitute the real slot value in place of the raw placeholder.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

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
    if not text or "{{" not in text:
        return text
    by_label = {
        str(label).strip(): (str(value).strip() if value not in (None, "") else "")
        for label, value in field_values
    }

    def _repl(m: re.Match) -> str:
        return by_label.get(m.group(1).strip()) or "（待补充）"

    return _PLACEHOLDER_RE.sub(_repl, text)


def generate_section_narratives(
    edges: list[dict],
    template,
    client,
    *,
    assessment_rows: list | None = None,
    manifest: Any | None = None,
    engine: Any | None = None,
    skip_semantic_sections: bool = False,
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
    rules_text = _format_rule_results(assessment_rows) if assessment_rows else ""
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
    for sec in template.sections:
        prompt = getattr(sec, "prompt", None)
        if not prompt or not str(prompt).strip():
            continue
        # De-dup with semantic slots (016+): skip a section whose content is already
        # produced per-slot by generate_semantic_slots (report path only).
        if skip_semantic_sections and any(
            getattr(slot.source, "kind", None) == "semantic"
            for grp in sec.groups
            for slot in grp.slots
        ):
            continue
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
            results.append({
                "section_id": sec.section_id,
                "title": sec.title,
                "text": text,
            })
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


def _format_rule_results(rows: list | None) -> str:
    """Format deterministic risk rows as read-only LLM context (FR-009). Capped so a
    long matrix can't blow the local model's context window."""
    if not rows:
        return "（无风险评估结果）"
    parts: list[str] = []
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
    rules_text = _format_rule_results(assessment_rows) if assessment_rows else ""
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
                ontology_text = "\n\n".join(ont_parts) if ont_parts else facts_text_all

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


def _format_facts(edges: list[dict]) -> str:
    """Format extraction edges into a readable fact list."""
    parts: list[str] = []
    for edge in edges:
        obj_class = edge.get("object_class_iri", "")
        obj_text = edge.get("object_text", "")
        subj_text = edge.get("subject_text", "")
        line = f"- {obj_class}: {subj_text} → {obj_text}"
        for dp in edge.get("object_data_properties") or []:
            label = dp.get("label", "")
            value = dp.get("value", "")
            if label and value:
                line += f" ({label}: {value})"
        parts.append(line)
    return "\n".join(parts[:100])


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
