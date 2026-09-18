"""Unified Word document IR for classification, preview and relation extraction.

The parser keeps a compact semantic view of a DOCX: inferred heading hierarchy,
section membership, normalized table headers, and block-level provenance.  The
heading inference function is also consumed by document_annotator so a paragraph
cannot be a heading in the preview while remaining invisible to extraction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypeAlias

from app.services.extraction.text_scanner import (
    chinese_heading_prefix,
    compact_space,
    first_ascii_number,
    key_value_spans,
    normalize_space,
    numbered_heading_depth,
)

_PT = 12700  # one point in EMU
_PROSE_ENDINGS = "。；;，,！？!?"
PARSER_VERSION = 4


@dataclass
class DocSection:
    heading: str
    level: int
    paras: list[str] = field(default_factory=list)
    heading_index: int | None = None
    para_indices: list[int] = field(default_factory=list)


@dataclass
class SourceRange:
    anchor_block_id: str | None = None
    start_block_id: str | None = None
    end_block_id: str | None = None
    heading_index: int | None = None


@dataclass
class LayerMetadata:
    content_summary: str | None = None
    summary_scope: Literal["subtree"] = "subtree"
    summary_status: Literal[
        "pending", "completed", "partial", "disabled", "failed"
    ] = "pending"
    summary_source: Literal[
        "llm", "extractive_fallback", "empty", "none"
    ] = "none"
    summary_model: str | None = None
    prompt_version: str = "word-tree-summary-v1"
    content_hash: str = ""
    generated_at: str | None = None
    direct_paragraph_count: int = 0
    direct_table_count: int = 0
    descendant_section_count: int = 0
    leaf_count: int = 0
    page_count: int = 0


@dataclass
class PageMetadata:
    content_summary: str | None = None
    summary_scope: Literal["page_segment"] = "page_segment"
    summary_status: Literal["pending", "completed", "disabled", "failed"] = "pending"
    summary_source: Literal[
        "llm", "extractive_fallback", "empty", "none"
    ] = "none"
    summary_model: str | None = None
    prompt_version: str = "word-tree-summary-v1"
    content_hash: str = ""
    generated_at: str | None = None
    paragraph_count: int = 0
    table_count: int = 0
    character_count: int = 0


@dataclass
class PaginationMetadata:
    mode: Literal[
        "rendered_markers", "explicit_markers", "single_page_fallback"
    ] = "single_page_fallback"
    physical_page_numbers_available: bool = False
    is_estimated: bool = True
    warning: str | None = (
        "Word 未保存可靠分页标记；章节页为逻辑片段，不代表物理页码。"
    )


@dataclass
class ParagraphBlock:
    block_id: str
    paragraph_index: int
    fragment_index: int
    text: str
    style_name: str | None
    heading_level: int
    section_node_id: str | None
    physical_page_number: int | None
    body_index: int
    block_type: Literal["paragraph"] = "paragraph"


@dataclass
class TableBlock:
    block_id: str
    table_index: int
    section_node_id: str | None
    physical_page_number: int | None
    body_index: int
    block_type: Literal["table"] = "table"


@dataclass
class PageBreakBlock:
    block_id: str
    break_source: Literal["manual", "pageBreakBefore", "section", "lastRendered"]
    physical_page_number_after: int | None
    body_index: int
    block_type: Literal["page_break"] = "page_break"


WordBlock: TypeAlias = ParagraphBlock | TableBlock | PageBreakBlock


@dataclass
class PageNode:
    node_id: str
    ordinal_in_leaf: int
    physical_page_number: int | None
    break_source: str | None
    block_ids: list[str] = field(default_factory=list)
    paragraph_indices: list[int] = field(default_factory=list)
    table_indices: list[int] = field(default_factory=list)
    source_range: SourceRange = field(default_factory=SourceRange)
    page_metadata: PageMetadata = field(default_factory=PageMetadata)
    node_type: Literal["page"] = "page"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChapterNode:
    node_id: str
    node_type: Literal["document", "section"]
    heading: str
    level: int
    path: list[str] = field(default_factory=list)
    heading_index: int | None = None
    direct_block_ids: list[str] = field(default_factory=list)
    paragraph_indices: list[int] = field(default_factory=list)
    table_indices: list[int] = field(default_factory=list)
    source_range: SourceRange = field(default_factory=SourceRange)
    layer_metadata: LayerMetadata = field(default_factory=LayerMetadata)
    pages: list[PageNode] = field(default_factory=list)
    children: list["ChapterNode"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "heading": self.heading,
            "level": self.level,
            "path": self.path,
            "heading_index": self.heading_index,
            "is_leaf": self.is_leaf,
            "direct_block_ids": self.direct_block_ids,
            "paragraph_indices": self.paragraph_indices,
            "table_indices": self.table_indices,
            "source_range": asdict(self.source_range),
            "layer_metadata": asdict(self.layer_metadata),
            "pages": [page.to_dict() for page in self.pages],
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class DocTable:
    headers: list[str]
    rows: list[dict[str, str]]
    cells: list[list[str]]
    table_index: int = 0
    header_row_count: int = 1
    row_indices: list[int] = field(default_factory=list)
    # Nearest enclosing Word heading at the table's block position.  Keeping this
    # provenance prevents a semantically unrelated table elsewhere in the document
    # (for example a yield summary) from being mistaken for a process-detail table.
    section_heading: str | None = None
    heading_index: int | None = None
    section_path: list[str] = field(default_factory=list)
    table_path: list[str] = field(default_factory=list)
    # Physical XML cells, not the expanded row.cells visual grid. Continuations
    # point to the original source cell and do not duplicate its text.
    source_cells: list[dict] = field(default_factory=list)
    grid: list[list[str | None]] = field(default_factory=list)

    @property
    def ncols(self) -> int:
        return len(self.headers)

    @property
    def header_sig(self) -> str:
        return " | ".join(self.headers)

    def has_headers(self, *needles: str) -> bool:
        sig = self.header_sig
        return all(n in sig for n in needles)


@dataclass
class DocStructure:
    title: str
    sections: list[DocSection]
    tables: list[DocTable]
    paragraphs: list[str]
    headings: list[str]
    source_filename: str | None = None
    warnings: list[str] = field(default_factory=list)
    blocks: list[WordBlock] = field(default_factory=list)
    section_tree: ChapterNode | None = None
    pagination: PaginationMetadata = field(default_factory=PaginationMetadata)
    parser_version: int = PARSER_VERSION

    def find_section(self, *needles: str) -> DocSection | None:
        for sec in self.sections:
            if any(n in sec.heading for n in needles):
                return sec
        return None

    def find_table(self, *needles: str) -> DocTable | None:
        for tbl in self.tables:
            if tbl.has_headers(*needles):
                return tbl
        return None


def heading_level_from_style(style_name: str | None) -> int:
    """Map built-in/localized Heading and TOC style names to levels 1..6."""
    if not style_name:
        return 0
    text = style_name.strip()
    low = normalize_space(text.lower())
    if low == "title" or text == "标题":
        return 1
    if low == "subtitle" or text == "副标题":
        return 2
    if "heading" in low or text.startswith("标题"):
        number = first_ascii_number(text)
        if number:
            return max(1, min(6, int(number)))
    for prefix in ("toc", "目录"):
        if low.startswith(prefix):
            suffix = low[len(prefix):].strip()
            if suffix in ("1", "2", "3", "4", "5", "6"):
                return int(suffix)
    return 0


def _outline_heading_level(paragraph) -> int:
    try:
        from docx.oxml.ns import qn

        ppr = paragraph._p.pPr
        if ppr is None:
            return 0
        node = ppr.find(qn("w:outlineLvl"))
        if node is None:
            return 0
        raw = node.get(qn("w:val"))
        return max(1, min(6, int(raw) + 1)) if raw is not None else 0
    except (AttributeError, TypeError, ValueError):
        return 0


def _paragraph_font_size(paragraph) -> int | None:
    for run in paragraph.runs:
        if (run.text or "").strip() and run.font.size:
            return run.font.size
    try:
        if paragraph.style and paragraph.style.font.size:
            return paragraph.style.font.size
    except AttributeError:
        pass
    return None


def _font_size_heading_level(font_size_emu: int | None) -> int:
    if not font_size_emu:
        return 0
    pt = font_size_emu / _PT
    if pt >= 20:
        return 1
    if pt >= 16:
        return 2
    if pt >= 14:
        return 3
    return 0


def _predominantly_bold(paragraph) -> bool:
    weighted = 0
    bold_weighted = 0
    style_bold = bool(getattr(getattr(paragraph, "style", None), "font", None)
                      and paragraph.style.font.bold)
    for run in paragraph.runs:
        text = (run.text or "").strip()
        if not text:
            continue
        weight = len(text)
        weighted += weight
        if run.bold is True or (run.bold is None and style_bold):
            bold_weighted += weight
    return weighted > 0 and bold_weighted / weighted >= 0.8


def _semantic_bold_heading_level(paragraph, text: str) -> int:
    compact = compact_space(text)
    if not compact or len(compact) > 60 or compact.endswith(tuple(_PROSE_ENDINGS)):
        return 0
    if not _predominantly_bold(paragraph):
        return 0
    numbered = numbered_heading_depth(compact)
    if numbered:
        return numbered
    if chinese_heading_prefix(compact):
        return 2
    # Enterprise reports often use Normal + bold for unnumbered semantic titles.
    return 2


def infer_heading_level(paragraph) -> int:
    """Infer one heading level using shared, deterministic Word semantics."""
    text = (paragraph.text or "").strip()
    if not text:
        return 0
    # Direct paragraph outline metadata is more specific than its named style.
    # Enterprise templates commonly reuse ``toc 2`` while assigning different
    # outline levels to a parent section and its child headings.
    outline_level = _outline_heading_level(paragraph)
    if outline_level:
        return outline_level
    style_level = heading_level_from_style(
        paragraph.style.name if paragraph.style else None
    )
    if style_level:
        return style_level
    # Field paragraphs are document data even when their label is bold.  Some
    # enterprise templates put the value in the following paragraph/table, so an
    # empty ``label:`` must not become a visual heading either.  Keep numbered
    # headings such as ``2. 产品信息：`` eligible for the semantic-heading fallback;
    # explicit Word outline/style headings have already returned above.
    kv = key_value_spans(text)
    if kv:
        has_value = kv[1][1] > kv[1][0]
        numbered = numbered_heading_depth(text) or chinese_heading_prefix(text)
        if has_value or not numbered:
            return 0
    size_level = _font_size_heading_level(_paragraph_font_size(paragraph))
    if size_level:
        return size_level
    return _semantic_bold_heading_level(paragraph, text)


def _cell_text(cell) -> str:
    return " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip()).strip()


def _row_has_horizontal_merge(row) -> bool:
    try:
        from docx.oxml.ns import qn

        for cell in row._tr.iterchildren(qn("w:tc")):
            props = cell.find(qn("w:tcPr"))
            if props is None:
                continue
            span = props.find(qn("w:gridSpan"))
            if span is None:
                continue
            raw = span.get(qn("w:val"))
            if raw is not None and int(raw) > 1:
                return True
    except (AttributeError, TypeError, ValueError):
        return False
    return False


def _detect_header_rows(table) -> int:
    if not table.rows:
        return 0
    if not _row_has_horizontal_merge(table.rows[0]):
        return 1
    count = 1
    for row in table.rows[1:]:
        count += 1
        if not _row_has_horizontal_merge(row):
            break
    return count


def _canonical_headers(cells: list[list[str]], header_count: int) -> list[str]:
    width = max((len(row) for row in cells), default=0)
    headers: list[str] = []
    seen: dict[str, int] = {}
    for column in range(width):
        parts: list[str] = []
        for row in cells[:header_count]:
            value = row[column].strip() if column < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        base = " / ".join(parts) or f"col{column}"
        occurrence = seen.get(base, 0)
        seen[base] = occurrence + 1
        headers.append(base if occurrence == 0 else f"{base} #{occurrence + 1}")
    return headers


def _table_to_struct(
    table,
    table_index: int = 0,
    *,
    section_heading: str | None = None,
    heading_index: int | None = None,
    section_path: list[str] | None = None,
    table_path: list[str] | None = None,
) -> DocTable:
    table_path = table_path or [f"table:{table_index}"]
    source_cells, grid = _source_table_cells(table, table_path)
    cells = [[_cell_text(cell) for cell in row.cells] for row in table.rows]
    header_count = _detect_header_rows(table)
    headers = _canonical_headers(cells, header_count)
    rows: list[dict[str, str]] = []
    row_indices: list[int] = []
    for raw_index, raw in enumerate(cells[header_count:], start=header_count):
        row: dict[str, str] = {}
        for column, header in enumerate(headers):
            value = raw[column] if column < len(raw) else ""
            if header not in row or not row[header]:
                row[header] = value
        rows.append(row)
        row_indices.append(raw_index)
    return DocTable(
        headers=headers,
        rows=rows,
        cells=cells,
        table_index=table_index,
        header_row_count=header_count,
        row_indices=row_indices,
        section_heading=section_heading,
        heading_index=heading_index,
        section_path=section_path or [],
        table_path=table_path,
        source_cells=source_cells,
        grid=grid,
    )


def _source_table_cells(table, table_path: list[str]) -> tuple[list[dict], list[list]]:
    """Read each physical source once, keeping nested block order and merge origins."""
    from docx.oxml.ns import qn
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    cells: list[dict] = []
    grid: list[list] = []
    active: dict[int, dict] = {}
    for row_index, tr in enumerate(table._tbl.tr_lst):
        before = tr.find("w:trPr/w:gridBefore", tr.nsmap)
        column = int(before.get(qn("w:val"), "0")) if before is not None else 0
        row: list = [None] * column
        next_active: dict[int, dict] = {}
        for tc in tr.tc_lst:
            span = tc.grid_span
            continuation = tc.vMerge == "continue"
            origin = active.get(column) if continuation else None
            if continuation and origin is None:
                raise ValueError("vertical merge continuation has no source cell")
            if origin is not None:
                if origin["column_span"] != span:
                    raise ValueError("vertical merge continuation changes its column span")
                origin["row_span"] += 1
            else:
                cell_id = "/".join([*table_path, f"cell:{row_index}:{column}"])
                origin = {
                    "cell_id": cell_id, "row_index": row_index, "column_index": column,
                    "row_span": 1, "column_span": span, "blocks": [],
                }
                cell = _Cell(tc, table)
                paragraph_index, nested_index = 0, 0
                for child in tc:
                    if child.tag == qn("w:p"):
                        origin["blocks"].append({
                            "kind": "paragraph", "paragraph_index": paragraph_index,
                            "text": Paragraph(child, cell).text,
                        })
                        paragraph_index += 1
                    elif child.tag == qn("w:tbl"):
                        nested_path = [*table_path, f"cell:{row_index}:{column}",
                                       f"table:{nested_index}"]
                        nested = _table_to_struct(Table(child, cell), table_path=nested_path)
                        origin["blocks"].append({"kind": "table", "table": asdict(nested)})
                        nested_index += 1
                cells.append(origin)
            row.extend([origin["cell_id"]] * span)
            if tc.vMerge is not None:
                for col in range(column, column + span):
                    next_active[col] = origin
            column += span
        grid.append(row)
        active = next_active
    return cells, grid


def _filename_stem(source_filename: str | None, fallback: Path) -> str:
    if source_filename:
        return Path(source_filename).stem
    return fallback.stem


def _is_in_revision(element) -> bool:
    """Return whether an OOXML element is inside deleted/moved-from content."""
    try:
        from docx.oxml.ns import qn

        ignored = {qn("w:del"), qn("w:moveFrom")}
        parent = element.getparent()
        while parent is not None:
            if parent.tag in ignored:
                return True
            parent = parent.getparent()
    except AttributeError:
        return False
    return False


def effective_page_break_before(paragraph) -> bool:
    """Resolve direct/style-inherited Word ``pageBreakBefore``."""
    value = paragraph.paragraph_format.page_break_before
    if value is not None:
        return bool(value)
    style = paragraph.style
    while style is not None:
        paragraph_format = getattr(style, "paragraph_format", None)
        if paragraph_format is not None:
            value = paragraph_format.page_break_before
            if value is not None:
                return bool(value)
        style = getattr(style, "base_style", None)
    return False


def scan_paragraph_breaks(paragraph) -> tuple[bool, list[dict], str | None]:
    """Read all deterministic page events from one top-level paragraph."""
    from docx.oxml.ns import qn

    inline_breaks: list[dict] = []
    cursor = 0
    for run_element in paragraph._element.iter(qn("w:r")):
        if _is_in_revision(run_element):
            continue
        for child in run_element:
            if child.tag == qn("w:t"):
                cursor += len(child.text or "")
            elif child.tag == qn("w:br"):
                if child.get(qn("w:type")) == "page":
                    inline_breaks.append({"offset": cursor, "source": "manual"})
                else:
                    # python-docx exposes ordinary line breaks as one ``\n``
                    # character in paragraph.text; keep later page offsets aligned.
                    cursor += 1
            elif child.tag == qn("w:lastRenderedPageBreak"):
                inline_breaks.append({"offset": cursor, "source": "lastRendered"})
            elif child.tag in {qn("w:tab"), qn("w:cr")}:
                cursor += 1

    section_type: str | None = None
    paragraph_properties = paragraph._element.find(qn("w:pPr"))
    if paragraph_properties is not None:
        section = paragraph_properties.find(qn("w:sectPr"))
        if section is not None:
            type_element = section.find(qn("w:type"))
            section_type = (
                type_element.get(qn("w:val"))
                if type_element is not None
                else "nextPage"
            )
    return effective_page_break_before(paragraph), inline_breaks, section_type


def _split_text_at_breaks(text: str, offsets: list[int]) -> list[str]:
    # Preserve duplicate offsets: two consecutive page events create an empty
    # fragment between them and must not collapse into one event.
    boundaries = [0] + sorted(max(0, min(len(text), offset)) for offset in offsets)
    boundaries.append(len(text))
    return [text[boundaries[i]:boundaries[i + 1]] for i in range(len(boundaries) - 1)]


def _table_has_leading_page_break(table) -> bool:
    if not table.rows:
        return False
    from docx.oxml.ns import qn

    for cell_element in table.rows[0]._tr.iterchildren(qn("w:tc")):
        for break_element in cell_element.iter(qn("w:br")):
            if (
                not _is_in_revision(break_element)
                and break_element.get(qn("w:type")) == "page"
            ):
                return True
    return False


def build_chapter_tree(
    title: str,
    sections: list[DocSection],
) -> tuple[ChapterNode, dict[int, ChapterNode]]:
    """Build a deterministic explicit tree from flat section level + order."""
    root = ChapterNode(
        node_id="document",
        node_type="document",
        heading=title,
        level=0,
    )
    stack = [root]
    by_heading_index: dict[int, ChapterNode] = {}
    fallback_index = 0
    for section in sections:
        if section.level <= 0:
            continue
        while len(stack) > 1 and stack[-1].level >= section.level:
            stack.pop()
        parent = stack[-1]
        heading_index = section.heading_index
        suffix: int | str
        if heading_index is None:
            fallback_index += 1
            suffix = f"legacy-{fallback_index}"
        else:
            suffix = heading_index
        node = ChapterNode(
            node_id=f"section:{suffix}",
            node_type="section",
            heading=section.heading,
            level=section.level,
            path=[*parent.path, section.heading],
            heading_index=heading_index,
            source_range=SourceRange(heading_index=heading_index),
        )
        parent.children.append(node)
        stack.append(node)
        if heading_index is not None:
            by_heading_index[heading_index] = node
    return root, by_heading_index


def _block_material(block: WordBlock, tables: list[DocTable]) -> str:
    if isinstance(block, ParagraphBlock):
        return block.text.strip()
    if isinstance(block, TableBlock) and block.table_index < len(tables):
        return "\n".join(
            " | ".join(cell.strip() for cell in row)
            for row in tables[block.table_index].cells
        )
    return ""


def _digest(parts: list[str]) -> str:
    material = "\n".join([
        f"parser-version:{PARSER_VERSION}",
        *(part for part in parts if part),
    ])
    return sha256(material.encode("utf-8")).hexdigest()


def _iter_chapters(root: ChapterNode) -> list[ChapterNode]:
    result: list[ChapterNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return result


def _attach_page_nodes(
    root: ChapterNode,
    blocks: list[WordBlock],
    tables: list[DocTable],
    pagination: PaginationMetadata,
) -> None:
    block_by_id = {block.block_id: block for block in blocks}
    break_source_by_page = {
        block.physical_page_number_after: block.break_source
        for block in blocks
        if isinstance(block, PageBreakBlock)
        and block.physical_page_number_after is not None
    }
    owners = [node for node in _iter_chapters(root) if node.is_leaf]
    if root.children:
        owners = [node for node in owners if node.node_type == "section"]

    for owner in owners:
        content_blocks = [
            block_by_id[block_id]
            for block_id in owner.direct_block_ids
            if block_id in block_by_id
            and not isinstance(block_by_id[block_id], PageBreakBlock)
        ]
        groups: list[tuple[int | None, list[WordBlock]]] = []
        if pagination.mode == "single_page_fallback":
            groups = [(None, content_blocks)]
        else:
            for block in content_blocks:
                page_number = getattr(block, "physical_page_number", None)
                if not groups or groups[-1][0] != page_number:
                    groups.append((page_number, [block]))
                else:
                    groups[-1][1].append(block)
        if not groups:
            groups = [(None, [])]

        for ordinal, (page_number, page_blocks) in enumerate(groups, start=1):
            block_ids = [block.block_id for block in page_blocks]
            paragraph_indices = list(dict.fromkeys(
                block.paragraph_index
                for block in page_blocks
                if isinstance(block, ParagraphBlock)
            ))
            table_indices = list(dict.fromkeys(
                block.table_index
                for block in page_blocks
                if isinstance(block, TableBlock)
            ))
            materials = [_block_material(block, tables) for block in page_blocks]
            character_count = sum(len(material) for material in materials)
            metadata = PageMetadata(
                content_hash=_digest(materials),
                paragraph_count=len(paragraph_indices),
                table_count=len(table_indices),
                character_count=character_count,
            )
            if not character_count and not table_indices:
                metadata.summary_status = "completed"
                metadata.summary_source = "empty"
            source_range = SourceRange(
                anchor_block_id=block_ids[0] if block_ids else None,
                start_block_id=block_ids[0] if block_ids else None,
                end_block_id=block_ids[-1] if block_ids else None,
                heading_index=owner.heading_index,
            )
            owner.pages.append(PageNode(
                node_id=f"{owner.node_id}:page:{ordinal}",
                ordinal_in_leaf=ordinal,
                physical_page_number=page_number,
                break_source=break_source_by_page.get(page_number),
                block_ids=block_ids,
                paragraph_indices=paragraph_indices,
                table_indices=table_indices,
                source_range=source_range,
                page_metadata=metadata,
            ))


def _finalize_tree_metadata(
    node: ChapterNode,
    blocks: list[WordBlock],
    tables: list[DocTable],
) -> list[str]:
    block_by_id = {block.block_id: block for block in blocks}
    order = {block.block_id: index for index, block in enumerate(blocks)}
    subtree_ids = list(node.direct_block_ids)
    descendant_count = 0
    leaf_count = 1 if node.is_leaf else 0
    page_count = len(node.pages)
    child_hashes: list[str] = []
    for child in node.children:
        subtree_ids.extend(_finalize_tree_metadata(child, blocks, tables))
        descendant_count += 1 + child.layer_metadata.descendant_section_count
        leaf_count += child.layer_metadata.leaf_count
        page_count += child.layer_metadata.page_count
        child_hashes.append(child.layer_metadata.content_hash)

    subtree_ids = sorted(set(subtree_ids), key=lambda value: order.get(value, 10**9))
    anchor = (
        f"paragraph:{node.heading_index}:0"
        if node.heading_index is not None
        else None
    )
    node.source_range = SourceRange(
        anchor_block_id=anchor if anchor in block_by_id else None,
        start_block_id=subtree_ids[0] if subtree_ids else None,
        end_block_id=subtree_ids[-1] if subtree_ids else None,
        heading_index=node.heading_index,
    )
    direct_blocks = [
        block_by_id[block_id]
        for block_id in node.direct_block_ids
        if block_id in block_by_id
    ]
    direct_material = [_block_material(block, tables) for block in direct_blocks]
    node.layer_metadata.direct_paragraph_count = sum(
        1
        for block in direct_blocks
        if isinstance(block, ParagraphBlock)
        and block.heading_level == 0
        and bool(block.text.strip())
    )
    node.layer_metadata.direct_table_count = sum(
        isinstance(block, TableBlock) for block in direct_blocks
    )
    node.layer_metadata.descendant_section_count = descendant_count
    node.layer_metadata.leaf_count = leaf_count
    node.layer_metadata.page_count = page_count
    node.layer_metadata.content_hash = _digest([*direct_material, *child_hashes])
    if not any(direct_material) and not child_hashes:
        node.layer_metadata.summary_status = "completed"
        node.layer_metadata.summary_source = "empty"
    return subtree_ids


def parse_docx_structure(
    file_path: str | Path,
    source_filename: str | None = None,
) -> DocStructure:
    """Parse DOCX once into flat compatibility views and the canonical tree IR."""
    path = Path(file_path)
    fallback_title = _filename_stem(source_filename, path)
    empty_root = ChapterNode(
        node_id="document",
        node_type="document",
        heading=fallback_title,
        level=0,
    )
    try:
        from app.services.extraction.docx_reader import load_docx_for_reading
    except Exception:
        return DocStructure(
            fallback_title, [], [], [], [], source_filename,
            ["python-docx unavailable"],
            section_tree=empty_root,
        )

    try:
        doc = load_docx_for_reading(path)
    except Exception as exc:
        return DocStructure(
            fallback_title, [], [], [], [], source_filename,
            [f"DOCX parse failed: {type(exc).__name__}: {exc}"],
            section_tree=empty_root,
        )

    # Compatibility view: retain the exact non-empty paragraph ordering and the
    # existing deterministic heading inference.
    sections: list[DocSection] = []
    paragraphs: list[str] = []
    headings: list[str] = []
    first_level_one = ""
    first_visual_heading = ""

    current = DocSection(heading="", level=0)
    for paragraph_index, paragraph in enumerate(doc.paragraphs):
        text = (paragraph.text or "").strip()
        if not text:
            continue
        level = infer_heading_level(paragraph)
        paragraphs.append(text)
        if level > 0:
            headings.append(text)
            if not first_visual_heading:
                first_visual_heading = text
            if level == 1 and not first_level_one:
                first_level_one = text
            if current.heading or current.paras:
                sections.append(current)
            current = DocSection(
                heading=text,
                level=level,
                heading_index=paragraph_index,
            )
        else:
            current.paras.append(text)
            current.para_indices.append(paragraph_index)
    if current.heading or current.paras:
        sections.append(current)

    title = first_level_one or first_visual_heading or fallback_title
    root, node_by_heading_index = build_chapter_tree(title, sections)

    # Canonical body walk.  Paragraph/table membership, page events and stable
    # coordinates all come from this one order-preserving pass.
    paragraph_by_element = {paragraph._element: paragraph for paragraph in doc.paragraphs}
    paragraph_index_by_element = {
        paragraph._element: index for index, paragraph in enumerate(doc.paragraphs)
    }
    table_by_element = {
        table._element: (table, index) for index, table in enumerate(doc.tables)
    }
    tables: list[DocTable] = []
    blocks: list[WordBlock] = []
    active_node = root
    current_page = 1
    page_has_content = False
    saw_marker = False
    saw_last_rendered = False

    def add_page_event(
        source: Literal["manual", "pageBreakBefore", "section", "lastRendered"],
        body_index: int,
        event_ordinal: int,
        *,
        ensure_start: bool = False,
    ) -> None:
        nonlocal current_page, page_has_content, saw_marker, saw_last_rendered
        saw_marker = True
        saw_last_rendered = saw_last_rendered or source == "lastRendered"
        if not ensure_start or page_has_content:
            current_page += 1
            page_has_content = False
        blocks.append(PageBreakBlock(
            block_id=f"page-break:{body_index}:{event_ordinal}",
            break_source=source,
            physical_page_number_after=current_page,
            body_index=body_index,
        ))

    for body_index, child in enumerate(doc.element.body):
        paragraph = paragraph_by_element.get(child)
        if paragraph is not None:
            paragraph_index = paragraph_index_by_element[child]
            raw_text = paragraph.text or ""
            stripped_text = raw_text.strip()
            level = infer_heading_level(paragraph) if stripped_text else 0
            if level and paragraph_index in node_by_heading_index:
                active_node = node_by_heading_index[paragraph_index]

            before, inline_breaks, section_type = scan_paragraph_breaks(paragraph)
            event_ordinal = 0
            if before:
                add_page_event(
                    "pageBreakBefore", body_index, event_ordinal, ensure_start=True
                )
                event_ordinal += 1

            sorted_events = sorted(inline_breaks, key=lambda event: event["offset"])
            offsets = [event["offset"] for event in sorted_events]
            fragments = _split_text_at_breaks(raw_text, offsets) if inline_breaks else [raw_text]
            for fragment_index, fragment in enumerate(fragments):
                if fragment_index > 0:
                    source = sorted_events[fragment_index - 1]["source"]
                    add_page_event(source, body_index, event_ordinal)
                    event_ordinal += 1
                # Match the preview contract: a break-only paragraph contributes
                # events, while an ordinary empty paragraph remains addressable.
                if not fragment.strip() and (inline_breaks or before):
                    continue
                block = ParagraphBlock(
                    block_id=f"paragraph:{paragraph_index}:{fragment_index}",
                    paragraph_index=paragraph_index,
                    fragment_index=fragment_index,
                    text=fragment,
                    style_name=(paragraph.style.name if paragraph.style else None),
                    heading_level=level,
                    section_node_id=active_node.node_id,
                    physical_page_number=current_page,
                    body_index=body_index,
                )
                blocks.append(block)
                active_node.direct_block_ids.append(block.block_id)
                if level == 0 and fragment.strip():
                    if paragraph_index not in active_node.paragraph_indices:
                        active_node.paragraph_indices.append(paragraph_index)
                page_has_content = page_has_content or bool(fragment.strip())

            if section_type in {"nextPage", "evenPage", "oddPage"}:
                add_page_event("section", body_index, event_ordinal)
        elif child in table_by_element:
            table, table_index = table_by_element[child]
            event_ordinal = 0
            if _table_has_leading_page_break(table):
                add_page_event("manual", body_index, event_ordinal)
            parsed_table = _table_to_struct(
                table,
                table_index,
                section_heading=(
                    active_node.heading if active_node.node_type == "section" else None
                ),
                heading_index=active_node.heading_index,
                section_path=list(active_node.path),
            )
            tables.append(parsed_table)
            block = TableBlock(
                block_id=f"table:{table_index}",
                table_index=table_index,
                section_node_id=active_node.node_id,
                physical_page_number=current_page,
                body_index=body_index,
            )
            blocks.append(block)
            active_node.direct_block_ids.append(block.block_id)
            active_node.table_indices.append(table_index)
            page_has_content = True

    if saw_marker:
        pagination = PaginationMetadata(
            mode="rendered_markers" if saw_last_rendered else "explicit_markers",
            physical_page_numbers_available=True,
            is_estimated=False,
            warning=None,
        )
    else:
        pagination = PaginationMetadata()
        for block in blocks:
            if isinstance(block, (ParagraphBlock, TableBlock)):
                block.physical_page_number = None

    _attach_page_nodes(root, blocks, tables, pagination)
    _finalize_tree_metadata(root, blocks, tables)
    return DocStructure(
        title=title,
        sections=sections,
        tables=tables,
        paragraphs=paragraphs,
        headings=headings,
        source_filename=source_filename,
        blocks=blocks,
        section_tree=root,
        pagination=pagination,
    )
