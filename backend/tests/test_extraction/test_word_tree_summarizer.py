"""018 — batched bottom-up Word tree summary contracts."""

from __future__ import annotations

import json

from docx import Document

from app.config import settings
from app.services.extraction import word_tree_summarizer
from app.services.extraction.docx_structure import parse_docx_structure


def _structure(tmp_path):
    doc = Document()
    doc.add_heading("父章节", level=1)
    doc.add_paragraph("父章节直接内容。")
    doc.add_heading("子章节", level=2)
    doc.add_paragraph("子章节正文。")
    doc.add_heading("另一个子章节", level=2)
    doc.add_paragraph("另一个子章节正文。")
    path = tmp_path / "summary.docx"
    doc.save(path)
    return parse_docx_structure(path)


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_enabled", True)
    monkeypatch.setattr(settings, "llm_word_tree_summary_enabled", True)
    monkeypatch.setattr(settings, "word_tree_summary_max_nodes_per_batch", 20)
    monkeypatch.setattr(settings, "word_tree_summary_max_batch_chars", 24000)


def test_summary_order_is_pages_then_deepest_sections_then_root(tmp_path, monkeypatch):
    structure = _structure(tmp_path)
    _enable(monkeypatch)
    calls: list[list[str]] = []
    prompts: dict[str, str] = {}

    def fake_chat(_client, **kwargs):
        payload = json.loads(kwargs["user"])
        ids = [node["node_id"] for node in payload["nodes"]]
        calls.append(ids)
        for node in payload["nodes"]:
            prompts[node["node_id"]] = node["content"]
        return {
            "summaries": [
                {"node_id": node_id, "content_summary": f"摘要-{node_id}"}
                for node_id in ids
            ]
        }

    monkeypatch.setattr(word_tree_summarizer, "chat_with_schema", fake_chat)
    root = word_tree_summarizer.summarize_word_tree(structure, object())

    assert root is not None
    flat_calls = [node_id for batch in calls for node_id in batch]
    page_index = next(i for i, value in enumerate(flat_calls) if ":page:" in value)
    child = root.children[0].children[0]
    parent = root.children[0]
    assert page_index < flat_calls.index(child.node_id)
    assert flat_calls.index(child.node_id) < flat_calls.index(parent.node_id)
    assert flat_calls.index(parent.node_id) < flat_calls.index("document")
    assert f"摘要-{child.node_id}" in prompts[parent.node_id]
    assert parent.layer_metadata.summary_status == "completed"


def test_unknown_and_missing_ids_fallback_per_node(tmp_path, monkeypatch):
    structure = _structure(tmp_path)
    _enable(monkeypatch)

    def fake_chat(_client, **kwargs):
        payload = json.loads(kwargs["user"])
        first = payload["nodes"][0]["node_id"]
        return {
            "summaries": [
                {"node_id": "unknown", "content_summary": "不得写入"},
                {"node_id": first, "content_summary": "有效摘要"},
                {"node_id": first, "content_summary": "重复摘要"},
            ]
        }

    monkeypatch.setattr(word_tree_summarizer, "chat_with_schema", fake_chat)
    root = word_tree_summarizer.summarize_word_tree(structure, object())
    assert root is not None
    nodes = [root, *root.children, *root.children[0].children]
    summaries = [node.layer_metadata for node in nodes]
    assert all(metadata.content_summary != "不得写入" for metadata in summaries)
    assert any(
        metadata.summary_source == "extractive_fallback" for metadata in summaries
    )


def test_disabled_summary_keeps_complete_tree_without_call(tmp_path, monkeypatch):
    structure = _structure(tmp_path)
    monkeypatch.setattr(settings, "local_llm_enabled", False)
    monkeypatch.setattr(settings, "llm_word_tree_summary_enabled", True)

    def boom(*_args, **_kwargs):
        raise AssertionError("disabled summary must not call the model")

    monkeypatch.setattr(word_tree_summarizer, "chat_with_schema", boom)
    root = word_tree_summarizer.summarize_word_tree(structure, None)
    assert root is structure.section_tree
    assert root.layer_metadata.summary_status == "disabled"
    assert root.children[0].children[0].pages
    assert root.children[0].children[0].pages[0].page_metadata.summary_status == "disabled"


def test_enabled_but_unavailable_client_uses_deterministic_fallback(tmp_path, monkeypatch):
    structure = _structure(tmp_path)
    _enable(monkeypatch)
    root = word_tree_summarizer.summarize_word_tree(structure, None)
    assert root is not None
    assert root.layer_metadata.summary_source == "extractive_fallback"
    assert root.layer_metadata.content_summary
