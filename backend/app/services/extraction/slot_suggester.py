"""013: LLM-assisted slot suggestion for AST template design.

Two-round LLM prompting:
  Round 1 — document structure analysis → sections/groups + candidate labels + evidence
  Round 2 — slot mapping given round-1 + existing template → concrete slots (dedup)

016 US1 — ontology grounding: when a ``doc_class_iri`` is supplied, the read-only
``get_relation_schema(doc_class_iri)`` (Principle II) is injected into both rounds
as a **selection menu** of the relationship edges the doc entity type participates
in. The LLM *selects* ``(predicate_iri, range_class_iri)`` pairs from that menu and
emits ``CoverageDeclaration``s — it can never invent an IRI, so declarations reference
ontology **types**, never sample individuals (FR-003). Positions that look
data-sourced but bind to no edge surface as explicit ``unresolved_candidates`` for
author disposition — they are NEVER silently collapsed to ``manual`` (FR-008a). The
old exact-string ``_bind_ontology_iris`` (which did exactly that collapse) is gone.
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
        # 016 US1: the LLM SELECTS relationship edges from the injected ontology
        # menu (never invents IRIs). `required` is deliberately NOT exposed — an
        # AI-proposed binding is required-by-default (FR-005a); the author demotes
        # it later in the editor.
        "coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "predicate_iri": {"type": "string"},
                    "range_class_iri": {"type": "string"},
                    "label": {"type": "string"},
                    "evidence_span": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["predicate_iri", "range_class_iri"],
                "additionalProperties": False,
            },
        },
        # 016 US1: data-sourced-looking positions that bind to no ontology edge —
        # surfaced for explicit author disposition, never auto-classified manual.
        "unresolved_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "proposed_label": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reason_unbound": {"type": "string"},
                },
                "required": ["proposed_label"],
                "additionalProperties": False,
            },
        },
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
    doc_class_iri: str | None = None,
) -> dict[str, Any]:
    """Run two-round LLM slot suggestion and return structured result.

    Returns a dict matching ``SuggestSlotsResponse`` shape:
    ``{sections, total_suggested, skipped_duplicates, document_summary, truncated,
    coverage, unresolved_candidates}``.

    ``content_json`` (tiptap) — when provided, each slot gets a deterministic
    ``source_ref`` anchor (``§ 标题`` / 原文片段) derived from the structure so
    the frontend ``WordViewer`` can locate and highlight the evidence in the
    faithful preview (013 — replaces the char-offset ``evidence_offset`` link).

    ``doc_class_iri`` (016 US1) — the document entity type. When it and
    ``ontology_engine`` are both present, the engine's read-only relationship
    schema is injected into both rounds and the LLM selects edges to cover; the
    result carries ``coverage`` (``CoverageDeclaration``s, required-by-default) and
    ``unresolved_candidates``. Absent either input, both lists come back empty and
    the classic section/slot output is unchanged (FR-012).
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
            "coverage": [],
            "unresolved_candidates": [],
        }

    if len(text) > _MAX_DOC_CHARS:
        text = text[:_MAX_DOC_CHARS] + "\n…（文档已截断）"

    # ── Ontology grounding menu (016 US1) — built once, injected into both rounds.
    # `schema_edges` doubles as the schema-membership filter that guarantees the
    # LLM's coverage output references TYPES, not sample individuals (FR-003).
    schema_edges, ontology_context = _build_ontology_context(
        ontology_engine, doc_class_iri,
    )

    # ── Round 1: structure analysis ──────────────────────────────────────
    r1_system = (
        "你是 GMP 合规文档结构分析专家。分析给定文档，识别其章节（sections）、"
        "分组（groups）和候选数据字段（candidates）。对每个候选字段，标注原文证据片段。"
    )
    r1_user = (
        f"请分析以下文档的结构，提取所有可作为报告模板数据插槽的候选字段：\n\n{text}"
        f"{ontology_context}"
    )

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
            "coverage": [],
            "unresolved_candidates": [],
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
        f"{ontology_context}"
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
            "coverage": [],
            "unresolved_candidates": [],
        }

    raw_slots = r2.get("slots", [])
    skipped = r2.get("skipped_duplicates", 0)
    logger.info("Round-2 OK: %d slots, %d skipped", len(raw_slots), skipped)

    # ── Ontology-grounded coverage (016 US1) ─────────────────────────────
    # The LLM SELECTED edges from the injected schema menu; keep only those whose
    # (predicate, range) is actually in the schema (drops invented individuals,
    # FR-003), dedup our own output (S7), and pass through unresolved candidates.
    # Remaining slots are typed llm_extraction — a graph-sourced position is NEVER
    # defaulted to manual (S1/FR-008a); the editor lets the author reclassify.
    coverage = _extract_coverage(r2, schema_edges, doc_class_iri)
    unresolved = _extract_unresolved(r2)
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
        "coverage": coverage,
        "unresolved_candidates": unresolved,
    }


