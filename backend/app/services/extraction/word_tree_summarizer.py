"""Offline-safe, bottom-up summaries for the deterministic Word chapter tree."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.services.extraction.docx_structure import (
    ChapterNode,
    DocStructure,
    LayerMetadata,
    PageMetadata,
    PageNode,
    ParagraphBlock,
    _block_material,
)
from app.services.llm.local_client import chat_with_schema

logger = logging.getLogger(__name__)

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "content_summary": {"type": "string"},
                },
                "required": ["node_id", "content_summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summaries"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """你是制药研发 Word 文档的分层摘要器。
只概括提供的原文和子节点摘要，不补充外部知识，不推断未给出的结论、数值、主体或因果。
保留药品、设备、工艺、风险名称和编号。父章节概括主题，不逐项机械重复。
使用简洁中文。只返回符合 JSON Schema 的对象。"""


def _chapters(root: ChapterNode) -> list[ChapterNode]:
    result: list[ChapterNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return result


def _depths(root: ChapterNode) -> dict[str, int]:
    result: dict[str, int] = {}

    def visit(node: ChapterNode, depth: int) -> None:
        result[node.node_id] = depth
        for child in node.children:
            visit(child, depth + 1)

    visit(root, 0)
    return result


def _material_for_blocks(
    structure: DocStructure,
    block_ids: Iterable[str],
    *,
    include_headings: bool,
) -> str:
    by_id = {block.block_id: block for block in structure.blocks}
    parts: list[str] = []
    for block_id in block_ids:
        block = by_id.get(block_id)
        if block is None:
            continue
        if (
            isinstance(block, ParagraphBlock)
            and block.heading_level > 0
            and not include_headings
        ):
            continue
        material = _block_material(block, structure.tables).strip()
        if material:
            parts.append(material)
    return "\n".join(parts)


def _page_material(structure: DocStructure, page: PageNode) -> str:
    body = _material_for_blocks(structure, page.block_ids, include_headings=True)
    return f"章节页 {page.ordinal_in_leaf}\n{body}".strip()


def _chapter_material(structure: DocStructure, node: ChapterNode) -> str:
    parts = [f"章节路径：{' / '.join(node.path) if node.path else node.heading}"]
    direct = _material_for_blocks(
        structure, node.direct_block_ids, include_headings=False
    )
    if direct:
        parts.append(f"当前章节直接内容：\n{direct}")
    if node.children:
        child_lines = [
            f"- {child.heading}：{child.layer_metadata.content_summary or '无摘要'}"
            for child in node.children
        ]
        parts.append("直接子章节摘要：\n" + "\n".join(child_lines))
    elif node.pages:
        page_lines = [
            f"- 章节页 {page.ordinal_in_leaf}："
            f"{page.page_metadata.content_summary or '无摘要'}"
            for page in node.pages
        ]
        parts.append("页摘要：\n" + "\n".join(page_lines))
    return "\n\n".join(parts)


def _extractive_summary(material: str, maximum: int) -> str:
    text = " ".join(material.split())
    if not text:
        return "本节点未包含可提取的正文内容。"
    if len(text) <= maximum:
        return text
    candidate = text[:maximum]
    cut = max(candidate.rfind(mark) for mark in "。！？；")
    if cut >= maximum // 2:
        return candidate[:cut + 1]
    return candidate.rstrip("，,；;：:") + "…"


def _mark_disabled(metadata: LayerMetadata | PageMetadata) -> None:
    if metadata.summary_source == "empty":
        return
    metadata.content_summary = None
    metadata.summary_status = "disabled"
    metadata.summary_source = "none"
    metadata.summary_model = None
    metadata.generated_at = None


def _mark_extractive_fallback(
    metadata: LayerMetadata | PageMetadata,
    material: str,
    generated_at: str,
) -> None:
    if metadata.summary_source == "empty":
        return
    metadata.content_summary = _extractive_summary(
        material, settings.word_tree_summary_max_output_chars
    )
    metadata.summary_status = "failed"
    metadata.summary_source = "extractive_fallback"
    metadata.summary_model = None
    metadata.generated_at = generated_at


def _batches(targets: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for target in targets:
        chars = len(target["material"])
        if current and (
            len(current) >= settings.word_tree_summary_max_nodes_per_batch
            or current_chars + chars > settings.word_tree_summary_max_batch_chars
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(target)
        current_chars += chars
    if current:
        batches.append(current)
    return batches


def _apply_batch(client, targets: list[dict[str, Any]]) -> None:
    active = [
        target for target in targets
        if target["metadata"].summary_source != "empty"
    ]
    if not active:
        return
    max_input = settings.word_tree_summary_max_input_chars_per_node
    model_nodes = [
        {
            "node_id": target["node_id"],
            "content": target["material"][:max_input],
        }
        for target in active
    ]
    parsed = None
    if client is not None:
        parsed = chat_with_schema(
            client,
            system=_SYSTEM_PROMPT,
            user=json.dumps({"nodes": model_nodes}, ensure_ascii=False),
            schema=_SUMMARY_SCHEMA,
            schema_name="word_tree_summaries",
            max_tokens=max(512, len(active) * 256),
            enable_thinking=False,
            timeout_s=settings.word_tree_summary_timeout_s,
        )

    valid_ids = {target["node_id"] for target in active}
    accepted: dict[str, str] = {}
    raw_summaries = parsed.get("summaries", []) if isinstance(parsed, dict) else []
    if isinstance(raw_summaries, list):
        for item in raw_summaries:
            if not isinstance(item, dict):
                continue
            node_id = item.get("node_id")
            summary = item.get("content_summary")
            if (
                node_id not in valid_ids
                or node_id in accepted
                or not isinstance(summary, str)
            ):
                continue
            cleaned = " ".join(summary.split())
            if cleaned:
                accepted[node_id] = cleaned[
                    : settings.word_tree_summary_max_output_chars
                ]

    generated_at = datetime.now(UTC).isoformat()
    for target in active:
        metadata = target["metadata"]
        summary = accepted.get(target["node_id"])
        if summary:
            metadata.content_summary = summary
            metadata.summary_status = (
                "partial" if target.get("has_degraded_child") else "completed"
            )
            metadata.summary_source = "llm"
            metadata.summary_model = settings.local_llm_model
            metadata.generated_at = generated_at
        else:
            metadata.content_summary = _extractive_summary(
                target["material"], settings.word_tree_summary_max_output_chars
            )
            metadata.summary_status = "failed"
            metadata.summary_source = "extractive_fallback"
            metadata.summary_model = None
            metadata.generated_at = generated_at


def fallback_word_tree_summaries(structure: DocStructure) -> ChapterNode | None:
    """Apply deterministic summaries after an unexpected orchestration failure."""
    root = structure.section_tree
    if root is None:
        return None

    all_nodes = _chapters(root)
    prompt_version = settings.word_tree_summary_prompt_version
    generated_at = datetime.now(UTC).isoformat()
    for node in all_nodes:
        node.layer_metadata.prompt_version = prompt_version
        for page in node.pages:
            page.page_metadata.prompt_version = prompt_version
            _mark_extractive_fallback(
                page.page_metadata,
                _page_material(structure, page),
                generated_at,
            )

    depths = _depths(root)
    section_nodes = [node for node in all_nodes if node.node_type == "section"]
    for depth in sorted({depths[node.node_id] for node in section_nodes}, reverse=True):
        for node in section_nodes:
            if depths[node.node_id] == depth:
                _mark_extractive_fallback(
                    node.layer_metadata,
                    _chapter_material(structure, node),
                    generated_at,
                )
    _mark_extractive_fallback(
        root.layer_metadata,
        _chapter_material(structure, root),
        generated_at,
    )
    return root


def summarize_word_tree(
    structure: DocStructure,
    client,
    *,
    progress_fn: Callable[[str], None] | None = None,
    should_stop_fn: Callable[[], bool] | None = None,
) -> ChapterNode | None:
    """Attach display-only page/chapter summaries without changing structure."""
    root = structure.section_tree
    if root is None:
        return None

    all_nodes = _chapters(root)
    all_pages = [page for node in all_nodes for page in node.pages]
    prompt_version = settings.word_tree_summary_prompt_version
    for node in all_nodes:
        node.layer_metadata.prompt_version = prompt_version
    for page in all_pages:
        page.page_metadata.prompt_version = prompt_version

    if not (
        settings.llm_word_tree_summary_enabled and settings.local_llm_enabled
    ):
        for page in all_pages:
            _mark_disabled(page.page_metadata)
        for node in all_nodes:
            _mark_disabled(node.layer_metadata)
        return root

    if progress_fn:
        progress_fn("summarizing")

    page_targets = [
        {
            "node_id": page.node_id,
            "material": _page_material(structure, page),
            "metadata": page.page_metadata,
        }
        for page in all_pages
    ]
    for batch in _batches(page_targets):
        if should_stop_fn and should_stop_fn():
            return root
        _apply_batch(client, batch)

    depths = _depths(root)
    section_nodes = [node for node in all_nodes if node.node_type == "section"]
    for depth in sorted({depths[node.node_id] for node in section_nodes}, reverse=True):
        targets = []
        for node in section_nodes:
            if depths[node.node_id] != depth:
                continue
            degraded = any(
                child.layer_metadata.summary_source == "extractive_fallback"
                or child.layer_metadata.summary_status in {"failed", "partial"}
                for child in node.children
            ) or any(
                page.page_metadata.summary_source == "extractive_fallback"
                or page.page_metadata.summary_status == "failed"
                for page in node.pages
            )
            targets.append({
                "node_id": node.node_id,
                "material": _chapter_material(structure, node),
                "metadata": node.layer_metadata,
                "has_degraded_child": degraded,
            })
        for batch in _batches(targets):
            if should_stop_fn and should_stop_fn():
                return root
            _apply_batch(client, batch)

    root_degraded = any(
        child.layer_metadata.summary_source == "extractive_fallback"
        or child.layer_metadata.summary_status in {"failed", "partial"}
        for child in root.children
    )
    if should_stop_fn and should_stop_fn():
        return root
    _apply_batch(client, [{
        "node_id": root.node_id,
        "material": _chapter_material(structure, root),
        "metadata": root.layer_metadata,
        "has_degraded_child": root_degraded,
    }])
    logger.info(
        "Word tree summaries complete: nodes=%d pages=%d model=%s",
        len(all_nodes), len(all_pages), settings.local_llm_model,
    )
    return root
