"""Smaller physical reading plans for incomplete discovery, without state replay."""

from __future__ import annotations

from collections import defaultdict

from .source import Window, identity, make_window

PLAN_PROTOCOL = "harness-reading-plan/1"
MIN_SPLIT_SPAN = 128
MAX_CHARACTER_OVERLAP = 80
MAX_LEAD_CHARACTERS = 400


def _tables(tables):
    for table in tables:
        yield table
        for cell in table.get("source_cells", ()):
            for block in cell.get("blocks", ()):
                if block.get("kind") == "table":
                    yield from _tables([block["table"]])


def _piece(source):
    return source["evidence_id"], source["offset"], source["offset"] + len(source["text"])


def _range(piece):
    return {"evidence_id": piece[0], "start": piece[1], "end": piece[2]}


def _validate_piece(ir, value):
    evidence_id, start, end = value
    unit = ir.unit(evidence_id)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(unit.text):
        raise ValueError("reading_plan_source_range_invalid")
    return evidence_id, start, end


def _ordered(ir, pieces):
    order = {unit.evidence_id: index for index, unit in enumerate(ir.evidence_units)}
    return sorted(set(pieces), key=lambda item: (order[item[0]], item[1], item[2]))


def _primary(ir, window):
    explicit = getattr(window, "primary_ranges", None)
    values = (
        [(item["evidence_id"], item["start"], item["end"]) for item in explicit]
        if explicit is not None else
        [_piece(source) for source in window.sources if source["evidence_id"] in window.primary_ids]
    )
    merged = []
    for value in _ordered(ir, [_validate_piece(ir, item) for item in values]):
        if merged and merged[-1][0] == value[0] and value[1] <= merged[-1][2]:
            merged[-1] = (value[0], merged[-1][1], max(value[2], merged[-1][2]))
        else:
            merged.append(value)
    return merged


def _weighted_split(groups):
    weights = [sum(end - start for _, start, end in group) for group in groups]
    total = sum(weights)
    split = min(range(1, len(groups)), key=lambda index: abs(sum(weights[:index]) - total / 2))
    return [
        [piece for group in groups[:split] for piece in group],
        [piece for group in groups[split:] for piece in group],
    ]


def _covers(pieces, target):
    pending = target[1]
    for evidence_id, start, end in sorted(pieces, key=lambda item: item[1]):
        if evidence_id != target[0] or end <= pending:
            continue
        if start > pending:
            return False
        pending = end
        if pending >= target[2]:
            return True
    return False


