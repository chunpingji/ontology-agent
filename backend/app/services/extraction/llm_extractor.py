"""LLM-based entity extraction using Claude API."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You are a pharmaceutical ontology entity extraction assistant.
Given source data and a target ontology class schema, extract structured entities.

Rules:
1. Extract only entities that match the target class
2. Map source fields to the provided ontology property IRIs
3. Return valid JSON array of extracted entities
4. Each entity must have all required properties filled
5. Use exact property IRI keys as specified in the schema
"""


def build_extraction_prompt(
    source_data: list[dict[str, Any]],
    target_class_iri: str,
    property_schema: list[dict],
    few_shot_examples: list[dict] | None = None,
    controlled_vocab: dict[str, list[str]] | None = None,
) -> str:
    prompt_parts = [
        f"## Target Ontology Class\nIRI: {target_class_iri}\n",
        "## Properties to Extract",
    ]

    for prop in property_schema:
        prompt_parts.append(
            f"- `{prop['iri']}` ({prop.get('name', '')}): {prop.get('description', '')}"
            f" [type: {prop.get('range', 'string')}]"
        )

    # 受控取值约束：在生成阶段约束 LLM 仅从受控词表取值，降低自由文本错误
    # （FR-006 / US1-AC3）；后处理 ``tag_controlled_vocab`` 仍作兜底归一化。
    if controlled_vocab:
        prompt_parts.append("\n## 受控取值约束 (Controlled Vocabulary)")
        prompt_parts.append(
            "下列字段若可识别，取值 MUST 归一化到对应受控词表，不得自由发挥："
        )
        for field, terms in controlled_vocab.items():
            prompt_parts.append(f"- {field}: {', '.join(terms)}")

    if few_shot_examples:
        prompt_parts.append("\n## Examples of Correctly Extracted Entities")
        prompt_parts.append("```json")
        prompt_parts.append(json.dumps(few_shot_examples, ensure_ascii=False, indent=2))
        prompt_parts.append("```")

    prompt_parts.append("\n## Source Data to Extract From")
    prompt_parts.append("```json")
    prompt_parts.append(json.dumps(source_data[:50], ensure_ascii=False, indent=2))
    prompt_parts.append("```")

    prompt_parts.append(
        "\n## Instructions\n"
        "Extract entities from the source data above. "
        "Return a JSON array where each element is an object with property IRI keys.\n"
        "Return ONLY the JSON array, no other text."
    )

    return "\n".join(prompt_parts)


async def extract_with_fallback(
    source_data: list[dict[str, Any]],
    target_class_iri: str,
    property_schema: list[dict],
    few_shot_examples: list[dict] | None = None,
    controlled_vocab: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """LLM 抽取并在不可用时回退到结构化原始数据（FR-007, R3）。

    返回 ``(entities, degraded_reason)``：``degraded_reason`` 非空表示已降级，
    调用方应在候选上标记并向 SSE 报 ``degraded=true``，但**不**使作业失败。
    """
    if not settings.anthropic_api_key:
        logger.warning("No Anthropic API key configured; falling back to structured extraction")
        return source_data, "LLM 不可用：未配置 API Key，回退结构化抽取"
    try:
        entities = await extract_entities_with_llm(
            source_data, target_class_iri, property_schema, few_shot_examples,
            controlled_vocab=controlled_vocab,
        )
        if not entities:
            return source_data, "LLM 返回为空，回退结构化抽取"
        return entities, None
    except Exception as exc:  # pragma: no cover - 网络/SDK 异常路径
        logger.exception("LLM extraction failed; falling back to structured extraction")
        return source_data, f"LLM 调用失败，回退结构化抽取：{type(exc).__name__}"


async def extract_entities_with_llm(
    source_data: list[dict[str, Any]],
    target_class_iri: str,
    property_schema: list[dict],
    few_shot_examples: list[dict] | None = None,
    controlled_vocab: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Call Claude API to extract entities from source data."""
    if not settings.anthropic_api_key:
        logger.warning("No Anthropic API key configured, returning source data as-is")
        return source_data

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = build_extraction_prompt(
        source_data, target_class_iri, property_schema, few_shot_examples,
        controlled_vocab=controlled_vocab,
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1]
        if response_text.endswith("```"):
            response_text = response_text[:-3]

    try:
        entities = json.loads(response_text)
        if not isinstance(entities, list):
            entities = [entities]
        return entities
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON")
        return []
