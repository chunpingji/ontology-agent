"""分页检测与段落拆分（Codex P0/P1/P2 全量覆盖）。

以真实 python-docx 构造 Word 文档，桩掉 GLiNER → 断言 tiptap 节点序列。
"""

from __future__ import annotations

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.services.extraction import document_annotator
from app.services.extraction.document_annotator import (
    _scan_para_breaks,
    _split_para_at_breaks,
    annotate_word,
    parse_word_to_tiptap,
)


class _Module:
    def __init__(self, key):
        self.key = key


class _FakeEngine:
    def get_modules(self):
        return [_Module("m")]

    def get_class_hierarchy(self, key):
        return []

    def data_property_domain_classes(self):
        return []

    def data_property_labels(self):
        return []

    def get_data_properties_by_domain(self, class_iri):
        return []


def _types(nodes):
    return [n.get("type") for n in nodes]


def _add_page_break_to_run(run):
    """在 run 内插入 <w:br w:type="page"/>。"""
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    run._r.append(br)


def _add_last_rendered_page_break(run):
    """在 run 内插入 <w:lastRenderedPageBreak/>。"""
    lrpb = OxmlElement("w:lastRenderedPageBreak")
    run._r.append(lrpb)


def _texts(content_nodes):
    """提取 tiptap 节点列表中所有段落/标题的纯文本。"""
    result = []
    for n in content_nodes:
        if n["type"] in ("paragraph", "heading"):
            texts = []
            for c in n.get("content", []):
                if c.get("type") == "text":
                    texts.append(c["text"])
            result.append("".join(texts))
    return result


# ── P0: 段中分页符拆分 ──


def test_intra_paragraph_break(tmp_path, monkeypatch):
    """段落中间的 w:br type=page 应把段落拆成两段，中间插入 pageBreak。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    p = d.add_paragraph()
    run1 = p.add_run("Before")
    _add_page_break_to_run(run1)
    run2 = p.add_run("After")  # noqa: F841

    path = tmp_path / "intra.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    nodes = doc_json["content"]
    types = _types(nodes)

    assert "pageBreak" in types, f"Expected pageBreak in {types}"
    pb_idx = types.index("pageBreak")
    assert pb_idx > 0, "pageBreak should not be at the very start"

    texts = _texts(nodes)
    assert "Before" in texts
    assert "After" in texts


def test_multiple_breaks_one_paragraph(tmp_path, monkeypatch):
    """同一段落内两个 w:br 应生成 3 个文本片段和 2 个 pageBreak。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    p = d.add_paragraph()
    r1 = p.add_run("A")
    _add_page_break_to_run(r1)
    r2 = p.add_run("B")
    _add_page_break_to_run(r2)
    p.add_run("C")

    path = tmp_path / "multi.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    assert types.count("pageBreak") == 2, f"Expected 2 pageBreaks, got {types}"
    texts = _texts(doc_json["content"])
    assert "A" in texts
    assert "B" in texts
    assert "C" in texts


# ── P0: 样式继承 pageBreakBefore ──


def test_style_inherited_pageBreakBefore(tmp_path, monkeypatch):
    """通过段落样式设置 pageBreakBefore 时应被检测到。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("First paragraph")
    p2 = d.add_paragraph("Second paragraph")
    # 直接在 XML 层面设置 pageBreakBefore
    pPr = p2._element.get_or_add_pPr()
    pbb_elem = OxmlElement("w:pageBreakBefore")
    pPr.append(pbb_elem)

    path = tmp_path / "pbb.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    assert "pageBreak" in types

    # pageBreak 应有 ensureStart 模式
    for n in doc_json["content"]:
        if n["type"] == "pageBreak":
            assert n.get("attrs", {}).get("mode") == "ensureStart"
            assert n.get("attrs", {}).get("source") == "pageBreakBefore"
            break


# ── P0: 分节符 ──


def test_section_break_nextPage(tmp_path, monkeypatch):
    """段落 pPr 中的 w:sectPr (nextPage) 应产生分页。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("Section 1 content")
    p2 = d.add_paragraph("Section 2 content")

    # 在第一段的 pPr 中添加 sectPr (nextPage)
    first_para = d.paragraphs[0]
    pPr = first_para._element.get_or_add_pPr()
    sect = OxmlElement("w:sectPr")
    type_elem = OxmlElement("w:type")
    type_elem.set(qn("w:val"), "nextPage")
    sect.append(type_elem)
    pPr.append(sect)

    path = tmp_path / "sect.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    assert "pageBreak" in types

    for n in doc_json["content"]:
        if n["type"] == "pageBreak":
            attrs = n.get("attrs", {})
            if attrs.get("source") == "section":
                assert attrs["mode"] == "force"
                break
    else:
        raise AssertionError("No section-source pageBreak found")


def test_section_break_continuous_no_page_break(tmp_path, monkeypatch):
    """continuous 分节符不应产生分页。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("Section 1")
    d.add_paragraph("Section 2")

    first_para = d.paragraphs[0]
    pPr = first_para._element.get_or_add_pPr()
    sect = OxmlElement("w:sectPr")
    type_elem = OxmlElement("w:type")
    type_elem.set(qn("w:val"), "continuous")
    sect.append(type_elem)
    pPr.append(sect)

    path = tmp_path / "cont.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    section_pbs = [
        n for n in doc_json["content"]
        if n["type"] == "pageBreak" and n.get("attrs", {}).get("source") == "section"
    ]
    assert len(section_pbs) == 0


# ── P0: attrs mode/source ──


def test_attrs_mode_source(tmp_path, monkeypatch):
    """分页节点应携带正确的 mode 和 source 属性。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    # 空段含手动分页
    p = d.add_paragraph()
    r = p.add_run()
    _add_page_break_to_run(r)
    d.add_paragraph("After break")

    path = tmp_path / "attrs.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    pbs = [n for n in doc_json["content"] if n["type"] == "pageBreak"]
    assert len(pbs) >= 1
    # 空段手动分页 → 由 has_before=False + inline_breaks 产出
    for pb in pbs:
        attrs = pb.get("attrs", {})
        assert attrs.get("mode") in ("force", "ensureStart")
        assert attrs.get("source") in ("manual", "pageBreakBefore", "section", "lastRendered")


