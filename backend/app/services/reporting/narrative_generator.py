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
