"""Physical reading windows, exact references and reusable original fields."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from app.services.extraction.document_ir import DocumentIR

QUOTE_FRAGMENT_BYTES = 8192


def identity(*parts) -> str:
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def missing(value: str) -> bool:
    return value.strip().casefold() in {
        "",
        "n/a",
        "na",
        "n.a.",
        "not available",
        "not applicable",
        "unknown",
        "未提供",
        "未知",
        "不适用",
        "无数据",
        "未测定",
        "—",
        "–",
        "-",
    }


def reference(ir: DocumentIR, evidence_id: str, start: int, end: int) -> dict:
    anchor = ir.anchor(evidence_id, start, end)
    unit = ir.unit(evidence_id)
    return {
        "source_id": evidence_id,
        "text": ir.resolve(anchor),
        "start": start,
        "end": end,
        "page": unit.physical_page_number,
        "section_id": unit.section_node_id,
        "block_id": unit.block_id,
    }


def references_cover(anchor, sources):
    """Require uninterrupted coverage in the same physical source unit."""
    end = anchor["start"]
    for ref in sorted(sources, key=lambda ref: ref["start"]):
        if ref["source_id"] == anchor["source_id"] and ref["start"] <= end:
            end = max(end, ref["end"])
    return end >= anchor["end"]


def _quote_offsets(text: str, quote: str) -> list[int]:
    """Enumerate every physical occurrence, including overlapping substrings."""
    if not quote:
        return []
    offsets = []
    start = text.find(quote)
    while start >= 0:
        offsets.append(start)
        start = text.find(quote, start + 1)
    return offsets


@dataclass
class Window:
    id: str
    sources: list[dict]
    fields: list[dict]
    primary_ids: list[str]
    entity_ids: list[str] | None = None
    primary_ranges: list[dict] | None = None

    @cached_property
    def quote_fragments(self):
        """Citation choices only; punctuation never changes ownership or source context."""
        fragments = {}
        used = 2
        for source in self.sources:
            spans = list(re.finditer(r"[^，,。！？!?；;\n]+[，,。！？!?；;\n]*", source["text"]))
            if len(spans) < 2:
                continue
            for match in spans:
                if not match.group().strip():
                    continue
                start, end = source["offset"] + match.start(), source["offset"] + match.end()
                key = "Q" + identity("quote-fragment", source["evidence_id"], start, end)
                fragment = {
                    "source_id": key, "parent_source_id": source["source_id"],
                    "evidence_id": source["evidence_id"], "offset": start,
                    "text": match.group(), "start": match.start(), "end": match.end(),
                }
                cost = len(json.dumps({k: v for k, v in fragment.items()
                                       if k not in {"evidence_id", "offset"}},
                                      ensure_ascii=False).encode()) + 2
                if used + cost <= QUOTE_FRAGMENT_BYTES:
                    fragments[key] = fragment
                    used += cost
        return fragments

    def citation_source(self, source_id):
        source = next((s for s in self.sources if s["source_id"] == source_id), None)
        return source if source is not None else self.quote_fragments.get(source_id)

    def discovery_payload(self):
        return {**self.payload(), "quote_fragments": [
            {k: fragment[k] for k in ("source_id", "parent_source_id", "start", "end", "text")}
            for fragment in self.quote_fragments.values()
        ]}

    def citation_choices(self):
        return {key: value["parent_source_id"] for key, value in self.quote_fragments.items()}

    def primary(self):
        if self.primary_ranges is not None:
            return self.primary_ranges
        return [{"evidence_id": s["evidence_id"], "start": s["offset"],
                 "end": s["offset"] + len(s["text"])} for s in self.sources
                if s["evidence_id"] in self.primary_ids]

    def owns(self, ref):
        return references_cover(ref, [{"source_id": r["evidence_id"],
                                       "start": r["start"], "end": r["end"]}
                                      for r in self.primary()])

    def payload(self):
        return {
            "reading_scope": [
                {"source_id": s["source_id"], "start": max(r["start"], s["offset"]) - s["offset"],
                 "end": min(r["end"], s["offset"] + len(s["text"])) - s["offset"]}
                for s in self.sources for r in self.primary()
                if r["evidence_id"] == s["evidence_id"]
                and r["start"] < s["offset"] + len(s["text"]) and r["end"] > s["offset"]
            ],
            "sources": [
                {k: v for k, v in source.items() if k not in {"evidence_id", "offset"}}
                for source in self.sources
            ],
            "fields": [
                {
                    "field_id": field["alias"],
                    "label": field["label"],
                    "value": field["value"],
                    "missing": field["missing"],
                    "sources": field["source_aliases"],
                    "row": field.get("row"),
                }
                for field in self.fields
            ],
        }

    def resolve(self, ir, quote, *, within=None):
        if hasattr(quote, "model_dump"):
            quote = quote.model_dump()
        source = self.citation_source(quote["source_id"])
        if source is None:
            raise ValueError("source_outside_reading_window")
        text = quote["text"]
        if not text.strip():
            raise ValueError("empty_source_quote")
        occurrences = _quote_offsets(source["text"], text)
        if not occurrences:
            raise ValueError("source_quote_mismatch")
        occurrence = quote.get("occurrence")
        if len(occurrences) != 1 and occurrence is None:
            scoped = [index for index, offset in enumerate(occurrences)
                      if within and within["source_id"] == source["evidence_id"]
                      and within["start"] <= source["offset"] + offset
                      and source["offset"] + offset + len(text) <= within["end"]]
            if len(scoped) != 1:
                raise ValueError("source_quote_ambiguous")
            occurrence = scoped[0]
        occurrence = 0 if occurrence is None else occurrence
        if type(occurrence) is not int or not 0 <= occurrence < len(occurrences):
            raise ValueError("source_occurrence_invalid")
        start = source["offset"] + occurrences[occurrence]
        return reference(ir, source["evidence_id"], start, start + len(text))

    def resolve_anchor(self, ir, anchor):
        """Select visible source text; its physical coordinates belong to the IR."""
        if hasattr(anchor, "model_dump"):
            anchor = anchor.model_dump()
        if anchor.get("text") is not None:
            return self.resolve(ir, anchor)
        if anchor.get("occurrence") is not None:
            raise ValueError("anchor_occurrence_requires_text")
        ref = self.quotes(ir, [anchor["source_id"]])[0]
        if not ref["text"].strip():
            raise ValueError("empty_source_anchor")
        return ref

    def quotes(self, ir, values):
        result = []
        for value in values:
            source = self.citation_source(value)
            if source is None:
                raise ValueError("source_outside_reading_window")
            ref = reference(
                ir, source["evidence_id"], source["offset"], source["offset"] + len(source["text"])
            )
            if ref not in result:
                result.append(ref)
        return result


def _all_tables(tables):
    for table in tables:
        yield table
        for cell in table.get("source_cells", []):
            for block in cell.get("blocks", []):
                if block.get("kind") == "table":
                    yield from _all_tables([block["table"]])


def _cell_text(cell):
    return "\n".join(
        block["text"].strip()
        for block in cell.get("blocks", [])
        if block.get("kind") == "paragraph" and block["text"].strip()
    )


def _field_rows_start(table):
    """Recognize explicit field/value layout, never guess from a two-column shape.

    The parser's one-row fallback is not a semantic header declaration. Ambiguous
    short rows stay as source cells for discovery to interpret. These words name
    table structure, not domain properties or ontology predicates.
    """
    grid = table.get("grid", [])
    if not grid or any(len(row) != 2 for row in grid):
        return None
    cells = {cell["cell_id"]: cell for cell in table.get("source_cells", [])}
    left, right = (_cell_text(cells.get(key, {})) for key in grid[0])
    label_headers = {"field", "attribute", "property", "parameter", "字段", "属性", "参数"}
    value_headers = {"value", "content", "值", "内容", "数值"}
    if left.casefold() in label_headers and right.casefold() in value_headers:
        return 1
    if left.endswith((":", "：")) and left[:-1].strip():
        return 0
    if table.get("header_row_count", 0) != 0:
        return None
    # A no-header table can also be numbered data. Only repeated short labels
    # paired with substantial prose supply a field-like shape without markers.
    values = [(_cell_text(cells.get(a, {})), _cell_text(cells.get(b, {}))) for a, b in grid]
    if len(values) >= 2 and all(
        0 < len(label) <= 64
        and any(character.isalpha() for character in label)
        and not any(mark in label for mark in "\n。；;.!！？?")
        and len(value) >= 60
        and any(mark in value for mark in "。；;.!！？?")
        for label, value in values
    ):
        return 0
    return None


def _row_field_bindings(ir, tables, sources):
    """Return row-local label sources keyed by their visible value source alias."""
    by_cell = defaultdict(list)
    for source in sources:
        unit = ir.unit(source["evidence_id"])
        if unit.source_cell_id:
            by_cell[unit.source_cell_id].append(source)
    bindings, consumed, ambiguous = {}, set(), set()
    for table in tables.values():
        start = _field_rows_start(table)
        if start is None:
            continue
        for row_index, row in enumerate(table["grid"]):
            if row_index < start or len(row) != 2 or None in row or row[0] == row[1]:
                continue
            labels, values = by_cell[row[0]], by_cell[row[1]]
            # Do not compose an artificial label out of missing paragraphs or
            # text fragments. Unbound source remains available to the model.
            if len(labels) != 1 or not values:
                continue
            label_source = labels[0]
            label_unit = ir.unit(label_source["evidence_id"])
            if label_source["offset"] or label_source["text"] != label_unit.text:
                continue
            label = label_source["text"].strip().rstrip(":：").strip()
            if not label or len(label) > 64 or "\n" in label:
                continue
            for value_source in values:
                alias = value_source["source_id"]
                previous = bindings.get(alias)
                if previous and previous[0]["source_id"] != label_source["source_id"]:
                    ambiguous.add(alias)
                bindings[alias] = (label_source, label, row_index)
            consumed.add(label_source["source_id"])
    for alias in ambiguous:
        bindings.pop(alias, None)
    return bindings, consumed


def _column_headers(ir, table, unit, sources):
    """Resolve merged column headers through the physical grid, not column guesses."""
    grid = table.get("grid", [])
    if unit.row_index is None or unit.row_index >= len(grid):
        return []
    columns = [
        index for index, cell in enumerate(grid[unit.row_index]) if cell == unit.source_cell_id
    ]
    header_cells = {
        row[column]
        for row in grid[: table.get("header_row_count", 0)]
        for column in columns
        if column < len(row) and row[column] is not None
    }
    return [
        source
        for source in sources
        if ir.unit(source["evidence_id"]).source_cell_id in header_cells
    ]


def _fields(ir, sources):
    """Bind physical labels/values only; ownership remains a model hypothesis."""
    fields = []
    tables = {tuple(t["table_path"]): t for t in _all_tables(ir.tables)}
    row_bindings, consumed = _row_field_bindings(ir, tables, sources)
    for source in sources:
        if source["source_id"] in consumed:
            continue
        unit = ir.unit(source["evidence_id"])
        # A budget fragment is not the whole original field. Keep it as citable
        # text for discovery instead of asserting its truncated value is complete.
        if source["offset"] != 0 or source["text"] != unit.text:
            continue
        match = re.match(r"^\s*([^：:\n]{1,64})[：:]\s*(.*?)\s*$", source["text"], re.S)
        refs, label_refs, label, value, row = [], [], None, None, None
        if source["source_id"] in row_bindings:
            label_source, label, row = row_bindings[source["source_id"]]
            start = label_source["text"].index(label)
            label_refs = [reference(ir, label_source["evidence_id"], start, start + len(label))]
            value = source["text"].strip()
            if value:
                start = source["offset"] + source["text"].index(value)
                refs = [reference(ir, unit.evidence_id, start, start + len(value))]
        elif match:
            label, value = match.group(1).strip(), match.group(2)
            if value:
                start = source["offset"] + match.start(2)
                refs = [reference(ir, unit.evidence_id, start, start + len(value))]
            label_refs = [
                reference(
                    ir,
                    unit.evidence_id,
                    source["offset"] + match.start(1),
                    source["offset"] + match.end(1),
                )
            ]
        elif unit.table_path:
            table = tables.get(tuple(unit.table_path), {})
            header_count = table.get("header_row_count", 0)
            if unit.row_index is None or unit.row_index < header_count:
                continue
            headers = _column_headers(ir, table, unit, sources)
            if not headers:
                continue
            label = " / ".join(s["text"].strip() for s in headers if s["text"].strip())
            if not label:
                continue
            value = source["text"].strip()
            if value:
                start = source["offset"] + source["text"].index(value)
                refs = [reference(ir, unit.evidence_id, start, start + len(value))]
            label_refs = [
                reference(ir, s["evidence_id"], s["offset"], s["offset"] + len(s["text"]))
                for s in headers
            ]
            row = unit.row_index
        if label is None:
            continue
        evidence = [*label_refs, *refs]
        fields.append(
            {
                "id": identity("field", evidence),
                "alias": f"F{len(fields) + 1}",
                "label": label,
                "value": value,
                "missing": missing(value),
                "evidence": evidence,
                "value_evidence": refs,
                "row": row,
                "source_aliases": [
                    s["source_id"]
                    for s in sources
                    if any(ref["source_id"] == s["evidence_id"] for ref in evidence)
                ],
            }
        )
    return fields


def make_window(ir, pieces, primary_ids):
    sources, seen = [], set()
    for evidence_id, start, end in pieces:
        if (evidence_id, start, end) in seen or start == end:
            continue
        seen.add((evidence_id, start, end))
        unit = ir.unit(evidence_id)
        sources.append(
            {
                "source_id": f"S{len(sources) + 1}",
                "evidence_id": evidence_id,
                "offset": start,
                "text": unit.text[start:end],
                "section": unit.section_node_id,
                "kind": unit.kind,
                "table": unit.table_path,
                "row": unit.row_index,
                "column": unit.column_index,
            }
        )
    return Window(
        identity("window", ir.document_hash, pieces),
        sources,
        _fields(ir, sources),
        list(primary_ids),
    )


def build_windows(ir: DocumentIR, *, max_chars=4800, max_sources=192):
    """Batch adjacent small sections; large sections retain lead-ins and headers.

    Budget boundaries do not assign entities, merge identities or restrict the
    citation rights of a visible source. Primary IDs separately account coverage.
    """
    sections = []
    tables = {tuple(t["table_path"]): t for t in _all_tables(ir.tables)}
    for unit in ir.evidence_units:
        if unit.text.strip() and not unit.navigation_role:
            if not sections or sections[-1][-1].section_node_id != unit.section_node_id:
                sections.append([])
            sections[-1].append(unit)
    windows, small_units = [], []

    def flush_small():
        if small_units:
            windows.append(make_window(
                ir,
                [(unit.evidence_id, 0, len(unit.text)) for unit in small_units],
                [unit.evidence_id for unit in small_units],
            ))
            small_units.clear()

    for units in sections:
        # Small heading-only sections must not force separate model calls or
        # disconnect the subject's name from nearby source fields. Each source
        # keeps its own section and table coordinates; batching is not ownership.
        if sum(len(u.text) for u in units) <= max_chars and len(units) <= max_sources:
            if small_units and (
                sum(len(unit.text) for unit in [*small_units, *units]) > max_chars
                or len(small_units) + len(units) > max_sources
            ):
                flush_small()
            small_units.extend(units)
            continue
        flush_small()
        lead = [
            (u.evidence_id, 0, len(u.text))
            for u in units[:3]
            if not u.table_path and len(u.text) <= 400
        ]
        pending, primary = [], []

        def flush():
            if pending:
                windows.append(make_window(ir, [*lead, *pending], primary))
                pending.clear()
                primary.clear()

        groups = []
        row_key = None
        for unit in units:
            key = (tuple(unit.table_path), unit.row_index) if unit.table_path else None
            pieces = [(unit.evidence_id, start, min(start + 1800, len(unit.text)))
                      for start in range(0, len(unit.text), 1600)]
            if key is not None and key == row_key:
                groups[-1].extend(pieces)
            elif key is not None:
                groups.append(pieces)
            else:
                groups.extend([[piece] for piece in pieces])
            row_key = key

        headers_by_table = {}
        for unit in units:
            if (unit.table_path and unit.row_index is not None
                    and unit.row_index < tables.get(
                        tuple(unit.table_path), {},
                    ).get("header_row_count", 0)):
                headers_by_table.setdefault(tuple(unit.table_path), []).append(
                    (unit.evidence_id, 0, len(unit.text))
                )
        for group in groups:
            unit = ir.unit(group[0][0])
            headers = headers_by_table.get(tuple(unit.table_path or ()), [])
            proposed = list(dict.fromkeys([*lead, *pending, *headers, *group]))
            if pending and (
                sum(end - begin for _, begin, end in proposed) > max_chars
                or len(proposed) > max_sources
            ):
                flush()
            # Keep one physical row intact. An oversized row is handled by the
            # existing request-budget split, rather than separating its subject
            # cell from the values during initial packing.
            pending.extend(item for item in [*headers, *group] if item not in pending)
            for evidence_id, _, _ in group:
                if evidence_id not in primary:
                    primary.append(evidence_id)
        flush()
    flush_small()
    return windows


def quote_for_reference(window: Window, ref: dict):
    for source in window.sources:
        if (
            source["evidence_id"] == ref["source_id"]
            and source["offset"] <= ref["start"]
            and ref["end"] <= source["offset"] + len(source["text"])
        ):
            text = ref["text"]
            starts = _quote_offsets(source["text"], text)
            return {
                "source_id": source["source_id"],
                "text": text,
                "occurrence": starts.index(ref["start"] - source["offset"])
                if len(starts) > 1
                else None,
            }
    return None