def _build_ontology_context(
    engine, doc_class_iri: str | None,
) -> tuple[list[dict], str]:
    """Read the doc type's relationship menu once (016 US1); return ``(edges, prompt)``.

    ``edges`` is the raw ``get_relation_schema`` output (the schema-membership filter
    the coverage extractor later applies). ``prompt`` is a compact **hop-1** view
    injected verbatim into both LLM rounds — only direct relationships of the doc
    type are offered for authoring (single-hop authoring unit; deeper reach via
    range-type follow-on templates, see ``_range_data_properties`` in coverage_validator).

    Read-only (Principle II). Any engine hiccup degrades to ``([], "")`` — the
    suggester still produces sections/slots (FR-012).
    """
    if engine is None or not doc_class_iri:
        return [], ""
    try:
        schema_edges = engine.get_relation_schema(doc_class_iri) or []
    except Exception:
        logger.warning("get_relation_schema failed; ontology grounding disabled", exc_info=True)
        return [], ""

    hop1 = [e for e in schema_edges if e.get("hop") == 1]
    if not hop1:
        return schema_edges, ""

    lines = [
        "\n\n【本体关系菜单】以下是本文档实体类型在本体中可覆盖的关系。"
        "请从中**选择**（切勿虚构 IRI，切勿引用具体实例个体）需要在本报告中体现的关系，"
        "在 coverage 中输出所选边的 predicate_iri 与 range_class_iri；"
        "文中看似取数但无法匹配任何下列关系的字段，放入 unresolved_candidates：",
    ]
    for e in hop1:
        props = e.get("range_data_properties") or []
        prop_hint = (
            "（目标属性：" + "、".join(p.get("label", "") for p in props) + "）"
            if props else ""
        )
        lines.append(
            f"- {e.get('predicate_label', '')} → {e.get('range_class_label', '')}"
            f"{prop_hint} "
            f"[predicate_iri={e.get('predicate_iri', '')}"
            f"; range_class_iri={e.get('range_class_iri', '')}]"
        )
    return schema_edges, "\n".join(lines)


def _extract_coverage(
    r2: dict, schema_edges: list[dict], doc_class_iri: str | None,
) -> list[dict]:
    """Turn LLM-selected edges into ``CoverageDeclaration`` dicts (016 US1).

    Keeps only entries whose ``(predicate_iri, range_class_iri)`` is a real schema
    edge — an invented individual/type is silently dropped (FR-003 / S2). Dedups by
    ``(doc_class_iri, predicate_iri, range_class_iri)`` (S7). Required-by-default
    (FR-005a): the LLM cannot lower it (``required`` is not in the round-2 schema).
    """
    if not doc_class_iri:
        return []
    edge_by_key = {
        (e.get("predicate_iri"), e.get("range_class_iri")): e for e in schema_edges
    }
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in r2.get("coverage", []) or []:
        pred = entry.get("predicate_iri")
        rng = entry.get("range_class_iri")
        edge = edge_by_key.get((pred, rng))
        if edge is None:  # selection-not-invention: not a real schema edge → drop
            continue
        key = (doc_class_iri, pred, rng)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "kind": "ontology_relation",
                "doc_class_iri": doc_class_iri,
                "predicate_iri": pred,
                "range_class_iri": rng,
                "required": True,  # FR-005a
                "label": entry.get("label") or edge.get("predicate_label"),
            }
        )
    return out


def _extract_unresolved(r2: dict) -> list[dict]:
    """Pass through the LLM's unbindable positions as candidates (016 US1 / FR-008a)."""
    out: list[dict] = []
    for entry in r2.get("unresolved_candidates", []) or []:
        label = (entry.get("proposed_label") or "").strip()
        if not label:
            continue
        out.append(
            {
                "proposed_label": label,
                "evidence": entry.get("evidence"),
                "reason_unbound": entry.get("reason_unbound"),
                "suggested_disposition": None,
            }
        )
    return out


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
