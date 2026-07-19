"""Tests for 013/016 slot_suggester.

016 收敛：``suggest_slots`` 只产出 ``{document_summary, coverage}``。逐插槽建议流
（slots / total_suggested / skipped_duplicates / truncated / 逐插槽 source_ref 绑定 /
``derive_source_ref``）已随 016 移除；原文取数候选流（``unresolved_candidates``，取代
FR-008a）亦一并移除——无法绑定到本体菜单的位点静默忽略。保留：两轮 LLM 流、本体覆盖
选择（selection-not-invention）、tiptap→文本 序列化。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.extraction.slot_suggester import (
    suggest_slots,
    tiptap_to_text,
)
from tests.fixtures.ontology import (
    BIOLOGIC,
    DRUG_NS,
    DRUG_PRODUCT,
    MANUFACTURED_BY,
    MANUFACTURER,
    build_drug_ontology,
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

# 016 round-2：LLM 从本体菜单**选择**覆盖边——不再有 slots / skipped_duplicates /
# unresolved_candidates 字段。
_R2_OK = {
    "coverage": [
        {"predicate_iri": MANUFACTURED_BY, "range_class_iri": MANUFACTURER, "label": "生产者"},
    ],
}


# ── Tests ────────────────────────────────────────────────────────────────

class TestBasic:
    """016 (+Option A)：AI 分析产出 document_summary + coverage + sections（Round-1 结构骨架
    逐字回传，无本体 IRI 绑定）。逐插槽取数流仍不回归。"""

    def test_two_round_flow_returns_pure_coverage_shape(self):
        engine = build_drug_ontology()
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(
            client, "some doc text",
            ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        assert set(result.keys()) == {"document_summary", "coverage", "sections"}
        assert result["document_summary"] == "GMP 风险评估报告"
        assert len(result["coverage"]) == 1
        assert result["coverage"][0]["predicate_iri"] == MANUFACTURED_BY

    def test_no_legacy_slot_stream_keys(self):
        """016 收敛：逐插槽取数流字段仍彻底移除（Option A 只恢复结构骨架 sections）。"""
        engine = build_drug_ontology()
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(
            client, "some doc text",
            ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        for gone in ("slots", "total_suggested", "skipped_duplicates", "truncated"):
            assert gone not in result
        # Option A：结构骨架回归，且不是旧取数流的复活。
        assert "sections" in result

    def test_skeleton_returned_verbatim_from_round1(self):
        """S11：sections 是 Round-1 骨架的逐字回传，零本体 IRI 绑定、零 manual 塌陷。"""
        engine = build_drug_ontology()
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(
            client, "some doc text",
            ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        assert result["sections"] == _R1_OK["sections"]
        # 骨架惰性：candidate 只有 label/evidence，绝无 IRI 绑定或 source_kind 标注。
        cand = result["sections"][0]["groups"][0]["candidates"][0]
        assert "predicate_iri" not in cand
        assert "source_kind" not in cand

    def test_skeleton_empty_when_round1_fails(self):
        """S11：Round-1 失败 → sections 为 []（骨架仅在 Round-1 成功后存在）。"""
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        result = suggest_slots(client, "doc text")
        assert result["sections"] == []

    def test_empty_document_returns_empty_skeleton(self):
        """S11：空文档 → sections 为 []（连 Round-1 都不发起）。"""
        client = MagicMock()
        result = suggest_slots(client, "   ")
        assert result["sections"] == []

    def test_empty_document_returns_empty(self):
        client = MagicMock()
        result = suggest_slots(client, "   ")
        assert result["coverage"] == []
        assert result["document_summary"]  # non-empty explanation

    def test_round1_failure_returns_empty(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("LLM down")
        result = suggest_slots(client, "doc text")
        assert result["coverage"] == []
        assert result["document_summary"]


class TestOntologyCoverage:
    """016 US1: ontology-grounded section coverage (selection-not-invention).

    The suggester has the LLM **select** relationship edges from
    ``get_relation_schema(doc_class_iri)`` and emit ``CoverageDeclaration``s.
    Declarations reference **types** only (S2). Positions that bind to no menu edge
    are silently ignored — no ``unresolved_candidates`` stream (supersedes FR-008a).
    Per contracts/suggest-slots-api.md.
    """

    def test_graph_sourced_section_emits_coverage(self):
        """S1: graph-sourced content becomes a coverage declaration."""
        engine = build_drug_ontology()
        r2 = {
            "coverage": [
                {"predicate_iri": MANUFACTURED_BY, "range_class_iri": MANUFACTURER,
                 "label": "生产者"},
            ],
        }
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(
            client, "some doc", ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        cov = result["coverage"]
        assert len(cov) == 1
        assert cov[0]["kind"] == "ontology_relation"
        assert cov[0]["doc_class_iri"] == DRUG_PRODUCT
        assert cov[0]["predicate_iri"] == MANUFACTURED_BY
        assert cov[0]["range_class_iri"] == MANUFACTURER
        assert cov[0]["required"] is True

    def test_declarations_reference_types_never_individuals(self):
        """S2 / SC-002: a declaration referencing a sample individual is dropped."""
        engine = build_drug_ontology()
        r2 = {
            "coverage": [
                {"predicate_iri": MANUFACTURED_BY, "range_class_iri": MANUFACTURER,
                 "label": "生产者"},
                # invented: range is a concrete individual, not a schema edge type
                {"predicate_iri": MANUFACTURED_BY,
                 "range_class_iri": DRUG_NS + "individual_acme_pharma_001",
                 "label": "某具体厂商"},
            ],
        }
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(
            client, "some doc", ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        cov = result["coverage"]
        # only the type-referencing declaration survives the schema-membership filter
        assert len(cov) == 1
        assert cov[0]["range_class_iri"] == MANUFACTURER
        valid = {
            (e["predicate_iri"], e["range_class_iri"])
            for e in engine.get_relation_schema(DRUG_PRODUCT)
        }
        assert all((d["predicate_iri"], d["range_class_iri"]) in valid for d in cov)

    def test_unbindable_position_is_silently_ignored(self):
        """S4 (016 收敛)：绑定不到菜单边的位点被静默忽略——不再产出取数候选。

        LLM 违规多输出的 ``unresolved_candidates`` 亦被丢弃（不进入返回契约）。
        """
        engine = build_drug_ontology()
        r2 = {
            "coverage": [],
            # LLM 违规多吐的字段——提取层不消费，不应泄漏进返回。
            "unresolved_candidates": [
                {"proposed_label": "设备编号 646", "evidence": "设备编号：646"},
            ],
        }
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(
            client, "some doc", ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        assert set(result.keys()) == {"document_summary", "coverage", "sections"}
        assert result["coverage"] == []
        assert "unresolved_candidates" not in result

    def test_declaration_required_true_by_default(self):
        """S5 / FR-005a: a declaration without an explicit flag defaults to required."""
        engine = build_drug_ontology()
        r2 = {
            "coverage": [
                {"predicate_iri": MANUFACTURED_BY, "range_class_iri": MANUFACTURER},
            ],
        }
        client = _make_client(_R1_OK, r2)
        result = suggest_slots(
            client, "some doc", ontology_engine=engine, doc_class_iri=DRUG_PRODUCT,
        )
        assert result["coverage"][0]["required"] is True

    def test_doc_class_iri_optional_request_still_valid(self):
        """S6 / D10: doc_class_iri is optional and OUTSIDE the exactly-one-of count."""
        from app.schemas.extraction import SuggestSlotsRequest

        req = SuggestSlotsRequest(document_text="doc")
        assert req.doc_class_iri is None
        req2 = SuggestSlotsRequest(document_text="doc", doc_class_iri=DRUG_PRODUCT)
        assert req2.doc_class_iri == DRUG_PRODUCT

        # FR-012: no doc_class_iri → the suggester still returns a summary, empty
        # coverage, and raises nothing (graceful degradation). Round-1 skeleton is
        # grounding-independent, so `sections` is present even without a doc_class_iri (S11).
        engine = build_drug_ontology()
        client = _make_client(_R1_OK, _R2_OK)
        result = suggest_slots(client, "doc", ontology_engine=engine)
        assert result["coverage"] == []
        assert result["sections"] == _R1_OK["sections"]
        assert isinstance(result["document_summary"], str)


class TestSupplementalCmcEdges:
    """016 schema-driven: broad-domain object props now have ``rdfs:domain`` declared
    in TTL, so ``get_relation_schema`` BFS discovers them directly. The AI coverage
    menu and extraction pipeline share the same ``get_relation_schema`` single source
    of truth.
    """

    def test_cmc_schema_edges_in_menu(self):
        from app.services.extraction.relation_extractor import (
            CMC_REPORT_IRI,
            DEGRADATION_PATHWAY_IRI,
            EQUIPMENT_IRI,
            HAS_DEGRADATION_PATHWAY_IRI,
            HAS_STORAGE_CONDITION_IRI,
            STORAGE_CONDITION_IRI,
            USES_EQUIPMENT_IRI,
        )
        from app.services.extraction.slot_suggester import (
            _build_ontology_context,
            _extract_coverage,
        )

        # CMCReport 现有 domain 声明 → get_relation_schema BFS 自然覆盖 3 条边。
        engine = build_drug_ontology()
        schema_edges, prompt = _build_ontology_context(engine, CMC_REPORT_IRI)

        edge_keys = {(e["predicate_iri"], e["range_class_iri"]) for e in schema_edges}
        assert (USES_EQUIPMENT_IRI, EQUIPMENT_IRI) in edge_keys
        assert (HAS_STORAGE_CONDITION_IRI, STORAGE_CONDITION_IRI) in edge_keys
        assert (HAS_DEGRADATION_PATHWAY_IRI, DEGRADATION_PATHWAY_IRI) in edge_keys

        for label in ("使用设备", "存放条件", "含降解途径"):
            assert label in prompt

        r2 = {"coverage": [
            {"predicate_iri": USES_EQUIPMENT_IRI, "range_class_iri": EQUIPMENT_IRI},
        ]}
        cov = _extract_coverage(r2, schema_edges, CMC_REPORT_IRI)
        assert len(cov) == 1
        assert cov[0]["predicate_iri"] == USES_EQUIPMENT_IRI
        assert cov[0]["doc_class_iri"] == CMC_REPORT_IRI
        assert cov[0]["required"] is True

    def test_non_cmc_doc_class_gets_own_schema_edges(self):
        """Other doc types get their own schema edges (no cross-type leakage)."""
        from app.services.extraction.slot_suggester import _supplemented_schema_edges

        engine = build_drug_ontology()
        dp_edges = _supplemented_schema_edges(engine, DRUG_PRODUCT)
        assert len(dp_edges) >= 1
        assert all(e["domain_class_iri"] == DRUG_PRODUCT for e in dp_edges if e["hop"] == 1)


class TestCoverageCapable:
    """Bugfix「仅启用已建模类型」(S9/S10): a document type is *capable* iff the ontology
    models ≥1 hop-1 coverage edge for it. ``coverage_capable`` (behind the
    ``/coverage-doc-classes`` probe that gates the authoring dropdown) and
    ``_build_ontology_context`` (the AI menu) MUST agree — both consume the one
    ``_supplemented_schema_edges`` source of truth, so "UI enables it" ⇔ "AI can emit
    coverage". Per contracts/suggest-slots-api.md.
    """

    def test_modeled_type_is_capable(self):
        """DrugProduct has a hop-1 object property (manufacturedBy) → capable."""
        from app.services.extraction.slot_suggester import coverage_capable

        engine = build_drug_ontology()
        assert coverage_capable(engine, DRUG_PRODUCT) is True

    def test_cmc_capable_via_domain_declarations(self):
        """CMCReport has 3 object properties with domain declarations → BFS finds
        them at hop-1, so CMCReport is capable."""
        from app.services.extraction.relation_extractor import CMC_REPORT_IRI
        from app.services.extraction.slot_suggester import coverage_capable

        engine = build_drug_ontology()
        assert coverage_capable(engine, CMC_REPORT_IRI) is True

    def test_unmodeled_type_not_capable(self):
        """Leaf/document-classification types with no object property → NOT capable
        (Manufacturer is a leaf; BiologicalDrugProduct is a subclass with no own
        object props — domain-literal matching, mirroring real doc-class subclasses)."""
        from app.services.extraction.slot_suggester import coverage_capable

        engine = build_drug_ontology()
        assert coverage_capable(engine, MANUFACTURER) is False
        assert coverage_capable(engine, BIOLOGIC) is False

    def test_missing_engine_or_iri_not_capable(self):
        """FR-012 graceful degradation: no engine / no iri → not capable (no raise)."""
        from app.services.extraction.slot_suggester import coverage_capable

        engine = build_drug_ontology()
        assert coverage_capable(None, DRUG_PRODUCT) is False
        assert coverage_capable(engine, None) is False

    def test_build_ontology_context_contract_unchanged(self):
        """S9 regression: ``(edges, prompt)`` contract intact — modeled type yields a
        non-empty menu prompt; no-hop1 / no-engine degrades to ``([], "")``."""
        from app.services.extraction.slot_suggester import _build_ontology_context

        engine = build_drug_ontology()
        edges, prompt = _build_ontology_context(engine, DRUG_PRODUCT)
        assert edges and prompt
        assert "生产者" in prompt  # hop-1 predicate label rendered into the menu

        # no hop-1 edge → prompt empty (dropdown/AI both see "unmodeled")
        assert _build_ontology_context(engine, MANUFACTURER) == ([], "")
        # engine/iri absent → fully degraded
        assert _build_ontology_context(None, DRUG_PRODUCT) == ([], "")
        assert _build_ontology_context(engine, None) == ([], "")

    def test_capable_iff_menu_nonempty(self):
        """S9 parity: capability ⇔ a non-empty AI menu, for every fixture type."""
        from app.services.extraction.slot_suggester import (
            _build_ontology_context,
            coverage_capable,
        )
        from app.services.extraction.relation_extractor import CMC_REPORT_IRI

        engine = build_drug_ontology()
        for iri in (DRUG_PRODUCT, CMC_REPORT_IRI, MANUFACTURER, BIOLOGIC):
            _, prompt = _build_ontology_context(engine, iri)
            assert coverage_capable(engine, iri) is bool(prompt), iri


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
