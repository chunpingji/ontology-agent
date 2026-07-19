"""Unified Word document IR for classification, preview and relation extraction.

The parser keeps a compact semantic view of a DOCX: inferred heading hierarchy,
section membership, normalized table headers, and block-level provenance.  The
heading inference function is also consumed by document_annotator so a paragraph
cannot be a heading in the preview while remaining invisible to extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_PT = 12700  # one point in EMU
_PROSE_ENDINGS = "。；;，,！？!?"
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<num>[0-9]+(?:[.．][0-9]+){0,5})[.．、\s]*"
)
_ZH_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:[（(]?[一二三四五六七八九十百]+[）)、.．]|"
    r"第[一二三四五六七八九十百0-9]+[章节部分])"
)


@dataclass
class DocSection:
    heading: str
    level: int
    paras: list[str] = field(default_factory=list)
    heading_index: int | None = None
    para_indices: list[int] = field(default_factory=list)


@dataclass
class DocTable:
    headers: list[str]
    rows: list[dict[str, str]]
    cells: list[list[str]]
    table_index: int = 0
    header_row_count: int = 1
    row_indices: list[int] = field(default_factory=list)

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
    low = re.sub(r"\s+", " ", text.lower())
    if low == "title" or text == "标题":
        return 1
    if low == "subtitle" or text == "副标题":
        return 2
    if "heading" in low or text.startswith("标题"):
        match = re.search(r"[0-9]+", text)
        if match:
            return max(1, min(6, int(match.group())))
    toc = re.fullmatch(r"(?:toc|目录)\s*([1-6])", low)
    if toc:
        return int(toc.group(1))
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
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 60 or compact.endswith(tuple(_PROSE_ENDINGS)):
        return 0
    if not _predominantly_bold(paragraph):
        return 0
    numbered = _NUMBERED_HEADING_RE.match(compact)
    if numbered:
        number = numbered.group("num").replace("．", ".")
        return max(1, min(6, len(number.split("."))))
    if _ZH_NUMBERED_HEADING_RE.match(compact):
        return 2
    # Enterprise reports often use Normal + bold for unnumbered semantic titles.
    return 2


def infer_heading_level(paragraph) -> int:
    """Infer one heading level using shared, deterministic Word semantics."""
    text = (paragraph.text or "").strip()
    if not text:
        return 0
    style_level = heading_level_from_style(
        paragraph.style.name if paragraph.style else None
    )
    if style_level:
        return style_level
    outline_level = _outline_heading_level(paragraph)
    if outline_level:
        return outline_level
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


def _table_to_struct(table, table_index: int = 0) -> DocTable:
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
    )


def _filename_stem(source_filename: str | None, fallback: Path) -> str:
    if source_filename:
        return Path(source_filename).stem
    return fallback.stem


def parse_docx_structure(
    file_path: str | Path,
    source_filename: str | None = None,
) -> DocStructure:
    """Parse DOCX into the shared semantic IR; failures return an empty structure."""
    path = Path(file_path)
    fallback_title = _filename_stem(source_filename, path)
    try:
        from docx import Document
    except Exception:
        return DocStructure(
            fallback_title, [], [], [], [], source_filename,
            ["python-docx unavailable"],
        )

    try:
        doc = Document(str(path))
    except Exception as exc:
        return DocStructure(
            fallback_title, [], [], [], [], source_filename,
            [f"DOCX parse failed: {type(exc).__name__}: {exc}"],
        )

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

    tables = [_table_to_struct(table, idx) for idx, table in enumerate(doc.tables)]
    title = first_level_one or first_visual_heading or fallback_title
    return DocStructure(
        title=title,
        sections=sections,
        tables=tables,
        paragraphs=paragraphs,
        headings=headings,
        source_filename=source_filename,
    )
