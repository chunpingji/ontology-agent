"""Co-read bounded consecutive table rows, retaining each physical source."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .records import RecordIndex


_FORWARD_TABLE = re.compile(
    r"如下表|(?:详见|参见|见)下表|下表(?:所示|列出|列示|给出|汇总)|"
    r"(?:见|如)以下表|\b(?:in\s+the\s+table\s+below|following\s+table)\b",
    re.IGNORECASE,
)
_CAPTION = re.compile(r"^(?:表\s*\d+(?:[.．-]\d+)*|Table\s+\d+)\s*\S*", re.IGNORECASE)
MAX_LEAD_CHARS = 240


def find_table_lead_ins(index: RecordIndex) -> dict[tuple[str, ...], tuple[str, ...]]:
    """Only a forward table reference or adjacent numbered caption creates a link.

    Do not cross another paragraph, heading, section, table or navigation unit.
    Empty/header-only tables and nested tables cannot consume an introduction.
    These links provide reading context, never entity identity or row ownership.
    """
    rows = defaultdict(list)
    for record in index.records:
        if record.kind == "table_row" and len(record.table_path) == 1:
            rows[record.table_path].append(record)
    starts = {}
    for position, unit in enumerate(index.ir.evidence_units):
        if unit.table_path:
            starts.setdefault(tuple(unit.table_path), position)
    links = {}
    for path, records in rows.items():
        first = records[0]
        # Require an actual data row, rather than a one-cell table used as a title.
        if len({u.source_cell_id for u in first.source_units if u.text.strip()}) < 2:
            continue
        cursor = starts[path] - 1
        preceding = []
        while cursor >= 0:
            unit = index.ir.evidence_units[cursor]
            if (unit.kind != "paragraph" or unit.table_path or unit.navigation_role
                    or unit.section_node_id != first.section_node_id):
                break
            if not unit.text.strip():
                cursor -= 1
                continue
            record = index.records_by_evidence[unit.evidence_id][0]
            content = record.text.strip()
            if len(content) > MAX_LEAD_CHARS:
                break
            if _FORWARD_TABLE.search(content):
                preceding.append(record.record_id)
                break
            if not preceding and _CAPTION.match(content):
                preceding.append(record.record_id)
                cursor = index.source_positions[record.record_id] - 1
                continue
            break
        if preceding:
            links[path] = tuple(reversed(preceding))
    return links


def table_reading_groups(
    index: RecordIndex, *, max_group_chars: int, max_records: int, max_data_rows: int = 4,
):
    """Yield bounded runs of consecutive rows with the same physical headers.

    Only the first batch can own an introduction's independent facts. Later
    batches receive it as non-fact context through table_lead_in_refs(). Records
    that cannot fit remain independently schedulable, without truncating sources.
    """
    if min(max_group_chars, max_records, max_data_rows) < 1 or max_data_rows > 4:
        raise ValueError("table_reading_limit_invalid")

    def fits(sources):
        return (len(sources) <= max_records
                and sum(len(index.by_id[rid].text) for rid in sources) <= max_group_chars)

    first_rows = {}
    pending, previous, data_rows = [], None, 0
    for record in index.records:
        top_level_row = (record.kind == "table_row" and len(record.table_path or ()) == 1
                         and record.row_index is not None)
        if top_level_row:
            first_rows.setdefault(record.table_path, record.record_id)
        # A vertically merged cell has several logical owners. Keep the existing
        # single-row context for it instead of assigning it to a batch's first row.
        eligible = (top_level_row
                    and all(len(index.tables.rows(unit)) == 1 for unit in record.source_units))
        same_structure = bool(
            eligible and previous is not None
            and record.table_path == previous.table_path
            and record.section_node_id == previous.section_node_id
            and record.row_index == previous.row_index + 1
            and record.header_units == previous.header_units
        )
        if pending and (not same_structure or data_rows >= max_data_rows
                        or not fits([*pending, record.record_id])):
            if len(pending) > 1:
                yield tuple(pending)
            pending, previous, data_rows = [], None, 0
        if not eligible:
            continue
        first = first_rows[record.table_path]
        if not pending and record.record_id == first:
            sources = [record.record_id, *index.table_lead_ins.get(record.table_path, ())]
            pending = sources if fits(sources) else [record.record_id]
        else:
            pending.append(record.record_id)
        previous, data_rows = record, data_rows + 1
    if len(pending) > 1:
        yield tuple(pending)


def table_lead_in_refs(index: RecordIndex, record_ids):
    identities = dict.fromkeys(
        unit.evidence_id for rid in record_ids
        for lead in index.table_lead_ins.get(index.by_id[rid].table_path, ())
        for unit in index.by_id[lead].source_units
    )
    return [index.ir.anchor(identity, 0, len(index.ir.unit(identity).text))
            for identity in identities]
