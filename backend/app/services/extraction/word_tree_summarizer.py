"""Offline-safe, bottom-up summaries for the deterministic Word chapter tree."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from datetime import UTC, datetime
from queue import Empty, SimpleQueue
from threading import Event
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
from app.services.llm.model_runtime import check_cancelled, model_scope, runtime

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
保留药品、设备、工艺、风险名称和编号，以及关键数值、否定与适用条件。父章节概括主题，不逐项机械重复。
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
    covered = {
        block_id for page in node.pages if _usable_page_summary(page)
        for block_id in page.block_ids
    } if not node.children else set()
    direct = _material_for_blocks(
        structure, (bid for bid in node.direct_block_ids if bid not in covered),
        include_headings=False,
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


def _usable_page_summary(page: PageNode) -> bool:
    metadata = page.page_metadata
    return bool(metadata.content_summary and metadata.summary_status == "completed"
                and metadata.summary_source == "llm")


def _reuse_single_page(node: ChapterNode) -> bool:
    if node.children or len(node.pages) != 1 or node.layer_metadata.summary_source == "empty":
        return False
    page = node.pages[0]
    if not _usable_page_summary(page) or set(page.block_ids) != set(node.direct_block_ids):
        return False
    # The source scope is identical. Retain the node's own scope, content hash,
    # deterministic counts and structural identity; reuse only generation fields.
    for name in ("content_summary", "summary_status", "summary_source", "summary_model",
                 "prompt_version", "generated_at"):
        setattr(node.layer_metadata, name, getattr(page.page_metadata, name))
    return True


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
        maximum = settings.word_tree_summary_max_output_chars
        output_tokens = max(512, len(active) * (maximum + 64))
        parsed = chat_with_schema(
            client,
            system=_SYSTEM_PROMPT + f"\n每个 content_summary 不超过 {maximum} 个字符。"
            "为每个输入节点返回一条摘要，不重复节点，不添加解释性文字。",
            user=json.dumps({"nodes": model_nodes}, ensure_ascii=False),
            schema=_SUMMARY_SCHEMA,
            schema_name="word_tree_summaries",
            max_tokens=output_tokens,
            truncation_max_tokens=output_tokens * 2,
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
                accepted[node_id] = cleaned

    generated_at = datetime.now(UTC).isoformat()
    for target in active:
        metadata = target["metadata"]
        summary = accepted.get(target["node_id"])
        if summary:
            metadata.content_summary = summary[:settings.word_tree_summary_max_output_chars]
            metadata.summary_status = (
                "partial" if (
                    target.get("has_degraded_child")
                    or len(target["material"]) > max_input
                    or len(summary) > settings.word_tree_summary_max_output_chars
                ) else "completed"
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


def _apply_batches(client, targets, should_stop_fn) -> bool:
    """Run one dependency layer; keep owner callbacks off the model threads."""
    batches = _batches(targets)
    if not batches:
        return True
    limit = max(1, min(settings.word_tree_summary_max_concurrency,
                       settings.local_llm_max_concurrency, len(batches)))
    stopped = Event()
    progress = SimpleQueue()
    owner_progress = runtime.get().get("progress")
    owner_wait = runtime.get().get("on_model_wait")

    def flush_progress():
        while owner_progress is not None:
            try:
                event = progress.get_nowait()
            except Empty:
                break
            owner_progress(event)

    def apply(batch):
        with model_scope(stage="word_tree_summary", should_stop=stopped.is_set,
                         on_model_wait=None, progress=progress.put if owner_progress else None):
            check_cancelled()
            _apply_batch(client, batch)

    pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="word-summary")
    pending = {}
    cursor = 0
    try:
        while pending or cursor < len(batches):
            check_cancelled()
            if should_stop_fn and should_stop_fn():
                return False
            flush_progress()
            if owner_wait is not None:
                owner_wait()
            while len(pending) < limit and cursor < len(batches):
                future = pool.submit(copy_context().run, apply, batches[cursor])
                pending[future] = cursor
                cursor += 1
            done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=pending.get):
                del pending[future]
                future.result()
        flush_progress()
        return True
    finally:
        stopped.set()
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)


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
    if not _apply_batches(client, page_targets, should_stop_fn):
        return root

    depths = _depths(root)
    section_nodes = [node for node in all_nodes if node.node_type == "section"]
    for depth in sorted({depths[node.node_id] for node in section_nodes}, reverse=True):
        targets = []
        for node in section_nodes:
            if depths[node.node_id] != depth:
                continue
            if _reuse_single_page(node):
                continue
            degraded = any(
                child.layer_metadata.summary_source == "extractive_fallback"
                or child.layer_metadata.summary_status in {"failed", "partial"}
                for child in node.children
            ) or any(
                page.page_metadata.summary_source == "extractive_fallback"
                or page.page_metadata.summary_status in {"failed", "partial"}
                for page in node.pages
            )
            targets.append({
                "node_id": node.node_id,
                "material": _chapter_material(structure, node),
                "metadata": node.layer_metadata,
                "has_degraded_child": degraded,
            })
        if not _apply_batches(client, targets, should_stop_fn):
            return root

    root_degraded = any(
        child.layer_metadata.summary_source == "extractive_fallback"
        or child.layer_metadata.summary_status in {"failed", "partial"}
        for child in root.children
    ) or any(page.page_metadata.summary_status in {"failed", "partial"} for page in root.pages)
    if should_stop_fn and should_stop_fn():
        return root
    if not _reuse_single_page(root):
        if not _apply_batches(client, [{
            "node_id": root.node_id,
            "material": _chapter_material(structure, root),
            "metadata": root.layer_metadata,
            "has_degraded_child": root_degraded,
        }], should_stop_fn):
            return root
    logger.info(
        "Word tree summaries complete: nodes=%d pages=%d model=%s",
        len(all_nodes), len(all_pages), settings.local_llm_model,
    )
    return root
