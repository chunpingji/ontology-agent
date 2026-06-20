"""Document parsers for Excel and Word files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_excel(
    file_path: Path,
    column_mapping: dict[str, str],
    sheet_name: str | None = None,
    header_row: int = 1,
) -> list[dict[str, Any]]:
    """Parse Excel file and map columns to ontology property IRIs.

    Args:
        file_path: Path to .xlsx file
        column_mapping: {column_header: property_iri}
        sheet_name: Target sheet (default: first)
        header_row: Row containing headers (1-indexed)

    Returns:
        List of dicts with property IRI keys
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    rows = list(ws.iter_rows(min_row=header_row, values_only=False))
    if not rows:
        return []

    header_cells = rows[0]
    headers = [str(cell.value).strip() if cell.value else "" for cell in header_cells]

    col_to_prop = {}
    for idx, header in enumerate(headers):
        if header in column_mapping:
            col_to_prop[idx] = column_mapping[header]

    results = []
    for row in rows[1:]:
        values = [cell.value for cell in row]
        if not any(values):
            continue
        entity = {}
        for col_idx, prop_iri in col_to_prop.items():
            val = values[col_idx] if col_idx < len(values) else None
            if val is not None:
                entity[prop_iri] = val
        if entity:
            results.append(entity)

    wb.close()
    return results


def parse_word(file_path: Path) -> list[dict[str, Any]]:
    """Parse Word document, extracting structured tables and paragraphs.

    Returns list of extracted sections, each with type (table/paragraph) and content.
    """
    import docx

    doc = docx.Document(file_path)
    sections = []

    for table in doc.tables:
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        for row in table.rows[1:]:
            row_data = {}
            for idx, cell in enumerate(row.cells):
                if idx < len(headers) and headers[idx]:
                    row_data[headers[idx]] = cell.text.strip()
            if row_data:
                sections.append({"type": "table_row", "content": row_data})

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            sections.append({"type": "paragraph", "content": text, "style": para.style.name})

    return sections
