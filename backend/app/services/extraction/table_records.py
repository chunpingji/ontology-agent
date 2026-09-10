"""Logical table records over physical source cells, including nested/merged grids.

Headers explain columns; they do not own data rows. A shared cell participates
in every row it covers without making the other cells in those rows equivalent.
"""

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

from app.services.extraction.document_ir import DocumentIR

_snapshot_records = ContextVar("extraction_table_records", default=None)


@contextmanager
def record_snapshot(ir):
    """Only the runner's detached, validated IR may be reused across tasks."""
    token = _snapshot_records.set(TableRecords(ir))
    try:
        yield
    finally:
        _snapshot_records.reset(token)


def table_records(ir):
    records = _snapshot_records.get()
    return records if records is not None and records.ir is ir else TableRecords(ir)


def table_binding_anchors(ir, anchors, subject=None):
    """Keep row evidence separate from the server-created document root's title.

    All anchors remain in the binding and undergo replay/scope/semantic checks.
    Only the exact metadata root's non-table span is irrelevant to locating a
    table row; other prose, other rows and other documents are never discarded.
    """
    if (subject is None or not subject.positive_eligible
        or subject.extractor_version != "document-metadata-v1"
        or subject.identity.get("document_root") != ir.document_hash
        or subject.identity.get("classification_source") != "job_metadata"):
        return anchors
    root_anchors = [a for p in subject.provenance if p.kind == "document" for a in p.anchors]
    retained = []
    for anchor in anchors:
        ir.resolve(anchor)
        title = any(
            not anchor.table_path and not root.table_path
            and anchor.document_hash == root.document_hash == ir.document_hash
            and anchor.evidence_id == root.evidence_id
            and (root.span_start or 0) <= (anchor.span_start or 0)
            and (anchor.span_end if anchor.span_end is not None
                 else len(ir.unit(anchor.evidence_id).text))
            <= (root.span_end if root.span_end is not None
                else len(ir.unit(root.evidence_id).text))
            for root in root_anchors
        )
        if not title:
            retained.append(anchor)
    return retained


class TableRecords:
    def __init__(self, ir: DocumentIR):
        self.ir = ir
        self.tables = {}
        self.cells = {}

        def visit(table):
            self.tables[tuple(table["table_path"])] = table
            for cell in table["source_cells"]:
                self.cells[cell["cell_id"]] = cell
                for block in cell["blocks"]:
                    if block["kind"] == "table":
                        visit(block["table"])

        for table in ir.tables:
            visit(table)
        self._rows = {}
        for table in self.tables.values():
            memberships = defaultdict(set)
            for index, row in enumerate(table["grid"]):
                for identity in row:
                    memberships[identity].add(index)
            for cell in table["source_cells"]:
                self._rows[cell["cell_id"]] = memberships[cell["cell_id"]] & set(
                    range(cell["row_index"], cell["row_index"] + cell["row_span"])
                )
        self._row_units = defaultdict(list)
        self._headers = defaultdict(list)
        self._positions = {}
        self._last_block = {}
        for index, unit in enumerate(ir.evidence_units):
            self._positions[unit.evidence_id] = index
            self._last_block[unit.block_id] = index
            if not unit.table_path:
                continue
            path = tuple(unit.table_path)
            for row in self.rows(unit):
                self._row_units[(path, row)].append(unit)
            if self.is_header(unit):
                self._headers[path].append(unit)

    def table(self, unit):
        return self.tables.get(tuple(unit.table_path or []))

    def rows(self, unit, *, data_only=True):
        table = self.table(unit)
        if table is None:
            return set()
        # The grid is authoritative, including omitted leading/trailing cells.
        rows = set(self._rows.get(unit.source_cell_id, ()))
        if data_only:
            rows -= set(range(table["header_row_count"]))
        return rows

    def columns(self, unit):
        cell = self.cells.get(unit.source_cell_id)
        return (
            set(range(cell["column_index"], cell["column_index"] + cell["column_span"]))
            if cell
            else set()
        )

    def is_header(self, unit):
        return (
            bool(self.table(unit))
            and not self.rows(unit)
            and bool(self.rows(unit, data_only=False))
        )

    def row_units(self, path, row):
        return list(self._row_units.get((tuple(path), row), []))

    def metadata(self, targets):
        result = {}
        for target in targets:
            if not self.table(target):
                continue
            selected = {
                u.evidence_id: u
                for row in self.rows(target)
                for u in self.row_units(target.table_path, row)
            }
            selected.update({
                u.evidence_id: u for u in self._headers[tuple(target.table_path)]
                if self.columns(u) & self.columns(target)
            })
            for unit in sorted(selected.values(), key=lambda u: self._positions[u.evidence_id]):
                if unit.text:
                    result[unit.evidence_id] = unit
        return list(result.values())

    def notes(self, targets):
        """Nearby table notes are binding context, never additional fact targets."""
        blocks = {u.block_id for u in targets}
        result = {}
        for block in blocks:
            index = self._last_block.get(block)
            if index is None:
                continue
            last = self.ir.evidence_units[index]
            for u in self.ir.evidence_units[index + 1 : index + 3]:
                if u.kind != "paragraph" or u.section_node_id != last.section_node_id:
                    break
                if u.text:
                    result[u.evidence_id] = u
        return list(result.values())

    def infer_mapping(self, anchors, fact_anchors=()):
        units = [self.ir.unit(a.evidence_id) for a in [*anchors, *fact_anchors]]
        paths = {tuple(u.table_path or []) for u in units}
        if len(paths) != 1 or not next(iter(paths)):
            return {}
        data = [u for u in units if not self.is_header(u)]
        if not data:
            return {}
        rows = set.intersection(*(self.rows(u) for u in data))
        if len(rows) != 1:
            return {}
        return {"table_path": list(next(iter(paths))), "row_index": next(iter(rows))}

    def valid_mapping(self, mapping, anchors, fact_anchors=()):
        path, row = mapping.get("table_path"), mapping.get("row_index")
        if not path or type(row) is not int:
            return False
        units = [self.ir.unit(a.evidence_id) for a in [*anchors, *fact_anchors]]
        data = [u for u in units if not self.is_header(u)]
        if not data or any(u.table_path != path for u in units):
            return False
        columns = set.union(*(self.columns(u) for u in data))
        return all(
            bool(self.columns(u) & columns) if self.is_header(u) else row in self.rows(u)
            for u in units
        )
