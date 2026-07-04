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
from typing import Any

from app.services.llm.local_client import chat_with_schema

logger = logging.getLogger(__name__)

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


def generate_section_narratives(
    edges: list[dict],
    template,
    client,
) -> list[dict]:
    """Report-time: generate prose for each section that carries a 行文 ``prompt``.

    Returns a list of ``{section_id, title, text}`` in template order. Sections
    without a prompt (or with empty LLM output) are skipped. Returns ``[]`` on
    total failure. MUST NOT influence deterministic evaluation (FR-009) — this is
    additive narrative only.
    """
    if not hasattr(template, "sections"):
        return []

    facts_text = _format_facts(edges)
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
        slot_labels = [
            slot.label
            for grp in sec.groups
            for slot in grp.slots
        ]
        labels_text = "、".join(l for l in slot_labels if l) or "（无显式数据字段）"
        user = (
            f"## 行文 Prompt\n{prompt}\n\n"
            f"## 本章节数据字段\n{labels_text}\n\n"
            f"## 抽取事实\n{facts_text}\n\n"
            "请按行文 Prompt 生成本章节正文。"
        )
        result = chat_with_schema(
            client,
            system=system,
            user=user,
            schema=_SECTION_NARRATIVE_SCHEMA,
            schema_name="section_narrative",
        )
        text = (result or {}).get("content", "") if result else ""
        if text and text.strip():
            results.append({
                "section_id": sec.section_id,
                "title": sec.title,
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
