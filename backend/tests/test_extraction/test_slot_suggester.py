"""Tests for 013 slot_suggester — two-round LLM flow, IRI binding, dedup, cap."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.extraction.slot_suggester import (
    suggest_slots,
    _bind_ontology_iris,
    tiptap_to_text,
    derive_source_ref,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_client(round1_response: dict, round2_response: dict):
    """Return a mock OpenAI client that returns canned responses for two calls."""
    client = MagicMock()
    responses = [round1_response, round2_response]
    call_count = {"n": 0}

    def _create(**kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps(responses[idx], ensure_ascii=False)
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


_R1_OK = {
    "document_summary": "GMP 风险评估报告",
    "sections": [
        {
            "title": "评估对象",
            "groups": [
                {
                    "title": "药品信息",
                    "candidates": [
                        {"label": "药品名称", "evidence_span": "XX注射液", "evidence_offset": 10},
                    ],
                },
            ],
        },
    ],
}

_R2_OK = {
    "slots": [
        {
            "slot_id": "drug_name",
            "label": "药品名称",
            "section": "评估对象",
            "group": "药品信息",
            "confidence": 0.92,
            "evidence_span": "XX注射液",
            "evidence_offset": 10,
            "reason": "文档首段",
        },
        {
            "slot_id": "drug_form",
            "label": "剂型",
            "section": "评估对象",
            "group": "药品信息",
            "confidence": 0.85,
            "evidence_span": "注射剂",
            "evidence_offset": 25,
            "reason": "首段后续",
        },
    ],
    "skipped_duplicates": 1,
}


# ── Tests ────────────────────────────────────────────────────────────────

class TestSuggestSlots:
    def test_two_round_flow_produces_grouped_suggestions(self):
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(client, "some doc text", max_suggestions=50)

        assert result["total_suggested"] == 2
        assert result["skipped_duplicates"] == 1
        assert result["document_summary"] == "GMP 风险评估报告"
        assert len(result["sections"]) >= 1
        sec = result["sections"][0]
        assert sec["title"] == "评估对象"
        assert len(sec["groups"]) >= 1
        assert len(sec["groups"][0]["slots"]) >= 1

    def test_empty_document_returns_empty(self):
        client = MagicMock()
        result = suggest_slots(client, "   ", max_suggestions=50)
        assert result["total_suggested"] == 0
        assert result["sections"] == []

    def test_max_suggestions_caps_and_truncates(self):
        many_slots = [
            {
                "slot_id": f"slot_{i}",
                "label": f"Label {i}",
                "section": "S",
                "group": "G",
                "confidence": 0.8,
                "evidence_span": "ev",
                "reason": "r",
            }
            for i in range(10)
        ]
        r2 = {"slots": many_slots, "skipped_duplicates": 0}
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(client, "doc text", max_suggestions=3)
        assert result["total_suggested"] == 3
        assert result["truncated"] is True

    def test_skipped_duplicates_counted(self):
        r2 = {"slots": [], "skipped_duplicates": 5}
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(
            client, "doc text",
            existing_template={"sections": [{"title": "S", "groups": []}]},
        )
        assert result["skipped_duplicates"] == 5

    def test_round1_failure_returns_empty(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        result = suggest_slots(client, "doc text")
        assert result["total_suggested"] == 0
        assert result["sections"] == []


class TestOntologyIRIBinding:
    def test_matching_label_sets_extraction(self):
        engine = MagicMock()
        engine.data_property_labels.return_value = ["药品名称", "剂型"]
        engine.data_property_domain_classes.return_value = [
            ("http://slpra/ontology#DrugProduct.name", "药品名称"),
        ]
        slots = [
            {"label": "药品名称", "slot_id": "drug_name"},
        ]
        _bind_ontology_iris(slots, engine)
        assert slots[0]["source_kind"] == "extraction"
        assert slots[0]["source_hint"] == "http://slpra/ontology#DrugProduct.name"

    def test_no_match_sets_llm_extraction(self):
        engine = MagicMock()
        engine.data_property_labels.return_value = ["药品名称"]
        engine.data_property_domain_classes.return_value = []
        slots = [{"label": "未知字段", "slot_id": "unknown"}]
        _bind_ontology_iris(slots, engine)
        assert slots[0]["source_kind"] == "llm_extraction"
        assert slots[0]["source_hint"] is None

    def test_engine_failure_defaults_to_llm_extraction(self):
        engine = MagicMock()
        engine.data_property_labels.side_effect = Exception("engine error")
        slots = [{"label": "x", "slot_id": "x"}]
        _bind_ontology_iris(slots, engine)
        assert slots[0]["source_kind"] == "llm_extraction"


# ── 013: tiptap → LLM text (server-side, structure-faithful) ───────────────

class TestTiptapToText:
    def test_empty_returns_empty_string(self):
        assert tiptap_to_text(None) == ""
        assert tiptap_to_text({}) == ""

    def test_heading_prefixed_with_hash(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "评估对象"}],
                },
            ],
        }
        assert tiptap_to_text(doc) == "## 评估对象"

    def test_heading_level_clamped_to_six(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 9},
                    "content": [{"type": "text", "text": "深层标题"}],
                },
            ],
        }
        assert tiptap_to_text(doc) == "###### 深层标题"

    def test_paragraph_and_list_item(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "普通段落"}]},
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "列表项"}],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        # listItem 走 _node_text 递归取全部文本，不再重复 emit 内部段落。
        assert tiptap_to_text(doc) == "普通段落\n- 列表项"

    def test_table_rows_joined_with_pipe(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "设备编号"}],
                                        }
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "CT64201"}],
                                        }
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        assert tiptap_to_text(doc) == "[表格]\n设备编号 | CT64201"

    def test_round_tripped_block_text_is_substring(self):
        # 忠于原文：LLM 引用的 evidence_span 必是序列化文本的子串（联动前提）。
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "评估对象"}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "本品为XX注射液。"}],
                },
            ],
        }
        text = tiptap_to_text(doc)
        assert "评估对象" in text
        assert "本品为XX注射液。" in text

    def test_truncation_marker_when_over_limit(self):
        from app.services.extraction.slot_suggester import _MAX_DOC_CHARS

        long = "字" * (_MAX_DOC_CHARS + 1000)
        doc = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": long}]}],
        }
        out = tiptap_to_text(doc)
        assert out.endswith("…（文档已截断）")
        assert len(out) <= _MAX_DOC_CHARS + len("\n…（文档已截断）")


# ── 013: deterministic source_ref anchor derivation (never LLM-emitted) ────

_ANCHOR_DOC = {
    "type": "doc",
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 1},
            "content": [{"type": "text", "text": "评估对象"}],
        },
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "本品为XX注射液，剂型为注射剂。"}],
        },
    ],
}


class TestDeriveSourceRef:
    def test_none_when_empty_span(self):
        assert derive_source_ref("", _ANCHOR_DOC) is None
        assert derive_source_ref(None, _ANCHOR_DOC) is None

    def test_none_when_empty_content(self):
        assert derive_source_ref("XX注射液", None) is None
        assert derive_source_ref("XX注射液", {}) is None

    def test_heading_hit_returns_section_anchor(self):
        assert derive_source_ref("评估对象", _ANCHOR_DOC) == "§ 评估对象"

    def test_paragraph_under_heading_returns_heading_anchor(self):
        # 段落命中 → 锚定其最近标题，前端 WordViewer 高亮整节。
        assert derive_source_ref("XX注射液", _ANCHOR_DOC) == "§ 评估对象"

    def test_headingless_top_block_returns_raw_text(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "独立段落内容片段"}],
                },
            ],
        }
        assert derive_source_ref("段落内容", doc) == "独立段落内容片段"

    def test_no_match_returns_none(self):
        assert derive_source_ref("完全不存在的文本", _ANCHOR_DOC) is None

    def test_reverse_containment_matches_longer_span(self):
        # evidence_span 比单元格文本更长（如跨列拼接），走 Pass-2 反向包含。
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [
                                                {"type": "text", "text": "高致敏药品专用设备"}
                                            ],
                                        }
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        span = "本设备属于高致敏药品专用设备范畴"
        assert derive_source_ref(span, doc) == "高致敏药品专用设备"


class TestContentJsonSourceRef:
    """suggest_slots + content_json → each slot gets a structural source_ref."""

    def _slots(self, result):
        return [
            slot
            for sec in result["sections"]
            for grp in sec["groups"]
            for slot in grp["slots"]
        ]

    def test_binds_source_ref_from_content_json(self):
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(
            client,
            "本品为XX注射液，剂型为注射剂。",
            content_json=_ANCHOR_DOC,
            max_suggestions=50,
        )
        slots = self._slots(result)
        assert slots, "expected suggested slots"
        # _R2_OK 的两个 evidence_span（XX注射液 / 注射剂）都落在「评估对象」章节内。
        assert all(s["source_ref"] == "§ 评估对象" for s in slots)

    def test_no_source_ref_key_without_content_json(self):
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(client, "some doc text", max_suggestions=50)
        slots = self._slots(result)
        assert slots
        assert all("source_ref" not in s for s in slots)
