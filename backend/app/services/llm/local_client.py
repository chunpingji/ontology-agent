"""Local LLM client wrapper (OpenAI-compatible endpoint).

Returns ``None`` when ``local_llm_enabled=False`` or the ``openai`` package
is not installed — callers gate on the return value so the pipeline degrades
gracefully to zero-LLM behaviour.

012: ``get_local_llm()`` — client factory.
013: ``chat_with_schema()`` — structured-output helper with prompt-based fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def get_local_llm():
    """Return an ``openai.OpenAI`` client pointed at the local LLM, or *None*.

    Conditions that yield *None* (no error raised):
    - ``settings.local_llm_enabled`` is ``False``
    - The ``openai`` package is not importable (missing optional dep)
    """
    from app.config import settings

    if not settings.local_llm_enabled:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — LLM gap filling disabled")
        return None

    return OpenAI(
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
        timeout=300.0,
    )


def chat_with_schema(
    client,
    *,
    system: str,
    user: str,
    schema: dict,
    schema_name: str = "response",
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Send a chat completion with structured JSON output, falling back to prompt-based parsing.

    Attempts ``response_format=json_schema`` first; on API rejection or
    malformed content, retries with the schema appended to the user prompt
    and parses the first JSON object from the response (mirrors the
    ``_build_response_format`` / ``_parse_llm_response`` pattern from
    ``llm_gap_filler``).

    Returns the parsed dict, or ``None`` on total failure.
    """
    from app.config import settings

    _model = model or settings.local_llm_model
    _temperature = temperature if temperature is not None else settings.local_llm_temperature
    _max_tokens = max_tokens or settings.local_llm_max_tokens

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": schema,
        },
    }

    # Attempt 1: structured json_schema
    try:
        resp = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=response_format,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )
        text = resp.choices[0].message.content or ""
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        logger.debug("json_schema attempt failed, falling back to prompt-based parsing", exc_info=True)

    # Attempt 2: prompt-based fallback
    # Disable Qwen 3 thinking mode (/no_think) — thinking blocks consume
    # most of max_tokens and leave the actual JSON truncated.
    schema_instruction = (
        "\n\n/no_think\n"
        "You MUST respond with ONLY a valid JSON object matching this schema "
        "(no markdown, no explanation, no thinking):\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )
    # Double the token budget for fallback — if thinking mode can't be
    # suppressed, the extra headroom lets the JSON complete after the block.
    fallback_max_tokens = _max_tokens * 2
    try:
        resp = client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user + schema_instruction},
            ],
            temperature=_temperature,
            max_tokens=fallback_max_tokens,
        )
        raw = resp.choices[0].message.content or ""
        logger.info(
            "chat_with_schema fallback response length=%d, prefix=%.200s",
            len(raw), raw[:200],
        )
        return _extract_json_object(raw)
    except Exception:
        logger.warning("chat_with_schema: both attempts failed", exc_info=True)
        return None


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """Extract the first JSON object from potentially wrapped LLM output."""
    text = raw.strip()

    # Strip <think>...</think> blocks (Qwen thinking mode)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Handle unclosed <think> tag (output truncated at max_tokens)
    if "<think>" in text:
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()

    # Strip markdown code fences
    if "```" in text:
        lines = text.split("\n")
        inside = False
        cleaned: list[str] = []
        for line in lines:
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                cleaned.append(line)
        if cleaned:
            text = "\n".join(cleaned).strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Locate first { ... } in free-form text
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
