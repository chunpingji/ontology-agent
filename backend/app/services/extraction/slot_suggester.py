"""013: LLM-assisted slot suggestion for AST template design.

Two-round LLM prompting:
  Round 1 — document structure analysis → sections/groups + candidate labels + evidence
  Round 2 — slot mapping given round-1 + existing template → concrete slots (dedup)

Ontology IRI binding resolves each candidate against the published Owlready2 World
to set ``source_kind`` and ``source_hint``.  Read-only (Principle II).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.llm.local_client import chat_with_schema

logger = logging.getLogger(__name__)

_MAX_DOC_CHARS = 12_000

# ── JSON schemas for structured LLM output ──────────────────────────────────

_ROUND1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "groups": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "candidates": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "evidence_span": {"type": "string"},
                                            "evidence_offset": {"type": "integer"},
                                        },
                                        "required": ["label", "evidence_span"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["title", "candidates"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "groups"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["document_summary", "sections"],
    "additionalProperties": False,
}

_ROUND2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string"},
                    "label": {"type": "string"},
                    "section": {"type": "string"},
                    "group": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence_span": {"type": "string"},
                    "evidence_offset": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["slot_id", "label", "section", "group", "confidence",
                             "evidence_span", "reason"],
                "additionalProperties": False,
            },
        },
        "skipped_duplicates": {"type": "integer"},
    },
    "required": ["slots", "skipped_duplicates"],
    "additionalProperties": False,
}


def suggest_slots(
    client,
    document_text: str,
    existing_template: dict | None = None,
    max_suggestions: int = 50,
    ontology_engine=None,
    content_json: dict | None = None,
) -> dict[str, Any]:
    """Run two-round LLM slot suggestion and return structured result.

    Returns a dict matching ``SuggestSlotsResponse`` shape:
    ``{sections, total_suggested, skipped_duplicates, document_summary, truncated}``.

    ``content_json`` (tiptap) — when provided, each slot gets a deterministic
    ``source_ref`` anchor (``§ 标题`` / 原文片段) derived from the structure so
    the frontend ``WordViewer`` can locate and highlight the evidence in the
    faithful preview (013 — replaces the char-offset ``evidence_offset`` link).
    """
    text = document_text.strip()
    logger.info("suggest_slots called: document_text length=%d", len(text))
    if not text:
        return {
            "sections": [],
            "total_suggested": 0,
            "skipped_duplicates": 0,
            "document_summary": "文档为空或仅含空白字符，无法进行分析。",
            "truncated": False,
        }

    if len(text) > _MAX_DOC_CHARS:
        text = text[:_MAX_DOC_CHARS] + "\n…（文档已截断）"

    # ── Round 1: structure analysis ──────────────────────────────────────
    r1_system = (
        "你是 GMP 合规文档结构分析专家。分析给定文档，识别其章节（sections）、"
        "分组（groups）和候选数据字段（candidates）。对每个候选字段，标注原文证据片段。"
    )
    r1_user = f"请分析以下文档的结构，提取所有可作为报告模板数据插槽的候选字段：\n\n{text}"

    r1 = chat_with_schema(
        client,
        system=r1_system,
        user=r1_user,
        schema=_ROUND1_SCHEMA,
        schema_name="structure_analysis",
    )
    if r1 is None:
        logger.warning("Round-1 LLM call failed — check backend logs for chat_with_schema details")
        return {
            "sections": [],
            "total_suggested": 0,
            "skipped_duplicates": 0,
            "document_summary": "LLM 结构分析失败，请检查本地 LLM 日志。",
            "truncated": False,
        }

    r1_candidates = sum(
        len(g.get("candidates", []))
        for s in r1.get("sections", [])
        for g in s.get("groups", [])
    )
    document_summary = r1.get("document_summary", "")
    logger.info(
        "Round-1 OK: %d sections, %d candidates, summary=%.80s",
        len(r1.get("sections", [])), r1_candidates, document_summary,
    )

    # ── Round 2: slot mapping + dedup ────────────────────────────────────
    r2_system = (
        "你是 GMP 报告模板设计专家。根据文档结构分析结果，生成具体的数据插槽定义。"
        "每个插槽需要 slot_id（snake_case）、label、所属 section/group、置信度、证据片段和理由。"
    )
    existing_context = ""
    if existing_template:
        existing_context = (
            "\n\n以下是已有模板结构，请跳过语义上已被覆盖的插槽（不要输出重复项），"
            "并在 skipped_duplicates 中计数跳过的数量：\n"
            + json.dumps(existing_template, ensure_ascii=False, indent=1)
        )

    r2_user = (
        f"文档结构分析结果：\n{json.dumps(r1, ensure_ascii=False, indent=1)}"
        f"{existing_context}"
        f"\n\n请生成不超过 {max_suggestions} 个数据插槽定义。"
    )

    r2 = chat_with_schema(
        client,
        system=r2_system,
        user=r2_user,
        schema=_ROUND2_SCHEMA,
        schema_name="slot_mapping",
    )
    if r2 is None:
        logger.warning("Round-2 LLM call failed — check backend logs for chat_with_schema details")
        return {
            "sections": [],
            "total_suggested": 0,
            "skipped_duplicates": 0,
            "document_summary": document_summary or "LLM 插槽映射失败，请检查本地 LLM 日志。",
            "truncated": False,
        }

    raw_slots = r2.get("slots", [])
    skipped = r2.get("skipped_duplicates", 0)
    logger.info("Round-2 OK: %d slots, %d skipped", len(raw_slots), skipped)

    # ── Ontology IRI binding ─────────────────────────────────────────────
    if ontology_engine is not None:
        _bind_ontology_iris(raw_slots, ontology_engine)
    else:
        for slot in raw_slots:
            slot["source_kind"] = "llm_extraction"
            slot["source_hint"] = None

    # ── Structural source_ref binding (deterministic, from tiptap) ───────
    # 从文档结构推导锚点，保证锚点文本真实存在于渲染 DOM 中——绝不让 LLM
    # 自造锚点（会导致 WordViewer 的 textContent.includes 静默匹配失败）。
    if content_json is not None:
        for slot in raw_slots:
            slot["source_ref"] = derive_source_ref(
                slot.get("evidence_span", ""), content_json,
            )

    # ── Cap + group into section hierarchy ───────────────────────────────
    truncated = len(raw_slots) > max_suggestions
    if truncated:
        raw_slots = raw_slots[:max_suggestions]

    sections = _group_into_sections(raw_slots)

    return {
        "sections": sections,
        "total_suggested": len(raw_slots),
        "skipped_duplicates": skipped,
        "document_summary": document_summary,
        "truncated": truncated,
    }


def _bind_ontology_iris(slots: list[dict], engine) -> None:
    """Resolve each slot against the Owlready2 World for IRI binding (FR-002)."""
    try:
        dp_labels = set(engine.data_property_labels())
        dp_domain_classes = {label: iri for iri, label in engine.data_property_domain_classes()}
    except Exception:
        logger.warning("Ontology property lookup failed; defaulting to llm_extraction", exc_info=True)
        for slot in slots:
            slot["source_kind"] = "llm_extraction"
            slot["source_hint"] = None
        return

    for slot in slots:
        label = slot.get("label", "")
        matched_iri = None

        if label in dp_labels:
            matched_iri = dp_domain_classes.get(label)
            if not matched_iri:
                try:
                    props = engine.get_data_properties_by_domain("")
                    for p in props:
                        if p.get("label") == label or p.get("name") == label:
                            matched_iri = p.get("iri")
                            break
                except Exception:
                    pass

        if matched_iri:
            slot["source_kind"] = "extraction"
            slot["source_hint"] = matched_iri
        else:
            slot["source_kind"] = "llm_extraction"
            slot["source_hint"] = None


def _group_into_sections(slots: list[dict]) -> list[dict]:
    """Group flat slot list into section → group → slots hierarchy."""
    section_map: dict[str, dict[str, list[dict]]] = {}
    for slot in slots:
        sec_title = slot.get("section", "未分组")
        grp_title = slot.get("group", "默认")
        section_map.setdefault(sec_title, {}).setdefault(grp_title, []).append(slot)

    sections = []
    for sec_title, groups in section_map.items():
        sec_groups = []
        for grp_title, grp_slots in groups.items():
            sec_groups.append({"title": grp_title, "slots": grp_slots})
        sections.append({"title": sec_title, "groups": sec_groups})
    return sections


def build_document_text(document_path: str | Path | None) -> str:
    """Extract plain-text from a DOCX for slot suggestion (reuses docx_structure)."""
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
        logger.warning("无法解析文档用于插槽建议", exc_info=True)
        return ""


# ── tiptap (structured) → LLM text + structural anchors ─────────────────────
# 013: 样例文档在后台解析为 tiptap（忠于原文结构），LLM 分析文本由 tiptap
# 服务端派生——避免「解析成文本送前台再送回」丢失结构。产出的文本风格与
# build_document_text 一致（## 标题、表格行以 " | " 拼接），保持 prompt 形态不变。


def _node_text(node: Any) -> str:
    """递归拼接一个 tiptap 节点下的全部文本。"""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "") or ""
    return "".join(_node_text(c) for c in (node.get("content") or []))


def tiptap_to_text(content_json: dict | None) -> str:
    """tiptap ProseMirror JSON → 结构标注纯文本（供 LLM 分析）。"""
    if not content_json:
        return ""
    lines: list[str] = []

    def emit(node: dict) -> None:
        t = node.get("type")
        if t == "heading":
            level = (node.get("attrs") or {}).get("level", 1) or 1
            txt = _node_text(node).strip()
            if txt:
                lines.append(f"{'#' * min(int(level), 6)} {txt}")
        elif t == "paragraph":
            txt = _node_text(node).strip()
            if txt:
                lines.append(txt)
        elif t == "listItem":
            txt = _node_text(node).strip()
            if txt:
                lines.append(f"- {txt}")
        elif t == "table":
            lines.append("[表格]")
            for row in node.get("content") or []:
                if row.get("type") != "tableRow":
                    continue
                cells = [_node_text(c).strip() for c in (row.get("content") or [])]
                row_text = " | ".join(c for c in cells if c)
                if row_text:
                    lines.append(row_text)
        else:
            for c in node.get("content") or []:
                emit(c)

    emit(content_json)
    text = "\n".join(lines)
    if len(text) > _MAX_DOC_CHARS:
        text = text[:_MAX_DOC_CHARS] + "\n…（文档已截断）"
    return text


def _collect_blocks(content_json: dict) -> list[tuple[str, str | None, bool]]:
    """遍历 tiptap，返回 ``[(块文本, 最近标题, 是否标题)]``（保序）。

    标题/段落/列表项/表格单元格各作一个可定位块；单元格不再下钻其内部段落，
    避免与段落块重复。表格块的「最近标题」为其前置章节标题。
    """
    blocks: list[tuple[str, str | None, bool]] = []
    current_heading: list[str | None] = [None]

    def walk(node: dict) -> None:
        t = node.get("type")
        if t == "heading":
            txt = _node_text(node).strip()
            if txt:
                current_heading[0] = txt
                blocks.append((txt, txt, True))
            return
        if t in ("paragraph", "listItem"):
            txt = _node_text(node).strip()
            if txt:
                blocks.append((txt, current_heading[0], False))
            return
        if t in ("tableCell", "tableHeader"):
            txt = _node_text(node).strip()
            if txt:
                blocks.append((txt, current_heading[0], False))
            return
        for c in node.get("content") or []:
            walk(c)

    walk(content_json)
    return blocks


def derive_source_ref(evidence_span: str | None, content_json: dict | None) -> str | None:
    """由证据片段 + tiptap 结构派生锚点，保证锚点真实存在于渲染 DOM。

    命中标题块 → ``§ <标题>``（前端高亮整节）；命中章节内段落/单元格 →
    ``§ <最近标题>``；无标题的顶层块 → 返回块原文（前端按关键词命中 p/li/单元格）。
    命中失败返回 ``None``（前端回退到 evidence_span 本身）。
    """
    span = (evidence_span or "").strip()
    if not span or not content_json:
        return None
    blocks = _collect_blocks(content_json)

    def anchor(text: str, heading: str | None, is_heading: bool) -> str:
        if is_heading:
            return f"§ {text}"
        if heading:
            return f"§ {heading}"
        return text

    # 1) 正向包含：块文本含证据片段（段落/标题/单元格常见）。
    for text, heading, is_heading in blocks:
        if span in text:
            return anchor(text, heading, is_heading)
    # 2) 反向包含：证据片段较长（如跨列表格行经 " | " 拼接）时，块文本落在片段内。
    for text, heading, is_heading in blocks:
        if len(text) >= 6 and text in span:
            return anchor(text, heading, is_heading)
    return None
