"""annotate_word 保留 Word 文档样式（回归）。

标注预览此前把所有段落拍平成同款 paragraph，丢失标题层级 / 行内粗体 / 对齐 / 空行，
渲染结果与上传文件样式不一致。本测覆盖：heading 层级、对齐、空行间距、行内样式，
以及行内样式与 NER span 按字符边界的合并（``_inline_nodes``）。

以真实 python-docx 构造样本，桩掉 GLiNER（span 为空）→ 结构断言确定、纯本地。
"""

from __future__ import annotations

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from docx.shared import Pt

from app.services.extraction import document_annotator
from app.services.extraction.document_annotator import _inline_nodes, annotate_word


class _Module:
    def __init__(self, key):
        self.key = key


class _FakeEngine:
    """最小本体引擎桩：仅供 seed_labels 调用，无需真实类层级。"""

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


def test_annotate_word_preserves_structure(tmp_path, monkeypatch):
    """heading 层级 / 居中对齐 / 空行 / 行内粗体均被保留为对应 tiptap 节点。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_heading("产品质量标准", level=1)          # → heading level 1
    centered = d.add_paragraph("居中副标题")
    centered.alignment = WD_ALIGN_PARAGRAPH.CENTER  # → textAlign center
    d.add_paragraph("")                              # 空行 → 保留间距
    body = d.add_paragraph()
    body.add_run("关键指标").bold = True             # 粗体 run
    body.add_run("：常规说明")                        # 普通 run
    path = tmp_path / "spec.docx"
    d.save(path)

    doc, _, _, _ = annotate_word(path, _FakeEngine())
    content = doc["content"]

    # 标题段：heading level 1
    heading = content[0]
    assert heading["type"] == "heading"
    assert heading["attrs"]["level"] == 1
    assert heading["content"][0]["text"] == "产品质量标准"

    # 居中段：paragraph + textAlign=center
    centered_node = content[1]
    assert centered_node["type"] == "paragraph"
    assert centered_node["attrs"]["textAlign"] == "center"

    # 空行：空 paragraph（无 content）→ 渲染为空行，保留间距
    empty_node = content[2]
    assert empty_node["type"] == "paragraph"
    assert "content" not in empty_node

    # 正文段：粗体 run 携 bold mark，普通 run 无 mark
    body_node = content[3]
    bold_seg = body_node["content"][0]
    assert bold_seg["text"] == "关键指标"
    assert {"type": "bold"} in bold_seg["marks"]
    plain_seg = body_node["content"][1]
    assert plain_seg["text"] == "：常规说明"
    assert "marks" not in plain_seg


def test_inline_nodes_merges_formatting_and_entity():
    """行内样式与 NER span 按字符边界合并：交叠段同时带 bold + entity mark。"""
    text = "无菌粉针剂A"
    runs = [(0, 5, ["bold"]), (5, 6, [])]   # 前 5 字粗体，"A" 普通
    spans = [{
        "start": 0, "end": 4, "text": "无菌粉针",
        "label": "无菌粉针剂", "className": "无菌粉针剂", "score": 0.9,
    }]

    nodes = _inline_nodes(text, spans, runs)

    # 边界 {0,4,5,6} → 三段
    assert [n["text"] for n in nodes] == ["无菌粉针", "剂", "A"]
    # 段一：bold + entity（交叠）
    types0 = {m["type"] for m in nodes[0]["marks"]}
    assert types0 == {"bold", "entity-annotation"}
    # 段二：仅 bold（在粗体 run 内、span 外）
    assert [m["type"] for m in nodes[1]["marks"]] == ["bold"]
    # 段三："A" 无样式无标注
    assert "marks" not in nodes[2]


def test_font_size_heading_fallback(tmp_path, monkeypatch):
    """Normal 样式 + 大字号 → 字号启发式回退识别为 heading。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    # 18pt（小二）→ h2；Normal 样式不触发 _heading_level
    title_para = d.add_paragraph()
    run = title_para.add_run("文档标题")
    run.font.size = Pt(18)

    # 14pt（四号）→ h3
    sub_para = d.add_paragraph()
    run2 = sub_para.add_run("子标题")
    run2.font.size = Pt(14)

    # 12pt（小四）→ paragraph
    body = d.add_paragraph()
    run3 = body.add_run("正文内容")
    run3.font.size = Pt(12)

    path = tmp_path / "fontsize.docx"
    d.save(path)

    doc, _, _, _ = annotate_word(path, _FakeEngine())
    content = doc["content"]

    assert content[0]["type"] == "heading"
    assert content[0]["attrs"]["level"] == 2

    assert content[1]["type"] == "heading"
    assert content[1]["attrs"]["level"] == 3

    assert content[2]["type"] == "paragraph"


def test_inherited_bold_from_style(tmp_path, monkeypatch):
    """段落样式定义粗体 → run.bold=None 时应继承，标记为 bold mark。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    # 创建段落，在样式上设置粗体
    para = d.add_paragraph(style="Normal")
    para.style.font.bold = True
    para.add_run("继承粗体")
    # 第二个 run 显式取消粗体
    r2 = para.add_run("非粗体")
    r2.bold = False

    path = tmp_path / "inherited.docx"
    d.save(path)

    doc, _, _, _ = annotate_word(path, _FakeEngine())
    body = doc["content"][0]

    # 第一段文本：继承粗体
    assert {"type": "bold"} in body["content"][0].get("marks", [])
    # 第二段文本：显式取消粗体
    marks = body["content"][1].get("marks", [])
    assert {"type": "bold"} not in marks


def test_tables_interleaved_with_paragraphs(tmp_path, monkeypatch):
    """表格应保持在文档中的原始位置，而非被统一移到尾部。"""
    monkeypatch.setattr(document_annotator, "_get_extractor", lambda: None)

    d = docx.Document()
    d.add_paragraph("段落一")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "单元格A"
    t.cell(0, 1).text = "单元格B"
    d.add_paragraph("段落二")

    path = tmp_path / "interleaved.docx"
    d.save(path)

    doc, _, _, _ = annotate_word(path, _FakeEngine())
    types = [n["type"] for n in doc["content"]]

    assert types == ["paragraph", "table", "paragraph"]
