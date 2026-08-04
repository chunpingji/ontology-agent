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

import copy
import io
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt, RGBColor

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

# 规则文案（风险评估矩阵三列）经前端富文本编辑常以 HTML <br> 承载换行；直接写入 DOCX 单元格
# 会原样显示字面「<br>」而非换行。python-docx 的 run.text setter 会把 \n 转成 Word 软换行
# (<w:br/>)，故渲染前把 <br>/<br/>/<br /> (含大小写与内部空白) 归一为 \n。对纯 \n 文案幂等。
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def _cell_text(text: str | None) -> str:
    """把单元格文案里的 HTML 换行标记 ``<br>`` 归一为 ``\\n``（Word 软换行）；``None`` → ``""``。"""
    return _BR_RE.sub("\n", text) if text else ""


def _display_value(value: object) -> str:
    """Render an audit value without leaking Python's ``None`` into the report."""
    return "—" if value is None or value == "" else str(value)


def _pde_value_text(side: dict) -> str:
    """Compact, unit-explicit PDE/OEB text for one side of a conflict."""
    mg = side.get("pde_mg_day")
    ug = side.get("pde_ug_day")
    if mg is None and ug is not None:
        try:
            mg = round(float(ug) / 1000.0, 6)
        except (TypeError, ValueError):
            pass
    return (
        f"PDE {_display_value(mg)} mg/日"
        f"（{_display_value(ug)} µg/日）；"
        f"OEB band {_display_value(side.get('band'))}"
    )


def _provenance_text(derived: dict) -> str:
    """Render the deterministic derivation basis as a compact audit trail."""
    provenance = derived.get("provenance") or {}
    parts: list[str] = []
    method = provenance.get("method") or {}
    if method.get("id") or method.get("version"):
        parts.append(
            f"方法：{_display_value(method.get('id'))} "
            f"v{_display_value(method.get('version'))}"
        )
    if provenance.get("formula"):
        parts.append(f"公式：{provenance['formula']}")
    inputs = provenance.get("inputs") or {}
    if inputs:
        input_labels = {
            "noael_mg_kg_day": "NOAEL(mg/kg/日)",
            "species": "种属",
            "study_duration_days": "研究周期(日)",
            "noael_is_loael": "是否 LOAEL",
            "genotoxic": "遗传毒性",
            "carcinogenic": "致癌性",
            "reproductive_toxicant": "生殖毒性",
        }
        rendered = [
            f"{input_labels[key]}={_display_value(inputs.get(key))}"
            for key in input_labels
            if key in inputs
        ]
        if rendered:
            parts.append("输入：" + "；".join(rendered))
    factors = provenance.get("factors") or {}
    if factors:
        parts.append(
            "因子：" + "；".join(
                f"{key}={_display_value(value)}" for key, value in factors.items()
            )
        )
    method_ref = method.get("regulation_ref")
    if method_ref:
        parts.append(f"依据：{method_ref}")
    return "\n".join(parts) or "—"


def _render_pde_conflicts(doc: Document, report: RiskReport) -> None:
    """Render asserted/derived PDE values and the immutable decision snapshot."""
    if not report.pde_conflicts:
        return

    doc.add_heading("PDE/OEB 潜能等级冲突及裁决", level=2)
    for index, conflict in enumerate(report.pde_conflicts, start=1):
        if len(report.pde_conflicts) > 1:
            doc.add_heading(f"冲突 {index}", level=3)

        asserted = conflict.get("asserted") or {}
        derived = conflict.get("derived") or {}
        decision = conflict.get("decision") or {}
        effective = conflict.get("effective")
        pending = not effective

        notice = doc.add_paragraph()
        if pending:
            run = notice.add_run(
                f"{_WARN_GLYPH} 待人工裁决：本项结论为待评估，不得视为全部风险可以接受。"
            )
            run.bold = True
            run.font.color.rgb = _WARN_COLOR
        else:
            notice.add_run(
                f"已完成人工裁决：本次报告采用{effective.get('source_label') or '已选'}数据，"
                "同时保留原文与推导数据。"
            )

        rows: list[tuple[str, str]] = [
            ("冲突摘要", _display_value(conflict.get("summary"))),
            ("原文断言", _pde_value_text(asserted)),
            ("确定性推导", _pde_value_text(derived)),
            (
                "差异程度",
                f"潜能等级相差 {_display_value(conflict.get('delta_bands'))} 档；"
                f"PDE 比值 {_display_value(conflict.get('pde_ratio'))}",
            ),
            (
                "裁决状态",
                "待裁决" if pending else f"已裁决（采纳{effective.get('source_label') or '已选'}值）",
            ),
            ("本报告有效值", "—（待裁决）" if pending else _pde_value_text(effective)),
            ("裁决人", _display_value(decision.get("actor"))),
            ("裁决时间", _display_value(decision.get("decided_at"))),
            ("裁决备注", _display_value(decision.get("note"))),
            ("推导依据与溯源", _provenance_text(derived)),
        ]
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for label, value in rows:
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = value
            for run in cells[0].paragraphs[0].runs:
                run.bold = True
            if pending and label == "裁决状态":
                for paragraph in cells[1].paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.color.rgb = _WARN_COLOR
        doc.add_paragraph()


