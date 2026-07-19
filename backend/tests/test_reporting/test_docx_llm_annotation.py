"""Tests for 013 DOCX LLM annotation rendering (T019).

Verifies:
  - Supplemented values render gray-italic + info glyph
  - End-of-report disclaimer section present when LLM content exists
  - No LLM styling when supplements empty
  - 100% of LLM content visually annotated (SC-004)
"""

from __future__ import annotations

import io

from docx import Document

from app.services.reporting.docx_renderer import (
    _LLM_END_DISCLAIMER_ZH,
    _LLM_INFO_GLYPH,
    _add_generated_disclaimer_section,
    _add_llm_disclaimer_line,
    _add_llm_run,
    _add_semantic_slots,
    render_risk_report,
)
from app.services.reporting.risk_report_generator import RiskReport


class TestLLMRunStyling:
    def test_add_llm_run_black_upright_inherits_style(self):
        """LLM 正文运行须为「黑色、非斜体」正文（作者要求，溯源靠 ⓘ 标记+免责声明），
        且不硬编码字号/字体——继承文档 Normal 样式，才能在套用输出模板时随模板正文字体走。"""
        doc = Document()
        p = doc.add_paragraph()
        _add_llm_run(p, "测试文本")

        run = p.runs[-1]
        assert _LLM_INFO_GLYPH in run.text
        assert "测试文本" in run.text
        # 黑色、非斜体正文（非 gray-italic）——provenance 由 ⓘ + 免责声明承载
        assert not run.italic
        # FIX C：不硬编码 size/name，运行继承 Normal（None = 取样式值），有模板时随模板字体
        assert run.font.size is None
        assert run.font.name is None

    def test_disclaimer_line_added(self):
        doc = Document()
        _add_llm_disclaimer_line(doc)
        last_p = doc.paragraphs[-1]
        assert last_p.runs[0].italic is True

    def test_generated_disclaimer_section(self):
        doc = Document()
        _add_generated_disclaimer_section(doc)
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        assert any(_LLM_END_DISCLAIMER_ZH in t for t in texts)


class TestRenderReportWithLLMSupplements:
    def test_disclaimer_present_when_supplements(self):
        report = RiskReport(
            llm_supplements={"subject.name": "阿莫西林"},
            llm_generated_fields={"subject.name"},
        )
        docx_bytes = render_risk_report(report)
        doc = Document(io.BytesIO(docx_bytes))
        all_text = "\n".join(p.text for p in doc.paragraphs)
        assert _LLM_END_DISCLAIMER_ZH in all_text

    def test_no_disclaimer_when_no_supplements(self):
        report = RiskReport()
        docx_bytes = render_risk_report(report)
        doc = Document(io.BytesIO(docx_bytes))
        all_text = "\n".join(p.text for p in doc.paragraphs)
        assert _LLM_END_DISCLAIMER_ZH not in all_text

    def test_info_glyph_present_for_supplemented_content(self):
        report = RiskReport(
            llm_supplements={"subject.desc": "LLM生成内容"},
            llm_generated_fields={"subject.desc"},
        )
        docx_bytes = render_risk_report(report)
        doc = Document(io.BytesIO(docx_bytes))
        all_runs_text = ""
        for p in doc.paragraphs:
            for run in p.runs:
                all_runs_text += run.text
        assert _LLM_INFO_GLYPH in all_runs_text


class TestRenderSemanticSlots:
    """016+: 语义化插槽 正文 renders as an LLM-annotated block (heading + ⓘ prose +
    disclaimer), mirroring 015 section narratives. No-op when the list is empty."""

    _SLOT = {
        "slot_id": "analysis.overview",
        "section_id": "s1",
        "label": "综合分析",
        "text": "本节综述：由 Acme 制药生产，风险经确定性评估为可控。",
    }

    def test_semantic_slot_heading_and_annotated_text(self):
        doc = Document()
        _add_semantic_slots(doc, RiskReport(semantic_slots=[self._SLOT]))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        # heading uses the slot label, and the synthesized text is ⓘ-annotated
        assert any("综合分析" in t for t in texts)
        runs = "".join(r.text for p in doc.paragraphs for r in p.runs)
        assert _LLM_INFO_GLYPH in runs
        assert self._SLOT["text"] in runs

    def test_empty_semantic_slots_is_noop(self):
        doc = Document()
        before = len(doc.paragraphs)
        _add_semantic_slots(doc, RiskReport())
        assert len(doc.paragraphs) == before  # nothing added

    def test_render_report_includes_disclaimer_with_only_semantic_slots(self):
        """A report whose ONLY LLM content is a semantic slot still gets the
        end-of-report disclaimer (parity with llm_supplements/section_narratives)."""
        report = RiskReport(
            semantic_slots=[self._SLOT],
            llm_generated_fields={"analysis.overview"},
        )
        docx_bytes = render_risk_report(report)
        doc = Document(io.BytesIO(docx_bytes))
        all_text = "\n".join(p.text for p in doc.paragraphs)
        assert _LLM_END_DISCLAIMER_ZH in all_text
        assert "综合分析" in all_text

    def test_blank_text_slot_skipped(self):
        doc = Document()
        _add_semantic_slots(
            doc,
            RiskReport(semantic_slots=[{**self._SLOT, "text": "   "}]),
        )
        # heading for the block is emitted, but no ⓘ prose run for a blank slot
        runs = "".join(r.text for p in doc.paragraphs for r in p.runs)
        assert _LLM_INFO_GLYPH not in runs
