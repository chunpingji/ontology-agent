"""Small source checks for the offline Schema Card probe.

Physical row membership comes from the IR grid intersected with recorded cell
spans. Header labels remain heuristic candidates because the existing IR does
not distinguish explicit Word headers from the parser's first-row fallback.
These checks locate evidence; they do not establish identity or entailment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy


def _tables(ir):
    def visit(table):
        yield table
        for cell in table.get("source_cells", []):
            for block in cell.get("blocks", []):
                if block.get("kind") == "table":
                    yield from visit(block["table"])

    for table in ir.get("tables", []):
        yield from visit(table)


def _header_rows(table):
    """Filter narrative first rows without elevating short labels to proof."""
    candidates = set(range(table.get("header_row_count", 0)))
    cells = {cell["cell_id"]: cell for cell in table.get("source_cells", [])}
    accepted = set()
    for row in candidates:
        grid = table.get("grid", [])
        if row >= len(grid):
            continue
        labels = []
        for cell_id in dict.fromkeys(grid[row]):
            cell = cells.get(cell_id, {})
            labels.append(" ".join(
                block.get("text", "") for block in cell.get("blocks", [])
                if block.get("kind") == "paragraph"
            ).strip())
        # The parser defaults to a first-row header even in key/value and
        # procedural tables. A sentence is not a column label. Short labels
        # still need field-role interpretation by the independent semantic layer.
        if len(labels) >= 2 and all(
            label and len(label) <= 96 and not re.search(r"[。！？!?；;]", label)
            for label in labels
        ):
            accepted.add(row)
    return accepted


def build_sources(case: dict, ir_dict: dict) -> dict[str, dict]:
    """Assign local refs and attach physical structure without adding evidence.

    ``logical_rows`` is the intersection of grid membership and recorded span;
    ``grid_rows`` / ``grid_columns`` retain the grid observations for inspection.
    ``column_header_refs`` contains only refs already present in this case.
    """
    tables = {tuple(table["table_path"]): table for table in _tables(ir_dict)}
    headers = {path: _header_rows(table) for path, table in tables.items()}
    cells = {
        (path, cell["cell_id"]): cell
        for path, table in tables.items() for cell in table.get("source_cells", [])
    }
    sources = {}
    for index, original in enumerate(case["source_units"]):
        source = deepcopy(original)
        ref = f"u{index}"
        source.update(
            ref=ref, row_span=1, column_span=1, logical_rows=[], column_indices=[],
            grid_rows=[], grid_columns=[], column_header_refs=[],
            is_header=False, parser_header_candidate=False, header_status="not_a_table",
            cell_structure_valid=False,
        )
        path = tuple(source.get("table_path") or [])
        if path:
            source["header_status"] = "unconfirmed"
            table = tables.get(path)
            cell = cells.get((path, source.get("source_cell_id")))
            if table is not None and cell is not None:
                row, column = cell.get("row_index"), cell.get("column_index")
                row_span, column_span = cell.get("row_span", 1), cell.get("column_span", 1)
                grid_positions = [
                    (ri, ci) for ri, values in enumerate(table.get("grid", []))
                    for ci, identity in enumerate(values) if identity == cell["cell_id"]
                ]
                source.update(
                    row_span=row_span, column_span=column_span,
                    grid_rows=sorted({ri for ri, _ in grid_positions}),
                    grid_columns=sorted({ci for _, ci in grid_positions}),
                )
                valid_coordinates = (
                    all(type(value) is int and value >= 0 for value in (row, column))
                    and all(type(value) is int and value > 0 for value in (row_span, column_span))
                    and source.get("row_index") == row and source.get("column_index") == column
                )
                if valid_coordinates:
                    positions = [
                        (ri, ci) for ri, ci in grid_positions
                        if row <= ri < row + row_span and column <= ci < column + column_span
                    ]
                    logical_rows = sorted({ri for ri, _ in positions})
                    columns = sorted({ci for _, ci in positions})
                    parser_rows = set(range(table.get("header_row_count", 0)))
                    is_header = bool(logical_rows) and set(logical_rows) <= headers[path]
                    source.update(
                        logical_rows=logical_rows, column_indices=columns,
                        cell_structure_valid=bool(positions), is_header=is_header,
                        parser_header_candidate=bool(logical_rows)
                        and set(logical_rows) <= parser_rows,
                        header_status="heuristic_candidate" if is_header else "unconfirmed",
                    )
        sources[ref] = source
    for source in sources.values():
        if not source.get("table_path") or source["is_header"]:
            continue
        source["column_header_refs"] = [
            ref for ref, other in sources.items()
            if other["is_header"] and other.get("table_path") == source["table_path"]
            and set(source["column_indices"]) & set(other["column_indices"])
        ]
    return sources


def quote_issue(sources: dict[str, dict], citation: dict) -> str | None:
    """Require a nonempty verbatim quote with one position in its source unit."""
    if (not isinstance(citation, Mapping) or not isinstance(citation.get("ref"), str)
            or citation["ref"] not in sources):
        return "citation_ref_missing"
    quote = citation.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return "citation_quote_empty"
    source = sources[citation["ref"]]["text"]
    start = source.find(quote)
    if start < 0:
        return "citation_quote_not_in_source"
    if source.find(quote, start + 1) >= 0:
        return "citation_quote_ambiguous"
    return None


def ownership_issue(
    sources: dict[str, dict], owner_citation: dict, value_citation: dict,
) -> str | None:
    """Reject incompatible physical owners; returning None is not semantic proof."""
    for citation in (owner_citation, value_citation):
        issue = quote_issue(sources, citation)
        if issue:
            return issue
    owner, value = sources[owner_citation["ref"]], sources[value_citation["ref"]]
    if owner.get("is_header"):
        return "owner_is_header_candidate"
    if not owner.get("table_path"):
        return None  # Narrative-to-table identity remains a semantic question.
    if not owner.get("cell_structure_valid"):
        return "owner_cell_structure_missing"
    if not value.get("table_path"):
        return None  # A cited narrative may explicitly describe the table subject.
    if owner["table_path"] != value["table_path"]:
        return "owner_table_mismatch"
    if not value.get("cell_structure_valid"):
        return "value_cell_structure_missing"
    if value.get("is_header"):
        return "value_is_header_candidate"
    if not set(owner["logical_rows"]) & set(value["logical_rows"]):
        return "owner_row_mismatch"
    return None


def marker_state(text: str, legend: str = "") -> str | None:
    """Interpret a standalone marker only, without creating a factual claim.

    Symbols have no global meaning. Only explicit definitions in the supplied
    legend authorize the small supported interpretations; conflicts stay unset.
    """
    marker = text.strip()
    if marker.casefold() in {"n/a", "not applicable", "不适用"}:
        return "not_applicable"
    if marker not in {"—", "×", "√"} or not legend:
        return None
    definitions = re.findall(
        re.escape(marker) + r"[”’\"']?\s*(?:代表|表示|意味着|=|：|:)\s*([^；;。\n]+)",
        legend,
    )
    states = set()
    for definition in definitions:
        definition = definition.strip()
        if re.fullmatch(r"(?:相关的?|研究|数据|证据)*(?:不充分|不足|未知|不确定)", definition):
            states.add("unknown")
        elif re.fullmatch(r"(?:没有|不存在|无)(?:相应的?|此)(?:毒性|性质|属性|现象|反应|作用)",
                          definition):
            states.add("negated")
        elif re.fullmatch(r"(?:有|存在)相应的?(?:毒性|性质|属性|现象|反应|作用)", definition):
            states.add("affirmed")
        else:
            return None
    return next(iter(states)) if len(states) == 1 else None
