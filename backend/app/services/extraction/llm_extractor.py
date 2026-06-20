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


async def extract_entities_with_llm(
    source_data: list[dict[str, Any]],
    target_class_iri: str,
    property_schema: list[dict],
    few_shot_examples: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Call Claude API to extract entities from source data."""
    if not settings.anthropic_api_key:
        logger.warning("No Anthropic API key configured, returning source data as-is")
        return source_data

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = build_extraction_prompt(
        source_data, target_class_iri, property_schema, few_shot_examples
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
