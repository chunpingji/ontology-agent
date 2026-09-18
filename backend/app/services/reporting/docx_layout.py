"""Freeze a sample's Word layout and fill it with authorized output AST content."""

from base64 import b64decode, b64encode
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml.etree import XMLSyntaxError

from app.services.reporting.output_ast import OutputNode, plain_text, walk
from app.services.reporting.template_v2 import ReportingError


def use_black_text(doc):
    """Override template text colors at export, including fields and all header variants."""
    for part in doc.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        # Named/conditional styles and numbering can otherwise restore theme colors.
        for color in root.iter(qn("w:color")):
            color.attrib.clear()
            color.set(qn("w:val"), "000000")
        # XML traversal includes nested tables, hyperlinks and text boxes. Setting
        # RGB removes theme overrides without changing font, size or emphasis.
        for element in root.iter(qn("w:r")):
            Run(element, part).font.color.rgb = RGBColor(0, 0, 0)


def _locate(doc, anchor):
    """Resolve the parser's physical paragraph/table coordinates, including nested tables."""
    container, table = doc, None
    for step in anchor.get("table_path") or []:
        kind, *indexes = step.split(":")
        if kind == "table":
            table = container.tables[int(indexes[0])]
        elif kind == "cell" and table is not None:
            container = table.cell(*map(int, indexes))
        else:
            raise ValueError("Invalid table path")
    if table is not None:
        container = table.cell(anchor["row_index"], anchor["column_index"])
    return container.paragraphs[anchor.get("paragraph_index") or 0], table


def _clear_paragraph(element):
    # Retain direct formatting as well as the named style; never copy sample prose,
    # fields, drawings, hyperlinks or annotations into the report body.
    properties = (element.xpath("./w:r[w:t]/w:rPr") or element.xpath("./w:r/w:rPr")
                  or element.xpath("./w:pPr/w:rPr"))
    run_properties = deepcopy(properties[0]) if properties else None
    for child in list(element):
        if child.tag != qn("w:pPr"):
            element.remove(child)
    if run_properties is not None:
        run = OxmlElement("w:r")
        run.append(run_properties)
        element.append(run)


def freeze_layout(row, template):
    """Capture formatting with the existing frozen source bundle, before queueing.

    Only the cleared layout is stored. Retries and signing do not read the mutable
    sample path, which can be replaced or removed after a report has been queued.
    """
    if row is None or not row.sample_docx_path:
        return None
    try:
        raw = Path(row.sample_docx_path).read_bytes()
        doc = Document(BytesIO(raw))
    except (OSError, ValueError, KeyError, BadZipFile, XMLSyntaxError) as exc:
        raise ReportingError(
            "TEMPLATE_LAYOUT_UNAVAILABLE", "模板 Word 样例不可用，请重新上传模板样例", status=409
        ) from exc
    digest, anchors, structural_ids = sha256(raw).hexdigest(), {}, []

    def visit(items, identity):
        for item in items:
            if identity != "output_id":
                structural_ids.append(item[identity])
            origin = item.get("origin") or {}
            anchor = origin.get("label_anchor")
            if anchor and origin.get("document_hash") == digest:
                try:
                    _locate(doc, anchor)
                except (IndexError, KeyError, TypeError, ValueError):
                    pass  # A manually edited output can still use the template's styles.
                else:
                    anchors[item[identity]] = anchor
            visit(item.get("groups", []), "group_id")
            visit(item.get("units", []), "output_id")

    visit(template["sections"], "section_id")
    for paragraph in doc.element.body.xpath(".//w:p"):
        _clear_paragraph(paragraph)
    # These parts belong to removed body content. Header/footer images have their
    # own relationships, so opening and saving the original package preserves them.
    for rel in list(doc.part.rels.values()):
        if rel.reltype.rsplit("/", 1)[-1] in {
            "image", "hyperlink", "oleObject", "package", "aFChunk", "comments",
            "footnotes", "endnotes", "customXml", "glossaryDocument",
        }:
            doc.part.drop_rel(rel.rId)
    stream = BytesIO()
    doc.save(stream)
    return {
        "docx": b64encode(stream.getvalue()).decode("ascii"),
        "sample_hash": digest,
        "anchors": anchors,
        "structural_ids": structural_ids,
        "custom_style": template.get("style") is not None,
    }


