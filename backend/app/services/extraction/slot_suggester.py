"""013/016: LLM-assisted ontology-coverage suggestion for AST template design.

016 收敛：本模块只产出**类型级本体覆盖**，不再产出任何逐插槽 ``llm_extraction`` 建议流。
点击「AI分析」→ ``{document_summary, coverage}``。

Two-round LLM prompting:
  Round 1 — document structure analysis → sections/groups + candidate labels + evidence
  Round 2 — 从注入的【本体关系菜单】**选择**需覆盖的关系边 → ``coverage``

016 US1 — ontology grounding: a ``doc_class_iri`` is **required** (作者在模板必选
「关联文档类型」→ 得到本体图谱). The read-only ``get_relation_schema(doc_class_iri)``
(Principle II) is injected into both rounds as a **selection menu** of the relationship
edges the doc entity type participates in. The LLM *selects* ``(predicate_iri,
range_class_iri)`` pairs from that menu and emits ``CoverageDeclaration``s — it can
never invent an IRI, so declarations reference ontology **types**, never sample
individuals (FR-003). Positions that bind to no menu edge are **silently ignored** —
AI 分析只呈现能绑定到本体的覆盖边，不再抛出「原文取数候选」（取代 FR-008a 的
``unresolved_candidates`` 流）。
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
    },
    "required": [],
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
    """Run two-round LLM ontology-coverage suggestion and return structured result.

    Returns a dict matching ``SuggestSlotsResponse`` shape:
    ``{document_summary, coverage, sections}``. ``sections`` is the Round-1
    **structural skeleton** (``sections[].groups[].candidates[]{label, …}``,
    per ``_ROUND1_SCHEMA``) returned **verbatim** — the编辑器 materializes it into
    author-fillable semantic slots. This骨架 carries **no** ontology IRI binding: the
    old per-slot取数流 (``_bind_ontology_iris`` exact-match → force-collapse to
    ``manual``, defect 1 root cause) stays removed; only inert structure is surfaced.

    ``doc_class_iri`` (016 US1) — the document entity type (作者在模板必选「关联文档
    类型」). When it and ``ontology_engine`` are both present, the engine's read-only
    relationship schema is injected into both rounds and the LLM selects edges to
    cover; the result carries ``coverage`` (``CoverageDeclaration``s, required-by-
    default). Absent either input, ``coverage`` comes back empty (graceful
    degradation, FR-012) and only ``document_summary`` is populated.

    ``content_json`` / ``max_suggestions`` — accepted for caller/endpoint signature
    stability; no longer used now that the逐插槽建议流 is removed.
    """
    text = document_text.strip()
    logger.info("suggest_slots called: document_text length=%d", len(text))
    if not text:
        return {
            "document_summary": "文档为空或仅含空白字符，无法进行分析。",
            "coverage": [],
            "sections": [],
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
            "document_summary": "LLM 结构分析失败，请检查本地 LLM 日志。",
            "coverage": [],
            "sections": [],
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

    # ── Round 2: ontology-coverage selection (016) ───────────────────────
    r2_system = (
        "你是 GMP 报告本体覆盖分析专家。根据文档结构分析结果与下方【本体关系菜单】，"
        "从菜单中**选择**本报告需要体现的关系边，在 coverage 中输出所选边的 "
        "predicate_iri 与 range_class_iri（切勿虚构 IRI、切勿引用具体实例个体）。"
        "无法匹配任何菜单关系的字段一律忽略，不要输出。"
    )
    existing_context = ""
    if existing_template:
        existing_context = (
            "\n\n以下是已有模板结构（含已声明的覆盖边），请跳过语义上已被覆盖的关系，"
            "不要重复输出：\n"
            + json.dumps(existing_template, ensure_ascii=False, indent=1)
        )

    r2_user = (
        f"文档结构分析结果：\n{json.dumps(r1, ensure_ascii=False, indent=1)}"
        f"{existing_context}"
        f"{ontology_context}"
        f"\n\n请只输出 coverage（所选本体关系边），不要输出任何其它内容。"
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
            "document_summary": document_summary or "LLM 覆盖分析失败，请检查本地 LLM 日志。",
            "coverage": [],
            # Round-1 succeeded → surface its skeleton even when coverage selection failed.
            "sections": r1.get("sections", []),
        }

    # ── Ontology-grounded coverage (016 US1) ─────────────────────────────
    # The LLM SELECTED edges from the injected schema menu; keep only those whose
    # (predicate, range) is actually in the schema (drops invented individuals,
    # FR-003) and dedup our own output (S7). Positions that bind to no menu edge are
    # silently ignored — AI 分析只呈现能绑定到本体的覆盖边（取代 FR-008a 的候选流）。
    coverage = _extract_coverage(r2, schema_edges, doc_class_iri)
    logger.info("Round-2 OK: %d coverage edges", len(coverage))

    return {
        "document_summary": document_summary,
        "coverage": coverage,
        # Round-1 structural skeleton, returned verbatim (no IRI binding). 编辑器
        # 把每个 candidate 物化为可作者填写的 semantic 槽；本体覆盖仍走 coverage 叠加。
        "sections": r1.get("sections", []),
    }


def _supplemented_schema_edges(engine, doc_class_iri: str | None) -> list[dict]:
    """本文档类型在只读本体中的关系边（``get_relation_schema``）。

    **单一事实源**：AI 覆盖菜单（:func:`_build_ontology_context`）与「文档类型是否已建模」
    能力探测（:func:`coverage_capable` → ``/coverage-doc-classes`` 端点）共用此函数，保证
    作者化 UI 的**启用集**与 suggester 实际能产出的**覆盖集**逐字一致，不再发散。

    引擎缺失 / ``doc_class_iri`` 为空 / 引擎异常 → ``[]``（优雅降级，FR-012）。
    """
    if engine is None or not doc_class_iri:
        return []
    try:
        return engine.get_relation_schema(doc_class_iri) or []
    except Exception:
        logger.warning("get_relation_schema failed; ontology grounding disabled", exc_info=True)
        return []


def coverage_capable(engine, doc_class_iri: str | None) -> bool:
    """该文档类型在本体中是否已建模**可覆盖关系**（≥1 条 hop-1 边）。

    ``/coverage-doc-classes`` 端点据此判定作者化 UI 应启用哪些文档类型（feature 016 决策：
    **仅启用已建模类型**）。与 :func:`_build_ontology_context` 走同一
    :func:`_supplemented_schema_edges`——「能选」当且仅当 AI 分析确能产出覆盖边。
    """
    return any(e.get("hop") == 1 for e in _supplemented_schema_edges(engine, doc_class_iri))


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
    suggester still returns ``document_summary`` with empty coverage (FR-012).
    """
    schema_edges = _supplemented_schema_edges(engine, doc_class_iri)
    hop1 = [e for e in schema_edges if e.get("hop") == 1]
    if not hop1:
        return schema_edges, ""

    lines = [
        "\n\n【本体关系菜单】以下是本文档实体类型在本体中可覆盖的关系。"
        "请从中**选择**（切勿虚构 IRI，切勿引用具体实例个体）需要在本报告中体现的关系，"
        "在 coverage 中输出所选边的 predicate_iri 与 range_class_iri。"
        "无法匹配下列任何关系的字段一律忽略，不要输出：",
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


# ── tiptap (structured) → LLM text ──────────────────────────────────────────
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
