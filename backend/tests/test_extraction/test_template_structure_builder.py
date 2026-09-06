from docx import Document

from app.services.extraction.document_ir import build_document_ir
from app.services.extraction.docx_structure import parse_docx_structure
from app.services.extraction.template_structure_builder import (
    build_template_structure,
    origin_status,
)


def test_offline_skeleton_has_stable_origins_and_separate_label_value_anchors(tmp_path):
    doc = Document()
    doc.add_heading("产品", 1)
    doc.add_paragraph("  规格：250 mg ")
    doc.add_paragraph("备注：")
    path = tmp_path / "sample.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path), role="template_sample")
    first = build_template_structure(ir)
    assert first == build_template_structure(ir)
    section = first[0]
    assert section["title"] == "产品"
    field = section["groups"][0]["candidates"][0]
    assert field["label"] == "规格"
    assert ir.resolve(field["origin"]["label_anchor"]) == "规格"
    assert ir.resolve(field["origin"]["value_anchor"]) == "250 mg"
    empty = section["groups"][0]["candidates"][1]
    assert empty["origin"]["value_anchor"] is None
    assert origin_status(field["origin"], ir) == "valid"
    assert origin_status({**field["origin"], "document_hash": "0" * 64}, ir) == "invalid"


def test_table_label_and_value_in_different_cells_and_prose_have_origins(tmp_path):
    doc = Document()
    doc.add_heading("产品", 1)
    doc.add_paragraph("此段是待作者编写的叙述。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "字段", "取值"
    table.cell(1, 0).text, table.cell(1, 1).text = "规格", "250 mg"
    path = tmp_path / "fields.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path), role="template_sample")
    sections = build_template_structure(ir)
    fields = [c for s in sections for g in s["groups"] for c in g["candidates"]]
    field = next(c for c in fields if c["label"] == "规格")
    assert ir.resolve(field["origin"]["label_anchor"]) == "规格"
    assert ir.resolve(field["origin"]["value_anchor"]) == "250 mg"
    assert (
        field["origin"]["label_anchor"]["evidence_id"]
        != (field["origin"]["value_anchor"]["evidence_id"])
    )
    assert any(c["label"] == "段落行文" for c in fields)


def test_multicolumn_header_does_not_pair_first_two_columns_as_key_value(tmp_path):
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    for col, label in enumerate(("对象", "规格", "备注")):
        table.cell(0, col).text = label
    path = tmp_path / "columns.docx"
    doc.save(path)
    ir = build_document_ir(path, parse_docx_structure(path), role="template_sample")
    fields = [c for s in build_template_structure(ir) for g in s["groups"] for c in g["candidates"]]
    assert all(c["origin"]["value_anchor"] is None for c in fields)