def _set_text(paragraph, text, style=None):
    properties = paragraph._p.xpath("./w:r/w:rPr") or paragraph._p.xpath("./w:pPr/w:rPr")
    run_properties = deepcopy(properties[0]) if properties else None
    paragraph.clear()
    run = paragraph.add_run(text)
    if run_properties is not None:
        run._r.insert(0, run_properties)
    if style:
        run.font.name = style["font"]
        run._r.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), style["font"])
        run.font.size = Pt(style["font_size_pt"])


def _paragraph_after(paragraph):
    element = deepcopy(paragraph._p)
    _clear_paragraph(element)
    # A generated continuation paragraph must not repeat a section break.
    for section in element.xpath("./w:pPr/w:sectPr"):
        section.getparent().remove(section)
    paragraph._p.addnext(element)
    return Paragraph(element, paragraph._parent)


def _fill_table(table, node, style):
    """Keep table/cell formatting, expanding only the generated row/column grid."""
    rows = node.children
    if not rows:
        return
    width = max(len(row.children) for row in rows)
    prototypes = [(deepcopy(row._tr.trPr), list(row.cells)) for row in table.rows]
    columns = list(table.columns)
    widths = [column.width or 1 for column in columns]
    total = sum(widths)
    weights = [widths[min(i, len(widths) - 1)] for i in range(width)]
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for weight in weights:
        grid.add_gridCol().w = round(total * weight / sum(weights))
    for row in list(table._tbl.tr_lst):
        table._tbl.remove(row)
    for index, row in enumerate(rows):
        row_properties, source_cells = prototypes[min(index, len(prototypes) - 1)]
        tr = OxmlElement("w:tr")
        if row_properties is not None:
            tr.append(deepcopy(row_properties))
        # Sample fixed heights can clip generated multi-line content.
        for height in tr.xpath("./w:trPr/w:trHeight"):
            height.set(qn("w:hRule"), "atLeast")
        for col in range(width):
            source = source_cells[min(col, len(source_cells) - 1)]
            cell = OxmlElement("w:tc")
            if source._tc.tcPr is not None:
                cell.append(deepcopy(source._tc.tcPr))
            for merge in cell.xpath("./w:tcPr/w:gridSpan | ./w:tcPr/w:vMerge"):
                merge.getparent().remove(merge)
            cell.get_or_add_tcPr().get_or_add_tcW().set(
                qn("w:w"), str(round(total * weights[col] / sum(weights) / 635))
            )
            element = deepcopy(source.paragraphs[0]._p)
            _clear_paragraph(element)
            cell.append(element)
            tr.append(cell)
            paragraph = Paragraph(element, table._parent)
            _set_text(paragraph, plain_text(row.children[col]) if col < len(row.children) else "",
                      style)
        table._tbl.append(tr)