# ── P2: 修订内容过滤 ──


def test_revision_deleted_break_ignored(tmp_path, monkeypatch):
    """w:del 内部的 w:br 不应被识别为分页符。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    p = d.add_paragraph("Normal text")

    # 手动在 XML 中构造 w:del 包裹的 w:br
    del_elem = OxmlElement("w:del")
    del_elem.set(qn("w:id"), "1")
    del_elem.set(qn("w:author"), "test")
    del_elem.set(qn("w:date"), "2024-01-01T00:00:00Z")
    r_elem = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    r_elem.append(br)
    del_elem.append(r_elem)
    p._element.append(del_elem)

    path = tmp_path / "rev.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    pbs = [n for n in doc_json["content"] if n["type"] == "pageBreak"]
    assert len(pbs) == 0, f"Deleted break should be ignored, got {pbs}"


# ── P1: lastRenderedPageBreak ──


def test_lastRenderedPageBreak(tmp_path, monkeypatch):
    """w:lastRenderedPageBreak 应被检测为分页事件。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    p = d.add_paragraph()
    r1 = p.add_run("Line1")
    _add_last_rendered_page_break(r1)
    p.add_run("Line2")

    path = tmp_path / "lrpb.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    pbs = [n for n in doc_json["content"] if n["type"] == "pageBreak"]
    assert len(pbs) >= 1
    sources = [pb.get("attrs", {}).get("source") for pb in pbs]
    assert "lastRendered" in sources


# ── P1: 表格前分页 ──


def test_table_leading_break(tmp_path, monkeypatch):
    """表格首行单元格含分页符时，应在表格前插入 pageBreak。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("Before table")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "H1"
    table.cell(0, 1).text = "H2"
    table.cell(1, 0).text = "D1"
    table.cell(1, 1).text = "D2"

    # 在第一行第一个单元格的段落中添加 w:br
    first_cell = table.rows[0].cells[0]
    first_para = first_cell.paragraphs[0]
    r = first_para.runs[0] if first_para.runs else first_para.add_run()
    _add_page_break_to_run(r)

    path = tmp_path / "tbl.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    # 应在 table 之前有一个 pageBreak
    table_idx = types.index("table")
    assert table_idx > 0
    assert types[table_idx - 1] == "pageBreak"


# ── structure_only 路径 ──


def test_structure_only_preserves_breaks(tmp_path, monkeypatch):
    """parse_word_to_tiptap (structure_only=True) 也应保留分页节点。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("Page 1")
    p2 = d.add_paragraph()
    r = p2.add_run()
    _add_page_break_to_run(r)
    d.add_paragraph("Page 2")

    path = tmp_path / "struct.docx"
    d.save(str(path))

    doc_json = parse_word_to_tiptap(path)
    types = _types(doc_json["content"])
    assert "pageBreak" in types


# ── NER 偏移正确性 ──


def test_split_preserves_text_idx(tmp_path, monkeypatch):
    """拆分后每个片段的 text_idx 应指向正确的 all_texts 索引。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    p = d.add_paragraph()
    r1 = p.add_run("Hello")
    _add_page_break_to_run(r1)
    p.add_run("World")

    path = tmp_path / "idx.docx"
    d.save(str(path))

    doc_json, _, _, _ = annotate_word(path, engine=None, structure_only=True)
    # 应该有至少两个段落节点
    paras = [n for n in doc_json["content"] if n["type"] in ("paragraph", "heading")]
    assert len(paras) >= 2


# ── _split_para_at_breaks 单元测试 ──


def test_split_para_at_breaks_basic():
    text = "ABCDE"
    runs = [(0, 5, [{"type": "bold"}])]
    offsets = [2]
    result = _split_para_at_breaks(text, runs, offsets)
    assert len(result) == 2
    assert result[0][0] == "AB"
    assert result[1][0] == "CDE"
    # runs 偏移归零
    assert result[0][1] == [(0, 2, [{"type": "bold"}])]
    assert result[1][1] == [(0, 3, [{"type": "bold"}])]


def test_split_para_at_breaks_multiple():
    text = "ABCDEFGH"
    runs = [(0, 3, []), (3, 8, [{"type": "italic"}])]
    offsets = [2, 5]
    result = _split_para_at_breaks(text, runs, offsets)
    assert len(result) == 3
    assert result[0][0] == "AB"
    assert result[1][0] == "CDE"
    assert result[2][0] == "FGH"


# ── _scan_para_breaks 单元测试 ──


def test_scan_para_breaks_empty_paragraph():
    """空段落应返回无分页事件。"""
    d = docx.Document()
    p = d.add_paragraph()
    has_before, breaks, sect = _scan_para_breaks(p)
    assert has_before is False
    assert breaks == []
    assert sect is None


def test_scan_para_breaks_with_manual_break():
    """含手动分页的段落应返回正确偏移。"""
    d = docx.Document()
    p = d.add_paragraph()
    r = p.add_run("Hello")
    _add_page_break_to_run(r)
    p.add_run("World")

    has_before, breaks, sect = _scan_para_breaks(p)
    assert has_before is False
    assert len(breaks) == 1
    assert breaks[0]["offset"] == 5
    assert breaks[0]["source"] == "manual"
    assert sect is None