def _clear_body(doc: Document) -> None:
    """Remove body content while preserving ALL section properties (page size/orientation/
    margins, header/footer references) — not just the final one.

    DOCX 的末节属性在 body 尾部的 <w:sectPr>；而中间各节（如「纵向封面 + 横向正文」或每节
    不同页眉页脚）的属性存放在各自**分节段落**的 <w:pPr>/<w:sectPr> 中，会随段落一起被删。
    只保留末节会把前面各节的页面布局与页眉页脚全部丢失（Codex #2，已复现：两节→一节）。
    故：段落若在 pPr 内携带 sectPr，则保留该段落但清空其可见内容（runs 等），保住分节骨架；
    其余段落/表格删除；body 尾部 sectPr 原样保留。单节模板不含分节段落 → 行为与旧实现逐字一致。"""
    body = doc.element.body
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            continue  # 末节属性
        if child.tag == qn("w:p"):
            pPr = child.find(qn("w:pPr"))
            if pPr is not None and pPr.find(qn("w:sectPr")) is not None:
                # 分节段落：保留 pPr（含 sectPr）承载该节页面/页眉页脚，仅清空其余内容
                for sub in list(child):
                    if sub.tag != qn("w:pPr"):
                        child.remove(sub)
                continue
        body.remove(child)


# Built-in styles the body renderer relies on (add_heading levels 1-3, tables,
# bullet lists). python-docx's own default template always ships them, but a
# user-supplied sample .docx authored elsewhere (LibreOffice / Google Docs /
# hand-minimised Word) may omit them — assigning a missing style then raises
# KeyError and aborts the whole render. We backfill any missing one from a fresh
# blank document so the template path is as robust as the blank path.
_REQUIRED_STYLES = ("Heading 1", "Heading 2", "Heading 3", "Table Grid", "List Bullet")


def _ensure_builtin_styles(doc: Document) -> None:
    """Backfill required built-in styles the sample template may lack (idempotent).

    按 style NAME 判定缺失（add_heading / t.style=... 等下游调用都按 name 解析样式）。补齐时
    须同时避免 styleId 冲突：模板可能已含目标 styleId 却把 name 改成本地化/自定义名（如中文
    Word 的「标题 1」，或作者手改）——此时若原样 append，会出现两个相同 styleId 的 style，Word
    视为损坏样式表。但若因 styleId 冲突而**跳过**补齐，则规范 name 仍不存在，随后 add_heading(
    "Heading 1") 会抛 KeyError 使整篇渲染崩溃（Codex #3，已复现）。故当 styleId 冲突时，改用一
    个全新的唯一 styleId 插入携带规范 name 的内建样式：既不产生重复 styleId，又让按 name 的查找
    可解析。
    注：List Bullet 依赖 numbering、Table Grid 依赖 TableNormal、Heading 依赖其 Char 链接
    样式；此处不深拷这些依赖——极简模板下项目符号可能不显示（降级为普通段落缩进），但不会
    崩溃或损坏文档。真正精简到无 numbering/TableNormal 的模板属已知限制。"""
    have_names = {s.name for s in doc.styles}
    have_ids = {s.style_id for s in doc.styles}
    missing = [n for n in _REQUIRED_STYLES if n not in have_names]
    if not missing:
        return
    blank = Document()
    dest = doc.styles.element
    for name in missing:
        try:
            src = blank.styles[name]
        except KeyError:
            continue
        el = copy.deepcopy(src.element)
        if src.style_id in have_ids:  # styleId 冲突：换唯一 styleId 再插入（规范 name 仍可解析）
            n = 1
            new_id = f"{src.style_id}X{n}"
            while new_id in have_ids:
                n += 1
                new_id = f"{src.style_id}X{n}"
            el.set(qn("w:styleId"), new_id)
            have_ids.add(new_id)
        else:
            have_ids.add(src.style_id)
        dest.append(el)
        have_names.add(name)