def render_template_docx(ast, style, layout, layout_nodes):
    doc = Document(BytesIO(b64decode(layout["docx"])))
    ast = OutputNode.model_validate(ast)
    custom = style if layout.get("custom_style") else None
    # Resolve all coordinates before inserting paragraphs or expanding tables.
    targets = {key: _locate(doc, anchor) for key, anchor in layout["anchors"].items()}
    tables = list(doc.tables)
    # Notes anchored in a sample table's final row are separate outputs. Move
    # their formatting anchors just after the table before replacing record rows;
    # otherwise their old cells would be detached and the generated notes lost.
    outputs = set(layout_nodes.values())
    for node in walk(ast):
        output_id = layout_nodes.get(node.node_id)
        target = targets.get(output_id)
        if not target or not any(child.kind == "table" for child in node.children):
            continue
        _, table = target
        if table is None or layout["anchors"][output_id].get("row_index") != 0:
            continue
        relocated, last = {}, table._tbl
        for key, (p, _) in list(targets.items()):
            if key == output_id or key not in outputs or table._tbl not in p._p.iterancestors():
                continue
            if p._p not in relocated:
                element = deepcopy(p._p)
                _clear_paragraph(element)
                last.addnext(element)
                last = element
                relocated[p._p] = Paragraph(element, table._parent)
            targets[key] = (relocated[p._p], None)
    if not targets:
        # Templates authored without physical anchors use flow layout. Keep the
        # first section's page setup and its own header/footer relationships.
        section = deepcopy(doc.sections[0]._sectPr)
        doc.element.body.clear_content()
        doc.element.body.replace(doc.element.body.sectPr, section)

    def paragraph(text, after=None, heading=None):
        if after is not None:
            result = _paragraph_after(after)
        else:
            result = doc.add_paragraph()
            if heading and f"Heading {heading}" in doc.styles:
                result.style = f"Heading {heading}"
        _set_text(result, text, custom)
        return result

    def emit(node, cursor=None, first=False, table_target=None):
        if node.kind in {"section", "group"}:
            if node.text:
                if first and cursor is not None:
                    _set_text(cursor, node.text, custom)
                else:
                    cursor = paragraph(node.text, cursor, 1 if node.kind == "section" else 2)
            for child in node.children:
                cursor = emit(child, cursor, table_target=table_target)
                table_target = None
            return cursor
        if node.kind == "table":
            if node.text:
                cursor = paragraph(node.text, cursor)
            if not node.children:
                return cursor
            if table_target is not None:
                table = table_target
            else:
                if tables:
                    element = deepcopy(tables[0]._tbl)
                    table = Table(element, doc)
                    doc.element.body.insert_element_before(element, "w:sectPr")
                else:
                    table = doc.add_table(rows=1, cols=max(len(r.children) for r in node.children))
                    if "Table Grid" in doc.styles:
                        table.style = "Table Grid"
                if cursor is not None:
                    cursor._p.addnext(table._tbl)
            _fill_table(table, node, custom)
            # Further generated content goes after this table, within its parent cell.
            element = OxmlElement("w:p")
            table._tbl.addnext(element)
            return Paragraph(element, table._parent)
        if node.kind == "list":
            for child in node.children:
                cursor = paragraph(plain_text(child), cursor)
                name = "List Number" if node.ordered else "List Bullet"
                if name in doc.styles:
                    cursor.style = name
            return cursor
        if node.kind in {"document", "envelope"}:
            for child in node.children:
                cursor = emit(child, cursor)
            return cursor
        if first and cursor is not None:
            _set_text(cursor, plain_text(node), custom)
            return cursor
        return paragraph(plain_text(node), cursor)

    used, filled_tables = {}, {}

    def place(node):
        output_id = layout_nodes.get(node.node_id)
        target = targets.get(output_id or node.node_id)
        if output_id and target:
            cursor, table = target
            # A table output anchored to a table header replaces that table's rows.
            is_table = any(child.kind == "table" for child in node.children)
            anchor = layout["anchors"][output_id]
            if is_table and table is not None and anchor.get("row_index") == 0:
                original_table = table._tbl
                if original_table in filled_tables:
                    element = deepcopy(original_table)
                    filled_tables[original_table]._p.addnext(element)
                    table = Table(element, table._parent)
                element = deepcopy(cursor._p)
                _clear_paragraph(element)
                table._tbl.addprevious(element)
                cursor = Paragraph(element, table._parent)
                filled_tables[original_table] = emit(node, cursor, first=True, table_target=table)
            else:
                previous = used.get(cursor._p)
                used[cursor._p] = emit(node, previous or cursor, first=previous is None)
            return
        if node.kind in {"document", "envelope", "section", "group"} and not output_id:
            if node.text and (not targets or node.node_id not in layout["structural_ids"]):
                paragraph(node.text, heading=1 if node.kind == "section" else 2)
            elif target and node.text and node.kind == "section":
                _set_text(target[0], node.text, custom)
            for child in node.children:
                place(child)
            return
        if node.node_id == "demo-notice" and targets:
            p = doc.add_paragraph()
            _set_text(p, plain_text(node), custom)
            doc.element.body.insert(0, p._p)
        else:
            emit(node)

    place(ast)
    use_black_text(doc)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()