def split_reading_window(ir, window: Window) -> list[Window]:
    """Return exactly two smaller windows, or no safe split at the minimum.

    Primary coverage is retained as source intervals. Headings/table headers
    and a bounded section lead-in can be shared context with identical citation
    rights. They do not turn all of the previous window into repeated context.
    """
    original = [_piece(source) for source in window.sources]
    primary = _primary(ir, window)
    if not primary:
        return []
    tables = {tuple(table["table_path"]): table for table in _tables(ir.tables)}

    def header(piece):
        unit = ir.unit(piece[0])
        return bool(
            unit.table_path and unit.row_index is not None
            and unit.row_index < tables.get(tuple(unit.table_path), {}).get("header_row_count", 0)
        )

    auxiliary = [piece for piece in primary if ir.unit(piece[0]).kind == "heading" or header(piece)]
    content = [piece for piece in primary if piece not in auxiliary]
    if not content:
        return []
    grouped = defaultdict(list)
    for piece in content:
        unit = ir.unit(piece[0])
        key = (tuple(unit.table_path), unit.row_index) if unit.table_path else piece
        grouped[key].append(piece)
    groups = list(grouped.values())
    if len(groups) >= 2:
        halves = _weighted_split(groups)
    elif len(content) >= 2:
        # A single very wide row can be divided by cells; its first cell below
        # remains available to interpret the row rather than inventing a subject.
        halves = _weighted_split([[piece] for piece in content])
    else:
        evidence_id, start, end = content[0]
        length = end - start
        if length < MIN_SPLIT_SPAN:
            return []
        middle = start + length // 2
        overlap = min(MAX_CHARACTER_OVERLAP, length // 8)
        halves = [[(evidence_id, start, middle + overlap)],
                  [(evidence_id, middle - overlap, end)]]
    # Auxiliary primary text is accounted once, while it can be cited in both
    # children where it supplies structural context.
    halves[0] = _ordered(ir, [*auxiliary, *halves[0]])
    original_size = sum(end - start for _, start, end in original)
    leads = []
    seen_sections = set()
    for piece in original:
        unit = ir.unit(piece[0])
        if (not unit.table_path and unit.kind != "heading"
                and piece[2] - piece[1] <= MAX_LEAD_CHARACTERS
                and unit.section_node_id not in seen_sections):
            leads.append(piece)
            seen_sections.add(unit.section_node_id)
    children = []
    original_fields = {field["id"] for field in window.fields}
    for index, child_primary in enumerate(halves):
        selected_tables = {tuple(ir.unit(piece[0]).table_path) for piece in child_primary
                           if ir.unit(piece[0]).table_path}
        selected_rows = {(tuple(ir.unit(piece[0]).table_path), ir.unit(piece[0]).row_index)
                         for piece in child_primary if ir.unit(piece[0]).table_path}
        selected_sections = {ir.unit(piece[0]).section_node_id for piece in child_primary}
        needed = [piece for piece in original if header(piece)
                  and tuple(ir.unit(piece[0]).table_path) in selected_tables]
        needed.extend(piece for piece in original
                      if ir.unit(piece[0]).kind == "heading"
                      and ir.unit(piece[0]).section_node_id in selected_sections
                      and piece[2] - piece[1] <= MAX_LEAD_CHARACTERS)
        # Preserve the row's first physical cell as context when splitting a
        # wide row. This is layout context, never an identity assertion.
        for row in selected_rows:
            row_pieces = [piece for piece in original
                          if (tuple(ir.unit(piece[0]).table_path or ()),
                              ir.unit(piece[0]).row_index) == row]
            if row_pieces:
                first = min(row_pieces, key=lambda piece: (
                    ir.unit(piece[0]).column_index or 0, piece[1],
                ))
                if first[2] - first[1] <= MAX_LEAD_CHARACTERS:
                    needed.append(first)
        base = _ordered(ir, [*child_primary, *needed])
        shared = [piece for piece in leads if not _covers(base, piece)]
        pieces = _ordered(ir, [*base, *shared])
        # Repeating an optional lead must not reconstruct the entire parent.
        while shared and sum(end - start for _, start, end in pieces) >= original_size:
            shared.pop()
            pieces = _ordered(ir, [*base, *shared])
        if sum(end - start for _, start, end in pieces) >= original_size:
            return []
        child = make_window(ir, pieces, list(dict.fromkeys(piece[0] for piece in child_primary)))
        # A character split can otherwise turn half a literal into a new field
        # with an apparently complete value. Only intact original fields remain.
        child.fields = [field for field in child.fields if field["id"] in original_fields]
        for field_index, field in enumerate(child.fields, 1):
            field["alias"] = f"F{field_index}"
        child.primary_ranges = [_range(piece) for piece in child_primary]
        child.id = identity("reading-continuation", ir.document_hash, ir.structure_hash,
                            window.id, index, child.primary_ranges, pieces)
        children.append(child)
    return children


def serialize_window_plan(window: Window) -> dict:
    """Persist coordinates/IDs only; document text remains in the frozen IR."""
    primary = getattr(window, "primary_ranges", None)
    if primary is None:
        primary = [_range(_piece(source)) for source in window.sources
                   if source["evidence_id"] in window.primary_ids]
    return {
        "protocol": PLAN_PROTOCOL, "id": window.id,
        "sources": [_range(_piece(source)) for source in window.sources],
        "source_hash": identity("reading-plan-sources", [
            [source["evidence_id"], source["offset"], source["text"]]
            for source in window.sources
        ]),
        "primary_ranges": primary,
        "field_ids": [field["id"] for field in window.fields],
    }


def restore_window_plan(ir, plan: dict) -> Window:
    if plan.get("protocol") != PLAN_PROTOCOL:
        raise ValueError("reading_plan_protocol_mismatch")
    pieces = [_validate_piece(ir, (item["evidence_id"], item["start"], item["end"]))
              for item in plan["sources"]]
    primary = [_validate_piece(ir, (item["evidence_id"], item["start"], item["end"]))
               for item in plan["primary_ranges"]]
    if not pieces or not primary or any(not _covers(pieces, value) for value in primary):
        raise ValueError("reading_plan_primary_not_visible")
    window = make_window(ir, pieces, list(dict.fromkeys(piece[0] for piece in primary)))
    if serialize_window_plan(window)["source_hash"] != plan["source_hash"]:
        raise ValueError("reading_plan_source_changed")
    available = {field["id"]: field for field in window.fields}
    if any(field_id not in available for field_id in plan["field_ids"]):
        raise ValueError("reading_plan_original_field_not_available")
    window.fields = [available[field_id] for field_id in plan["field_ids"]]
    for index, field in enumerate(window.fields, 1):
        field["alias"] = f"F{index}"
    window.id = plan["id"]
    window.primary_ranges = [_range(piece) for piece in primary]
    return window