def _add_llm_run(paragraph, text: str) -> None:
    """Add an ⓘ-marked run for LLM-sourced content: black, upright body text.

    Provenance is carried by the leading ⓘ marker plus the trailing disclaimer line
    (:func:`_add_llm_disclaimer_line`), NOT by de-emphasising the prose — the body
    must read as ordinary black, non-italic report text (per author request)."""
    # 不硬编码字号/字体：继承文档 Normal 样式，正文才能套用输出模板的字体与字号。空白
    # 文档路径下 Normal 即宋体 10.5，输出与此前一致；有模板时随模板正文字体走。
    paragraph.add_run(f"{_LLM_INFO_GLYPH} {text}")


def _add_llm_disclaimer_line(doc: Document) -> None:
    """Add a per-section LLM disclaimer paragraph."""
    p = doc.add_paragraph()
    run = p.add_run(_LLM_DISCLAIMER_ZH)
    run.italic = True
    run.font.color.rgb = _LLM_COLOR
    run.font.size = Pt(8)  # 免责声明保持小号灰斜体；不设 font.name，随模板正文字体（Codex #1）


def _add_generated_disclaimer_section(doc: Document) -> None:
    """Add end-of-report generated-content disclaimer (FR-006)."""
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(_LLM_END_DISCLAIMER_ZH)
    run.italic = True
    run.bold = True
    run.font.color.rgb = _LLM_COLOR
    run.font.size = Pt(9)  # 结尾免责声明保持醒目样式；不设 font.name，随模板正文字体（Codex #1）


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
    sample_docx_path: str | None = None,
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

    ``sample_docx_path`` — when provided, the sample .docx is opened as the base
    ``Document``, preserving its styles, page layout, headers, and footers. The
    body content is cleared and regenerated. Falls back to a blank document on
    error.
    """
    import logging

    _log = logging.getLogger(__name__)

    if sample_docx_path:
        try:
            doc = Document(sample_docx_path)
        except Exception:
            # 容错：仅「打开/解析模板文件」失败才回退空白文档——这是真正的用户输入问题
            # （模板损坏、路径失效等）。_clear_body / _ensure_builtin_styles 的异常刻意不在
            # 此吞掉：它们是编程缺陷，应显式抛出暴露，而非被伪装成「模板损坏」静默产出格式
            # 迥异却状态成功的空白文档（此前宽 except 会掩盖此类内部错误）。
            _log.warning("无法打开示例模板 %s，回退到空白文档", sample_docx_path, exc_info=True)
            sample_docx_path = None
        else:
            _clear_body(doc)
            _ensure_builtin_styles(doc)
            # 完整沿用模板 Normal 样式（字体/字号/eastAsia），不作任何覆盖——正文据此继承
            # 模板的字体与字号（需求核心）。此前强设 eastAsia=宋体既会在 Normal 无 rPr 时
            # 崩溃（AttributeError），也会篡改模板自有的中文字体，二者皆已移除。
            if not doc.sections:
                # 模板整篇省略 <w:sectPr>（合法，Word 用默认版式）：doc.sections 为空，既无页面
                # 几何可沿用，python-docx 的 add_table 亦依赖末节 (_block_width→sections[-1]) 而
                # 直接 IndexError。此类模板无法承载「固化排版」，回退到已充分测试的空白横向 A4
                # 默认路径，报告仍成功生成（Codex R3）。
                _log.warning("示例模板 %s 无分节属性(<w:sectPr>)，回退到空白文档", sample_docx_path)
                sample_docx_path = None

    if not sample_docx_path:
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

    # Report-level deterministic audit block: intentionally outside the AST walk so
    # template-driven, sample-DOCX, and legacy fallback output all retain the conflict.
    _render_pde_conflicts(doc, report)

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
        if note.startswith("APS排期冲突"):
            paragraph = doc.add_paragraph(style="List Bullet")
            run = paragraph.add_run(f"{_WARN_GLYPH} {note}")
            run.bold = True
            run.font.color.rgb = _WARN_COLOR
        else:
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
    # 不再强设 run.font.name="宋体"：该属性只作用于**西文**字形（ascii/hAnsi），会把模板正文
    # 西文字体覆盖成宋体、形成混排；中文字形本就取自 Normal 的 eastAsia，故套用模板时中文已随
    # 模板走。去掉硬编码字体名后西文亦随模板；仅保留紧凑 9pt 以维持密集表格版式（表格字号为
    # 刻意的版式取舍，非「保留模板字号」范畴，见需求说明）。空白文档路径 Normal 西文即宋体，
    # 输出视觉不变（Codex #1）。
    hdr = t.rows[0].cells
    for k in range(ncols):
        hdr[k].text = (header[k] if k < len(header) else "").replace("**", "")
        for p in hdr[k].paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(9)
    for raw in block[2:]:
        cells = _split_cells(raw)
        row = t.add_row().cells
        for k in range(ncols):
            row[k].text = (cells[k] if k < len(cells) else "").replace("**", "")
            for p in row[k].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)


_BOLD_SPLIT_RE = re.compile(r"(\*\*[^*]+\*\*)")


def _add_inline_runs(paragraph, text: str, *, prefix: str = "", bold_all: bool = False) -> None:
    """Emit ``text`` as black, upright runs, honoring ``**bold**`` inline emphasis and
    stripping stray Markdown ``*`` markers so nothing renders as literal syntax.

    正文运行不硬编码字号/字体：继承文档 Normal 样式。空白文档路径 Normal 即宋体 10.5，
    输出与旧版逐字一致；套用输出模板时，正文随模板 Normal 的字体/字号走——这是「完整保留
    模板字体、字号」需求在正文**主路径**上的落点。此前仅 _add_llm_run（次要路径）生效，而
    模板驱动的章节 narrative / semantic slot 主体走 _add_markdown_body→_add_inline_runs，
    仍被强制宋体 10.5pt，等于需求核心在主路径未达成（Codex #6 复核确证）。"""
    if prefix:
        paragraph.add_run(prefix)
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
    run.font.size = Pt(9)  # 覆盖提示行；不设 font.name，随模板正文字体（Codex #1）
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
    # 固定列宽合计 25.5cm 是为默认横向 A4（可用宽 ~25.7cm）设计的；套用纵向/大边距的输出
    # 模板时会超过页面可用宽度而溢出，破坏「保留模板页面布局」。按当前 section 的可用宽度
    # 等比缩放，使表格始终落在保留下来的页面内（Codex #7）。横向默认路径下 25.5<25.7 不触发
    # 缩放，逐字不变；只在窄页模板下收缩。
    # 精简模板可能省略 <w:pgSz>/<w:pgMar>（依赖 Word 默认值），此时 python-docx 返回 None——
    # 直接相减会 None-None TypeError 使整篇渲染崩溃。仅当页宽与左右边距齐备时才计算可用宽并
    # 缩放；缺任一值则保留设计列宽（Codex #8）。装订线 gutter 占用正文，也从可用宽扣除。
    # 表格追加在 body 末尾 → 归属**末节**（sections[-1]），故按末节而非 sections[0] 取几何：
    # 多节模板（如纵向封面 + 横向正文）下正文落在末节，用首节（封面）宽度缩放会错缩/错溢（Codex R2）。
    # 精简模板可能整篇省略 <w:sectPr>（依赖 Word 默认）：此时 doc.sections 为空，不仅本处读几何，
    # 连 python-docx 的 add_table（_block_width→sections[-1]）也会 IndexError。公共入口
    # render_risk_report 已对此类模板回退到空白文档，但本 helper 若被独立调用仍须自保——补一个默认
    # <w:sectPr>（无 pgSz/pgMar，几何仍返回 None → 下方跳过缩放），令 add_table 有末节可测量（Codex R3）。
    if not doc.sections:
        doc.element.body.get_or_add_sectPr()
    sec = doc.sections[-1]
    pw, lm, rm = sec.page_width, sec.left_margin, sec.right_margin
    gutter = getattr(sec, "gutter", None) or 0
    if pw is not None and lm is not None and rm is not None:
        avail = pw - lm - rm - gutter
        total = sum(col_widths)
        if avail > 0 and total > avail:
            scale = avail / total
            col_widths = [Emu(int(w * scale)) for w in col_widths]
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
                run.font.size = Pt(9)  # 紧凑表格版式；不设 font.name，西文/中文均随模板（Codex #1）

    for row_data in report.assessment_rows:
        row = t.add_row().cells
        vals = [
            row_data.hazid, row_data.contributing_factors,
            row_data.pre_control_level, row_data.post_control_level,
            row_data.control_measures, row_data.traceability, row_data.status,
        ]
        for i, val in enumerate(vals):
            row[i].text = _cell_text(val)  # <br> → Word 软换行
            pending = bool(val) and val.startswith(_WARN_GLYPH)
            for p in row[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)  # 紧凑版式；不设 font.name，随模板正文字体（Codex #1）
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
        run_d.font.size = Pt(9)  # 不设 font.name，随模板正文字体（Codex #1）
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
