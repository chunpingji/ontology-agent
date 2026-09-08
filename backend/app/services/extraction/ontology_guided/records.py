"""Document record index with physical cells and logical views kept separate."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.document_ir import DocumentIR, EvidenceUnit
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import FieldGroup, RecordView
from app.services.extraction.table_records import TableRecords


@dataclass(frozen=True)
class IndexedRecord:
    record_id: str
    kind: str
    section_node_id: str
    source_units: tuple[EvidenceUnit, ...]
    header_units: tuple[EvidenceUnit, ...] = ()
    note_units: tuple[EvidenceUnit, ...] = ()
    parent_units: tuple[EvidenceUnit, ...] = ()
    table_path: tuple[str, ...] | None = None
    row_index: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(unit.text for unit in self.source_units if unit.text)


class RecordIndex:
    """Build immutable retrieval targets from one detached, validated IR."""

    def __init__(self, ir: DocumentIR):
        self.ir = DocumentIR.model_validate(ir.model_dump(mode="json"))
        self.tables = TableRecords(self.ir)
        self.positions = {
            unit.evidence_id: position for position, unit in enumerate(self.ir.evidence_units)
        }
        self.records = self._build_records()
        self.by_id = {record.record_id: record for record in self.records}
        self.record_views = [self._view(record) for record in self.records]
        self.field_groups = self._field_groups()

    def _ordered(self, units) -> tuple[EvidenceUnit, ...]:
        unique = {unit.evidence_id: unit for unit in units if unit.text}
        return tuple(sorted(unique.values(), key=lambda item: self.positions[item.evidence_id]))

    def _build_records(self) -> list[IndexedRecord]:
        records: list[IndexedRecord] = []
        row_records: dict[tuple[tuple[str, ...], int], IndexedRecord] = {}
        for path, table in self.tables.tables.items():
            for row_index in range(table["header_row_count"], len(table["grid"])):
                row_units = self.tables.row_units(path, row_index)
                source = self._ordered(
                    unit for unit in row_units if not self.tables.is_header(unit)
                )
                if not source:
                    continue
                headers = self._ordered(
                    unit for unit in self.tables.metadata(row_units) if self.tables.is_header(unit)
                )
                record = IndexedRecord(
                    record_id=stable_id(
                        "record",
                        [self.ir.analysis_id, "table_row", list(path), row_index],
                    ),
                    kind="table_row",
                    section_node_id=source[0].section_node_id,
                    source_units=source,
                    header_units=headers,
                    note_units=self._ordered(self.tables.notes(row_units)),
                    table_path=path,
                    row_index=row_index,
                )
                records.append(record)
                row_records[(path, row_index)] = record

        # Preserve nested-table parent records as context only.
        containers: dict[tuple[str, ...], tuple[tuple[str, ...], dict]] = {}
        for path, table in self.tables.tables.items():
            for cell in table["source_cells"]:
                for block in cell["blocks"]:
                    if block["kind"] == "table":
                        containers[tuple(block["table"]["table_path"])] = (path, cell)
        if containers:
            replaced: list[IndexedRecord] = []
            for record in records:
                parent_units: list[EvidenceUnit] = []
                path = record.table_path
                visited: set[tuple[str, ...]] = set()
                while path in containers and path not in visited:
                    visited.add(path)
                    parent_path, cell = containers[path]
                    data_rows = self.tables._rows.get(cell["cell_id"], set()) - set(
                        range(self.tables.tables[parent_path]["header_row_count"])
                    )
                    for parent_row in data_rows:
                        parent = row_records.get((parent_path, parent_row))
                        if parent:
                            parent_units.extend(parent.source_units)
                            parent_units.extend(parent.header_units)
                    path = parent_path
                replaced.append(
                    IndexedRecord(
                        **{
                            **record.__dict__,
                            "parent_units": self._ordered(parent_units),
                        }
                    )
                )
            records = replaced

        paragraphs: dict[tuple[str, int], list[EvidenceUnit]] = defaultdict(list)
        for unit in self.ir.evidence_units:
            if unit.table_path or unit.kind == "heading" or not unit.text:
                continue
            paragraphs[(unit.section_node_id, unit.paragraph_index)].append(unit)
        for (section_node_id, paragraph_index), units in paragraphs.items():
            source = self._ordered(units)
            records.append(
                IndexedRecord(
                    record_id=stable_id(
                        "record",
                        [self.ir.analysis_id, "paragraph", section_node_id, paragraph_index],
                    ),
                    kind="paragraph",
                    section_node_id=section_node_id,
                    source_units=source,
                )
            )
        records.sort(
            key=lambda record: min(self.positions[unit.evidence_id] for unit in record.source_units)
        )
        return records

    def _anchor(self, unit: EvidenceUnit) -> EvidenceAnchor:
        return self.ir.anchor(unit.evidence_id, 0, len(unit.text))

    def _view(self, record: IndexedRecord) -> RecordView:
        source_cell_ids = sorted(
            {unit.source_cell_id for unit in record.source_units if unit.source_cell_id}
        )
        return RecordView(
            record_view_id=stable_id(
                "record-view",
                [self.ir.analysis_id, record.record_id, record.table_path, record.row_index],
            ),
            record_id=record.record_id,
            source_record_kind=record.kind,
            section_node_id=record.section_node_id,
            source_refs=[self._anchor(unit) for unit in record.source_units],
            header_refs=[self._anchor(unit) for unit in record.header_units],
            note_refs=[self._anchor(unit) for unit in record.note_units],
            parent_context_refs=[self._anchor(unit) for unit in record.parent_units],
            table_path=list(record.table_path) if record.table_path else None,
            logical_row_id=(
                stable_id("logical-row", [list(record.table_path), record.row_index])
                if record.table_path is not None
                else None
            ),
            source_cell_ids=source_cell_ids,
        )

    @staticmethod
    def _looks_like_field(text: str) -> bool:
        compact = text.strip()
        if not compact or len(compact) > 500:
            return False
        return any(separator in compact for separator in ("：", ":", "＝", "="))

    def _field_groups(self) -> list[FieldGroup]:
        groups: list[FieldGroup] = []
        pending: list[IndexedRecord] = []

        def flush() -> None:
            if not pending:
                return
            records = list(pending)
            pending.clear()
            label_refs: list[EvidenceAnchor] = []
            value_refs: list[EvidenceAnchor] = []
            for record in records:
                for unit in record.source_units:
                    separator = next(
                        (item for item in ("：", ":", "＝", "=") if item in unit.text),
                        None,
                    )
                    if separator is None:
                        continue
                    offset = unit.text.index(separator)
                    if offset:
                        label_refs.append(self.ir.anchor(unit.evidence_id, 0, offset))
                    if offset + 1 < len(unit.text):
                        value_refs.append(
                            self.ir.anchor(unit.evidence_id, offset + 1, len(unit.text))
                        )
            groups.append(
                FieldGroup(
                    field_group_id=stable_id(
                        "field-group", [self.ir.analysis_id, [r.record_id for r in records]]
                    ),
                    section_node_id=records[0].section_node_id,
                    record_ids=[record.record_id for record in records],
                    label_refs=label_refs,
                    value_refs=value_refs,
                )
            )

        previous_position: int | None = None
        for record in self.records:
            position = min(self.positions[unit.evidence_id] for unit in record.source_units)
            continues = (
                pending
                and record.kind == "paragraph"
                and record.section_node_id == pending[-1].section_node_id
                and previous_position is not None
                and position == previous_position + 1
                and self._looks_like_field(record.text)
            )
            if not continues:
                flush()
            if record.kind == "paragraph" and self._looks_like_field(record.text):
                pending.append(record)
            previous_position = position
        flush()
        return groups

    def source_text(self, record_id: str, *, include_context: bool = False) -> str:
        record = self.by_id[record_id]
        units = list(record.source_units)
        if include_context:
            units = [*record.header_units, *record.parent_units, *units, *record.note_units]
        return "\n".join(unit.text for unit in self._ordered(units))

    def physical_mention_key(self, evidence_id: str, start: int, end: int) -> tuple[str, int, int]:
        unit = self.ir.unit(evidence_id)
        # A merged cell can appear in several logical rows; source_cell_id keeps
        # those observations attached to one physical mention.
        return (unit.source_cell_id or evidence_id, start, end)
