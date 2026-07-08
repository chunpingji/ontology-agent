"""Render a RiskReport to .docx bytes (010, FR-005).

Produces a QS-A-020F05 formatted Word document with:
- Header with document number/revision/date + coverage summary (AST-5)
- SECTION I: Subject description, equipment tables by workshop, assessment table
- SECTION II: Placeholders for risk review and conclusion

When a :class:`CoverageManifest` is supplied (AST-5), the document carries the
no-omission evidence visibly: a coverage banner, an outstanding-materials list,
and red highlighting on any "⚠ 待评估" cell — so a missing material can never be
silently absent from the rendered report.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.services.reporting.ast_template import Group, ReportTemplate, Slot
from app.services.reporting.coverage_validator import MISSING_REQUIRED, CoverageManifest
from app.services.reporting.risk_report_generator import RiskReport

# Visual flag for missing/pending material (AST-5). Matches the warning glyph
# used by the generator's PENDING_LEVEL and the template's missing_placeholder.
_WARN_GLYPH = "⚠"
_WARN_COLOR = RGBColor(0xC0, 0x00, 0x00)

# 013: LLM-generated content visual annotation
_LLM_INFO_GLYPH = "ⓘ"
_LLM_COLOR = RGBColor(0x80, 0x80, 0x80)  # gray
_LLM_DISCLAIMER_ZH = "以上内容由文档抽取结果自动生成，仅供参考，请核对后确认。"
_LLM_END_DISCLAIMER_ZH = (
    "本报告中标注 ⓘ 的内容由 LLM 自动生成或补充，仅供参考，不替代人工审核。"
)


def _add_llm_run(paragraph, text: str) -> None:
    """Add an ⓘ-marked run for LLM-sourced content: black, upright body text.

    Provenance is carried by the leading ⓘ marker plus the trailing disclaimer line
    (:func:`_add_llm_disclaimer_line`), NOT by de-emphasising the prose — the body
    must read as ordinary black, non-italic report text (per author request)."""
    run = paragraph.add_run(f"{_LLM_INFO_GLYPH} {text}")
    run.font.size = Pt(10.5)
    run.font.name = "宋体"


def _add_llm_disclaimer_line(doc: Document) -> None:
    """Add a per-section LLM disclaimer paragraph."""
    p = doc.add_paragraph()
    run = p.add_run(_LLM_DISCLAIMER_ZH)
    run.italic = True
    run.font.color.rgb = _LLM_COLOR
    run.font.size = Pt(8)
    run.font.name = "宋体"


def _add_generated_disclaimer_section(doc: Document) -> None:
    """Add end-of-report generated-content disclaimer (FR-006)."""
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(_LLM_END_DISCLAIMER_ZH)
    run.italic = True
    run.bold = True
    run.font.color.rgb = _LLM_COLOR
    run.font.size = Pt(9)
    run.font.name = "宋体"


def _add_section_narratives(doc: Document, report: RiskReport) -> None:
    """Render per-section 行文 narrative prose under its own headings (015).

    Each entry of ``report.section_narratives`` is ``{section_id, title, text}``
    generated at report time from the section's 行文 ``prompt``. The prose is
    LLM-sourced, so every block carries the gray-italic ⓘ marker and a per-block
    disclaimer — additive narrative that never alters deterministic evaluation
    (FR-009). No-op when there are no narratives (legacy / LLM-off reports).
    """
    if not report.section_narratives:
        return
    doc.add_heading("章节行文 Section Narratives", level=2)
    for entry in report.section_narratives:
        title = entry.get("title") or entry.get("section_id") or ""
        text = entry.get("text") or ""
        if not text.strip():
            continue
        if title:
            doc.add_heading(title, level=3)
        p = doc.add_paragraph()
        _add_llm_run(p, text)
        _add_llm_disclaimer_line(doc)


def _add_semantic_slots(doc: Document, report: RiskReport) -> None:
    """Render per-slot 语义化插槽 synthesized text under its own heading (016+).

    Each entry of ``report.semantic_slots`` is ``{slot_id, section_id, label, text}``
    synthesized at report time from the slot's projection of its Section (行文
    ``prompt`` + ``coverage`` associated ontology + read-only deterministic rule
    results). The prose is LLM-sourced, so every block carries the gray-italic ⓘ
    marker and a per-block disclaimer — additive content that never alters the
    deterministic risk matrix (FR-009). No-op for legacy / LLM-off reports.
    """
    if not report.semantic_slots:
        return
    doc.add_heading("语义化插槽 Semantic Slots", level=2)
    for entry in report.semantic_slots:
        title = entry.get("label") or entry.get("slot_id") or ""
        text = entry.get("text") or ""
        if not text.strip():
            continue
        if title:
            doc.add_heading(title, level=3)
        p = doc.add_paragraph()
        _add_llm_run(p, text)
        _add_llm_disclaimer_line(doc)


def render_risk_report(
    report: RiskReport,
    manifest: CoverageManifest | None = None,
    template: ReportTemplate | None = None,
) -> bytes:
    """Render ``RiskReport`` dataclass to .docx bytes.

    ``manifest`` (AST-5) is optional for backward compatibility; when given, its
    coverage status is surfaced in the rendered document.

    ``template`` (016+) drives the document STRUCTURE: when supplied, the body is
    generated by walking ``template.sections → groups → slots`` so the rendered
    Sections / sub-Sections follow the authored AST template exactly (each slot in
    place — semantic → LLM text, deterministic → manifest value, table groups →
    the equipment / risk-matrix tables). When ``template`` is ``None`` the legacy
    fixed QS-A-020F05 skeleton is rendered (backward-compatible safety fallback).
    """
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)
    style.font.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    section = doc.sections[0]
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.5)

    _add_page_header(doc, report, manifest)
    _add_header(doc, report)
    _add_coverage_banner(doc, manifest)

    if template is not None and getattr(template, "sections", None):
        _render_template_sections(doc, report, manifest, template)
        _add_outstanding_materials(doc, manifest)
    else:
        _add_section_one(doc, report)
        _add_assessment_table(doc, report)
        _add_outstanding_materials(doc, manifest)
        _add_section_two(doc, report)
        _add_section_narratives(doc, report)
        _add_semantic_slots(doc, report)

    if report.llm_supplements or report.section_narratives or report.semantic_slots:
        _add_generated_disclaimer_section(doc)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 016+: template-driven rendering — walk the authored Section/Group/Slot tree
# --------------------------------------------------------------------------- #


def _render_template_sections(
    doc: Document,
    report: RiskReport,
    manifest: CoverageManifest | None,
    template: ReportTemplate,
) -> None:
    """Render the document body from the AST template's own structure (016+).

    Sections become level-2 headings, groups level-3, and each slot renders in
    place. Deterministic slot values come from ``manifest`` (indexed by base
    slot_id); semantic-slot text comes from ``report.semantic_slots``; per-section
    行文 narrative (015) renders under its section heading. Table groups
    (equipment / assessment) render the deterministic tables. This makes the
    rendered report follow the authored template rather than a fixed skeleton.
    """
    slot_by_id: dict[str, list] = {}
    if manifest is not None:
        for sc in manifest.slots:
            base = sc.slot_id.split("[")[0]
            slot_by_id.setdefault(base, []).append(sc)
    sem_by_id = {
        s.get("slot_id"): s for s in (report.semantic_slots or []) if s.get("slot_id")
    }
    narr_by_section = {
        n.get("section_id"): n
        for n in (report.section_narratives or [])
        if n.get("section_id")
    }

    for sec in template.sections:
        doc.add_heading(sec.title, level=2)
        narr = narr_by_section.get(sec.section_id)
        if narr and (narr.get("text") or "").strip():
            _add_markdown_body(doc, narr["text"])
            _add_llm_disclaimer_line(doc)
        for grp in sec.groups:
            _render_group(doc, report, grp, slot_by_id, sem_by_id)


def _render_group(
    doc: Document,
    report: RiskReport,
    group: Group,
    slot_by_id: dict[str, list],
    sem_by_id: dict[str, dict],
) -> None:
    """Render one template group under its own level-3 heading, dispatched by kind."""
    if group.kind == "equipment_table":
        doc.add_heading(group.title, level=3)
        _render_equipment_tables(doc, report)
        return
    if group.kind == "assessment_table":
        doc.add_heading(group.title, level=3)
        _render_assessment_matrix(doc, report)
        return

    # fields | manual → render each slot in place. Semantic slots are absorbed into
    # the section narrative (section-level generation), so ``report.semantic_slots``
    # is normally empty: a group that is ONLY semantic slots with no per-slot text
    # renders nothing — skip its heading entirely rather than emit an empty heading
    # or a spurious 「（待补充）」. Deterministic (non-semantic) slots keep their old
    # behaviour, including the 待补充 placeholder when a required value is missing.
    non_semantic = [
        s for s in group.slots if getattr(s.source, "kind", None) != "semantic"
    ]
    has_sem_text = any(
        ((sem_by_id.get(s.slot_id) or {}).get("text", "") or "").strip()
        for s in group.slots
        if getattr(s.source, "kind", None) == "semantic"
    )
    if not non_semantic and not has_sem_text:
        return

    doc.add_heading(group.title, level=3)
    rendered_any = False
    for slot in group.slots:
        if getattr(slot.source, "kind", None) == "semantic":
            if _render_semantic_slot(doc, slot, sem_by_id.get(slot.slot_id)):
                rendered_any = True
        elif _render_well_known_slot(doc, report, slot):
            rendered_any = True
        elif _render_value_slot(doc, slot, slot_by_id.get(slot.slot_id, [])):
            rendered_any = True
    if not rendered_any and non_semantic:
        doc.add_paragraph("（待补充）")


# Legacy QS-A-020F05 manual slots map to structured RiskReport fields rather than
# to a manifest value; this bridge preserves their rich rendering under the
# template-driven walk (team / review / conclusion / approvals).
def _render_well_known_slot(doc: Document, report: RiskReport, slot: Slot) -> bool:
    """Render a manual slot that maps to a dedicated RiskReport field. Returns
    ``True`` when it claimed the slot, ``False`` to fall through to generic rendering."""
    sid = slot.slot_id.split("[")[0]
    if sid == "team.members":
        _render_team_table(doc, report)
        return True
    if sid == "review.text":
        doc.add_paragraph(report.risk_review or "（定期回顾时补充）")
        return True
    if sid == "conclusion.text":
        if "conclusion" in report.llm_generated_fields and report.conclusion:
            p = doc.add_paragraph()
            _add_llm_run(p, report.conclusion)
            _add_llm_disclaimer_line(doc)
        else:
            doc.add_paragraph(report.conclusion or "（待补充）")
        return True
    if sid == "approvals.table":
        _render_approvers_table(doc, report)
        return True
    return False


def _render_value_slot(doc: Document, slot: Slot, coverages: list) -> bool:
    """Render a deterministic (non-semantic) slot from its manifest coverage rows.

    Missing-required → the slot's warn placeholder (red+bold); a value → ``标签：值``
    (ⓘ-annotated when LLM-sourced); blank → ``（待补充）`` unless ``on_missing`` is
    ``leave_blank``. Returns ``True`` when anything was rendered."""
    if not coverages:
        if getattr(slot, "on_missing", "annotate") == "leave_blank":
            return False
        p = doc.add_paragraph()
        p.add_run(f"{slot.label}：（待补充）")
        return True

    rendered = False
    for sc in coverages:
        if sc.status == MISSING_REQUIRED:
            p = doc.add_paragraph()
            run = p.add_run(
                slot.missing_placeholder or f"{_WARN_GLYPH} {slot.label}（待评估）"
            )
            run.bold = True
            run.font.color.rgb = _WARN_COLOR
            rendered = True
        elif sc.value:
            p = doc.add_paragraph()
            p.add_run(f"{slot.label}：")
            if getattr(sc, "is_llm_sourced", False):
                _add_llm_run(p, sc.value)
            else:
                p.add_run(sc.value)
            rendered = True
        elif getattr(slot, "on_missing", "annotate") != "leave_blank":
            p = doc.add_paragraph()
            p.add_run(f"{slot.label}：（待补充）")
            rendered = True
    return rendered


def _render_semantic_slot(doc: Document, slot: Slot, entry: dict | None) -> bool:
    """Render a semantic slot's LLM-synthesized text (016+): ⓘ-annotated prose and
    real Word tables for any Markdown table, followed by a per-block disclaimer.
    Returns ``False`` (renders nothing) when the slot produced no text."""
    text = (entry or {}).get("text", "") if entry else ""
    if not text or not text.strip():
        return False
    _add_markdown_body(doc, text)
    _add_llm_disclaimer_line(doc)
    return True


def _render_team_table(doc: Document, report: RiskReport) -> None:
    """Render the assessment-team member table (legacy manual slot)."""
    if report.team_members:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = t.rows[0].cells
        hdr[0].text = "姓名\nName"
        hdr[1].text = "职务\nTitle"
        hdr[2].text = "签名\nSignature"
        for m in report.team_members:
            row = t.add_row().cells
            row[0].text = m.get("name", "")
            row[1].text = m.get("title", "")
    else:
        doc.add_paragraph("（待补充）")


def _render_approvers_table(doc: Document, report: RiskReport) -> None:
    """Render the approvals table (legacy manual slot)."""
    if report.approvers:
        t = doc.add_table(rows=1, cols=4)
        t.style = "Table Grid"
        hdr = t.rows[0].cells
        hdr[0].text = "角色\nRole"
        hdr[1].text = "姓名\nName"
        hdr[2].text = "签名\nSignature"
        hdr[3].text = "日期\nDate"
        for a in report.approvers:
            row = t.add_row().cells
            row[0].text = a.get("role", "")
            row[1].text = a.get("name", "")
    else:
        doc.add_paragraph("（待补充）")


def _render_equipment_tables(doc: Document, report: RiskReport) -> None:
    """Per-workshop equipment tables (heading-less; reused by the template-driven
    ``equipment_table`` group and the legacy skeleton)."""
    if not report.equipment_tables:
        doc.add_paragraph("（待补充）")
        return
    for workshop, entries in report.equipment_tables.items():
        doc.add_paragraph(f"● {workshop}", style="List Bullet")
        t = doc.add_table(rows=1, cols=5)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = t.rows[0].cells
        hdr[0].text = "序号\nNo."
        hdr[1].text = "设备编号\nEquipment ID"
        hdr[2].text = "设备名称\nName"
        hdr[3].text = "规格\nSpecification"
        hdr[4].text = "材质\nMaterial"
        for e in entries:
            row = t.add_row().cells
            row[0].text = str(e.seq)
            row[1].text = e.equipment_id
            row[2].text = e.name
            row[3].text = e.spec
            row[4].text = e.material
    for note in report.equipment_notes:
        doc.add_paragraph(f"注: {note}", style="List Bullet")


# --------------------------------------------------------------------------- #
# Lightweight Markdown-body rendering (semantic slots may emit Markdown tables)
# --------------------------------------------------------------------------- #


def _is_table_separator(line: str) -> bool:
    """A Markdown table separator row, e.g. ``|---|:--:|---|``."""
    s = line.strip().replace(" ", "")
    if not s or "-" not in s:
        return False
    return set(s) <= {"|", "-", ":"}


def _is_table_row(line: str) -> bool:
    return "|" in line


def _split_cells(row: str) -> list[str]:
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _add_markdown_table(doc: Document, block: list[str]) -> None:
    """Render a Markdown table block (header, separator, body rows) as a Word table."""
    header = _split_cells(block[0])
    ncols = len(header) or 1
    t = doc.add_table(rows=1, cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for k in range(ncols):
        hdr[k].text = (header[k] if k < len(header) else "").replace("**", "")
        for p in hdr[k].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(9)
                run.font.name = "宋体"
    for raw in block[2:]:
        cells = _split_cells(raw)
        row = t.add_row().cells
        for k in range(ncols):
            row[k].text = (cells[k] if k < len(cells) else "").replace("**", "")
            for p in row[k].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
                    run.font.name = "宋体"


_BOLD_SPLIT_RE = re.compile(r"(\*\*[^*]+\*\*)")


def _add_inline_runs(paragraph, text: str, *, prefix: str = "", bold_all: bool = False) -> None:
    """Emit ``text`` as black, upright runs, honoring ``**bold**`` inline emphasis and
    stripping stray Markdown ``*`` markers so nothing renders as literal syntax."""
    if prefix:
        r = paragraph.add_run(prefix)
        r.font.size = Pt(10.5)
        r.font.name = "宋体"
    for part in _BOLD_SPLIT_RE.split(text):
        if not part:
            continue
        bold = bold_all
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            content = part[2:-2]
            bold = True
        else:
            content = part.replace("*", "")  # drop leftover single-* emphasis markers
        if not content:
            continue
        run = paragraph.add_run(content)
        run.bold = bold
        run.font.size = Pt(10.5)
        run.font.name = "宋体"


def _add_markdown_line(doc: Document, text: str, *, prefix: str = "") -> None:
    """Render one Markdown text line: ``#…`` headings → bold paragraph; ``-``/``*``/``+``
    or ``1.`` list items → bulleted line; everything else → normal paragraph. Inline
    ``**bold**`` is honored. All black and upright (no gray/italic)."""
    p = doc.add_paragraph()
    m = re.match(r"^(#{1,6})\s+(.*)$", text)
    if m:  # heading → bold paragraph (avoids clobbering the doc heading hierarchy)
        _add_inline_runs(p, m.group(2).strip(), prefix=prefix, bold_all=True)
        return
    m = re.match(r"^\s*[-*+]\s+(.*)$", text)
    if m:  # unordered list item
        _add_inline_runs(p, m.group(1).strip(), prefix=f"{prefix}• ")
        return
    m = re.match(r"^\s*(\d+\.)\s+(.*)$", text)
    if m:  # ordered list item — keep its number
        _add_inline_runs(p, m.group(2).strip(), prefix=f"{prefix}{m.group(1)} ")
        return
    _add_inline_runs(p, text, prefix=prefix)


def _add_markdown_body(doc: Document, text: str) -> None:
    """Render LLM Markdown ``text`` as clean black body: pipe-delimited table blocks
    become real Word tables (Table Grid); ``#`` headings, ``**bold**`` and ``-``/``1.``
    lists are parsed to Word formatting rather than emitted as literal syntax. The ⓘ
    marker leads the first prose paragraph (provenance); the caller adds the trailing
    disclaimer line."""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    first_prose = True
    while i < n:
        line = lines[i]
        if (
            _is_table_row(line)
            and i + 1 < n
            and _is_table_separator(lines[i + 1])
        ):
            block = [line, lines[i + 1]]
            j = i + 2
            while j < n and _is_table_row(lines[j]) and lines[j].strip():
                block.append(lines[j])
                j += 1
            _add_markdown_table(doc, block)
            i = j
            continue
        if line.strip():
            prefix = ""
            if first_prose:
                prefix = f"{_LLM_INFO_GLYPH} "
                first_prose = False
            _add_markdown_line(doc, line.strip(), prefix=prefix)
        i += 1


def _add_page_header(
    doc: Document, report: RiskReport, manifest: CoverageManifest | None = None
) -> None:
    header = doc.sections[0].header
    header.is_linked_to_previous = False
    p = header.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run(f"{report.doc_no}  Rev.{report.revision}")
    run.font.size = Pt(8)
    run.font.name = "宋体"
    if report.effective_date:
        run2 = p.add_run(f"  |  {report.effective_date}")
        run2.font.size = Pt(8)
        run2.font.name = "宋体"
    if manifest is not None and manifest.has_omissions:
        warn = p.add_run(f"  |  {_WARN_GLYPH} {manifest.missing_required} 项待补充")
        warn.font.size = Pt(8)
        warn.font.name = "宋体"
        warn.bold = True
        warn.font.color.rgb = _WARN_COLOR


def _add_coverage_banner(doc: Document, manifest: CoverageManifest | None) -> None:
    """A one-line material-coverage summary directly under the title (AST-5)."""
    if manifest is None:
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    parts = [
        f"素材覆盖：共 {manifest.total_slots} 项",
        f"已填充 {manifest.filled}",
        f"推理 {manifest.inferred}",
        f"人工 {manifest.manual}",
        f"待补充 {manifest.missing_required}",
    ]
    if manifest.dismissed > 0:
        parts.append(f"不适用 {manifest.dismissed}")
    summary = " · ".join(parts)
    run = p.add_run(summary)
    run.font.size = Pt(9)
    run.font.name = "宋体"
    if manifest.has_omissions:
        run.bold = True
        run.font.color.rgb = _WARN_COLOR


def _add_header(doc: Document, report: RiskReport) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"风险评估表 ({report.doc_no})")
    run.bold = True
    run.font.size = Pt(16)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    meta.add_run(f"修订号: {report.revision}    生效日期: {report.effective_date}")


def _add_section_one(doc: Document, report: RiskReport) -> None:
    doc.add_heading("SECTION I  风险评估", level=2)

    doc.add_heading("1. 风险评估对象 Subject Description", level=3)
    has_llm_in_section = False
    if report.subject_description:
        if "subject_description" in report.llm_generated_fields:
            p = doc.add_paragraph()
            _add_llm_run(p, report.subject_description)
            _add_llm_disclaimer_line(doc)
            has_llm_in_section = True
        else:
            doc.add_paragraph(report.subject_description)
    elif report.llm_supplements:
        p = doc.add_paragraph()
        supplement_parts = [
            f"{sid}: {val}" for sid, val in report.llm_supplements.items()
            if "subject" in sid.lower()
        ]
        if supplement_parts:
            _add_llm_run(p, "；".join(supplement_parts))
            has_llm_in_section = True
        else:
            p.add_run("（待补充）")
    else:
        doc.add_paragraph("（待补充）")

    doc.add_heading("2. 评估小组 Assessment Team", level=3)
    if report.team_members:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = t.rows[0].cells
        hdr[0].text = "姓名\nName"
        hdr[1].text = "职务\nTitle"
        hdr[2].text = "签名\nSignature"
        for m in report.team_members:
            row = t.add_row().cells
            row[0].text = m.get("name", "")
            row[1].text = m.get("title", "")
    else:
        doc.add_paragraph("（待补充）")

    if report.equipment_tables:
        doc.add_heading("3. 设备一览表 Equipment List", level=3)
        _render_equipment_tables(doc, report)

    llm_vals = {
        sid: val for sid, val in report.llm_supplements.items()
        if "subject" not in sid.lower()
        and "equipment" not in sid.lower()
        and "assessment" not in sid.lower()
    }
    if llm_vals:
        doc.add_heading("LLM 补充数据", level=3)
        for sid, val in llm_vals.items():
            p = doc.add_paragraph()
            p.add_run(f"{sid}：")
            _add_llm_run(p, val)
        _add_llm_disclaimer_line(doc)
        has_llm_in_section = True

    if has_llm_in_section:
        _add_llm_disclaimer_line(doc)


def _add_assessment_table(doc: Document, report: RiskReport) -> None:
    doc.add_heading("4. 风险评估 Risk Assessment", level=3)
    _render_assessment_matrix(doc, report)


def _render_assessment_matrix(doc: Document, report: RiskReport) -> None:
    """The deterministic risk-assessment matrix (heading-less; reused by the
    template-driven ``assessment_table`` group and the legacy skeleton)."""
    headers = [
        "HazID\n风险类型",
        "Contributing Factors\n风险因素",
        "Pre-Control\n控制前风险等级",
        "Post-Control\n控制后风险等级",
        "Control Measures\n风险控制措施",
        "Traceability\n控制可追溯性",
        "Status\n风险状态",
    ]

    col_widths = [Cm(2.5), Cm(5.5), Cm(2.5), Cm(2.5), Cm(6.0), Cm(4.0), Cm(2.5)]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, w in enumerate(col_widths):
        t.columns[i].width = w
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = h
        hdr[i].width = col_widths[i]
        for p in hdr[i].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(9)
                run.font.name = "宋体"

    for row_data in report.assessment_rows:
        row = t.add_row().cells
        vals = [
            row_data.hazid, row_data.contributing_factors,
            row_data.pre_control_level, row_data.post_control_level,
            row_data.control_measures, row_data.traceability, row_data.status,
        ]
        for i, val in enumerate(vals):
            row[i].text = val
            pending = bool(val) and val.startswith(_WARN_GLYPH)
            for p in row[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
                    run.font.name = "宋体"
                    if pending:  # AST-5: 待评估 cells flagged red+bold
                        run.bold = True
                        run.font.color.rgb = _WARN_COLOR

    doc.add_paragraph()
    doc.add_paragraph(f"QA 评论: {report.qa_comments or '（无）'}")


def _add_outstanding_materials(
    doc: Document, manifest: CoverageManifest | None
) -> None:
    """List every required slot that could not be filled (AST-5, no-omission proof).

    This subsection makes the G1 omissions explicit and reviewable: each missing
    required material is named with its dimension/source so a human knows exactly
    what to supply — the report never silently drops a required input.
    Dismissed slots are listed separately as "N/A（不适用）" (011 FR-API-006).
    """
    has_missing = manifest is not None and manifest.has_omissions
    has_dismissed = manifest is not None and manifest.dismissed > 0
    if not has_missing and not has_dismissed:
        return
    doc.add_heading("5. 待补充素材清单 Outstanding Materials", level=3)
    if has_missing:
        intro = doc.add_paragraph()
        run = intro.add_run(
            f"{_WARN_GLYPH} 以下 {manifest.missing_required} 项必填素材未能从抽取数据中确定，"
            "需人工补充后方可定论；在此之前相关结论标记为「待评估」。"
        )
        run.bold = True
        run.font.color.rgb = _WARN_COLOR
        for slot in manifest.missing_required_slots:
            label = slot.label or slot.slot_id
            text = f"{label}（{slot.slot_id}）"
            if slot.note:
                text += f" — {slot.note}"
            doc.add_paragraph(text, style="List Bullet")
    if has_dismissed:
        intro_d = doc.add_paragraph()
        run_d = intro_d.add_run(
            f"以下 {manifest.dismissed} 项已确认为不适用（N/A），不计入缺失。"
        )
        run_d.font.size = Pt(9)
        run_d.font.name = "宋体"
        for slot in manifest.dismissed_slots:
            label = slot.label or slot.slot_id
            doc.add_paragraph(f"{label}（{slot.slot_id}）— N/A（不适用）", style="List Bullet")


def _add_section_two(doc: Document, report: RiskReport) -> None:
    doc.add_heading("SECTION II  风险回顾 Risk Review", level=2)
    doc.add_paragraph(report.risk_review or "（定期回顾时补充）")

    doc.add_heading("结论 Conclusion", level=3)
    if "conclusion" in report.llm_generated_fields and report.conclusion:
        p = doc.add_paragraph()
        _add_llm_run(p, report.conclusion)
        _add_llm_disclaimer_line(doc)
    else:
        doc.add_paragraph(report.conclusion or "（待补充）")

    doc.add_heading("审批 Approvals", level=3)
    if report.approvers:
        t = doc.add_table(rows=1, cols=4)
        t.style = "Table Grid"
        hdr = t.rows[0].cells
        hdr[0].text = "角色\nRole"
        hdr[1].text = "姓名\nName"
        hdr[2].text = "签名\nSignature"
        hdr[3].text = "日期\nDate"
        for a in report.approvers:
            row = t.add_row().cells
            row[0].text = a.get("role", "")
            row[1].text = a.get("name", "")
    else:
        doc.add_paragraph("（待补充）")
