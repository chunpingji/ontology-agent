"""012: LLM-based gap filling for missing AST coverage slots.

Given a :class:`CoverageManifest` with ``missing_required`` slots and the
source document text, constructs a structured extraction prompt for the
local LLM and returns synthetic edges that can be merged into the
relationship set before a second-pass coverage validation.

All failures are caught and logged — the function NEVER raises; it returns
an empty list on any error (graceful degradation).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.reporting.ast_template import ReportTemplate
    from app.services.reporting.coverage_validator import CoverageManifest

logger = logging.getLogger(__name__)

_MAX_DOC_CHARS = 12_000


def _build_document_text(document_path: str | Path | None) -> str:
    """Extract plain-text from the source document for LLM context."""
    if not document_path:
        return ""
    path = Path(document_path)
    if not path.is_file():
        return ""
    try:
        from app.services.extraction.docx_structure import parse_docx_structure
        struct = parse_docx_structure(path)
        parts: list[str] = []
        for sec in struct.sections:
            if sec.heading:
                parts.append(f"## {sec.heading}")
            parts.extend(sec.paras)
        for tbl in struct.tables:
            parts.append(f"[表格: {tbl.header_sig}]")
            for row in tbl.rows[:10]:
                parts.append(" | ".join(f"{k}: {v}" for k, v in row.items() if v))
        text = "\n".join(parts)
        if len(text) > _MAX_DOC_CHARS:
            text = text[:_MAX_DOC_CHARS] + "\n…（文档已截断）"
        return text
    except Exception:
        logger.warning("无法解析文档用于 LLM 补抽", exc_info=True)
        return ""


def _build_prompt(
    missing_slots: list[dict],
    document_text: str,
) -> str:
    """Build the extraction prompt for the LLM."""
    slot_descriptions = "\n".join(
        f"- slot_id: {s['slot_id']}, label: {s['label']}"
        for s in missing_slots
    )
    return f"""你是一个药品注册信息抽取助手。请从以下文档中提取指定字段的值。

## 需要提取的字段

{slot_descriptions}

## 源文档内容

{document_text}

## 输出要求

请以 JSON 数组格式返回抽取结果，每个元素包含：
- "slot_id": 字段标识符（与上方一致）
- "value": 从文档中提取的值（字符串，如未找到则为 null）
- "source_span": 值在文档中的原文片段（用于溯源，如未找到则为 null）

只返回 JSON 数组，不要包含其他文字。如果所有字段都未找到，返回空数组 []。

示例输出格式：
[{{"slot_id": "subject.name", "value": "阿莫西林胶囊", "source_span": "产品名称：阿莫西林胶囊"}}]"""


def _build_response_format() -> dict:
    """Build ``response_format`` with ``json_schema`` for structured output."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "slot_extraction",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "slots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "slot_id": {"type": "string"},
                                "value": {"type": "string"},
                                "source_span": {"type": "string"},
                            },
                            "required": ["slot_id", "value", "source_span"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["slots"],
                "additionalProperties": False,
            },
        },
    }


def fill_coverage_gaps(
    manifest: CoverageManifest,
    document_path: str | Path | None,
    template: ReportTemplate,
) -> list[dict]:
    """Attempt to fill missing required slots via local LLM extraction.

    Returns a list of synthetic edge dicts with ``source="llm"`` that can
    be merged into the relationship edge set.  Returns ``[]`` on any failure.
    """
    from app.config import settings
    from app.services.llm.local_client import get_local_llm

    if not settings.local_llm_enabled:
        return []

    missing = manifest.missing_required_slots
    if not missing:
        return []

    client = get_local_llm()
    if client is None:
        return []

    document_text = _build_document_text(document_path)
    if not document_text:
        logger.info("LLM 补抽跳过：无可用文档文本")
        return []

    slot_info = [{"slot_id": s.slot_id, "label": s.label} for s in missing]
    prompt = _build_prompt(slot_info, document_text)

    try:
        kwargs: dict = {
            "model": settings.local_llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": settings.local_llm_max_tokens,
            "temperature": settings.local_llm_temperature,
            "response_format": _build_response_format(),
        }
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception:
            logger.info("json_schema 模式不可用，回退到纯提示词模式")
            kwargs.pop("response_format", None)
            response = client.chat.completions.create(**kwargs)
        raw = response.choices[0].message.content or ""
    except Exception:
        logger.warning("LLM 补抽调用失败", exc_info=True)
        return []

    return _parse_llm_response(raw, missing)


def _parse_llm_response(
    raw: str,
    missing_slots: list,
) -> list[dict]:
    """Parse LLM JSON response into synthetic edges.

    Robust to markdown code fences, thinking tags, and other wrapper text.
    Returns ``[]`` on any parse failure.
    """
    text = raw.strip()

    # Strip <think>...</think> blocks (Qwen thinking mode)
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

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
        text = "\n".join(cleaned).strip()

    # Try full parse first (handles json_schema {"slots": [...]} wrapper)
    items: list | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("slots"), list):
            items = parsed["slots"]
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: locate bare JSON array in free-form text
    if items is None:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < 0 or end <= start:
            logger.warning("LLM 补抽返回无法解析为 JSON: %s", text[:200])
            return []
        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            logger.warning("LLM 补抽返回 JSON 解析失败: %s", text[:200])
            return []

    if not isinstance(items, list):
        return []

    valid_ids = {s.slot_id for s in missing_slots}
    edges: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        slot_id = item.get("slot_id", "")
        value = item.get("value")
        source_span = item.get("source_span")
        if not slot_id or slot_id not in valid_ids or not value:
            continue
        edges.append({
            "slot_id": slot_id,
            "value": str(value),
            "source_span": str(source_span) if source_span else None,
            "source": "llm",
        })
    logger.info("LLM 补抽成功提取 %d/%d 个缺失字段", len(edges), len(missing_slots))
    return edges
