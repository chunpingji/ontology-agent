"""Replayable document evidence built only from the shared structural parser."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PrivateAttr, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.docx_structure import DocStructure, ParagraphBlock, TableBlock
from app.services.extraction.evidence_identity import evidence_hash, stable_id

IR_VERSION = "document-ir-v1"
STRUCTURE_POLICY_VERSION = "word-structure-v1"


class EvidenceUnit(EvidenceModel):
    evidence_id: str
    kind: Literal["heading", "paragraph", "cell_paragraph"]
    text: str
    section_node_id: str
    block_id: str
    paragraph_index: int
    fragment_index: int = 0
    table_path: list[str] | None = None
    row_index: int | None = None
    column_index: int | None = None
    physical_page_number: int | None = None
    source_cell_id: str | None = None
    paragraph_offset: int = 0


class DocumentIR(EvidenceModel):
    _unit_index: dict = PrivateAttr(default_factory=dict)
    _unit_index_source: tuple[int, int] | None = PrivateAttr(default=None)
    ir_version: str = IR_VERSION
    structure_policy_version: str = STRUCTURE_POLICY_VERSION
    parser_version: str
    document_hash: str
    original_document_hash: str
    structure_hash: str
    analysis_id: str
    document_role: Literal[
        "analysis_source", "default_source", "template_sample", "training_source", "training_report"
    ] = "analysis_source"
    title: str
    nodes: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    evidence_units: list[EvidenceUnit]
    pagination: dict[str, Any]
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verify_identity(self):
        structural = self.model_dump(mode="json", include={
            "parser_version", "ir_version", "structure_policy_version", "nodes",
            "blocks", "tables", "evidence_units", "pagination",
        })
        if evidence_hash(structural) != self.structure_hash:
            raise ValueError("structure identity does not match analysis content")
        expected = stable_id("analysis", {
            "document_hash": self.document_hash, "structure_hash": self.structure_hash,
            "role": self.document_role, "original_document_hash": self.original_document_hash,
        })
        if self.analysis_id != expected:
            raise ValueError("analysis identity does not match its dependencies")
        ids = [unit.evidence_id for unit in self.evidence_units]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence identity")
        return self

    def unit(self, evidence_id: str) -> EvidenceUnit:
        units = self.evidence_units
        source = (id(units), len(units))
        entry = self._unit_index.get(evidence_id)
        # Also detect in-place replacements/reordering and changed IDs. Cached
        # entries hold the live unit, so text/coordinate edits cannot return a copy.
        if (
            self._unit_index_source != source
            or entry is None
            or units[entry[0]] is not entry[1]
            or entry[1].evidence_id != evidence_id
        ):
            self._unit_index = {unit.evidence_id: (i, unit) for i, unit in enumerate(units)}
            self._unit_index_source = source
            entry = self._unit_index.get(evidence_id)
        if entry is not None:
            return entry[1]
        raise ValueError(f"unknown evidence identity: {evidence_id}")

    def anchor(self, evidence_id: str, start: int | None = None,
               end: int | None = None) -> EvidenceAnchor:
        unit = self.unit(evidence_id)
        if start is not None and (end is None or not 0 <= start < end <= len(unit.text)):
            raise ValueError("span outside source evidence")
        return EvidenceAnchor(
            document_hash=self.document_hash, parser_version=self.parser_version,
            structure_hash=self.structure_hash, evidence_id=evidence_id,
            section_node_id=unit.section_node_id, block_id=unit.block_id,
            paragraph_index=unit.paragraph_index, fragment_index=unit.fragment_index,
            table_path=unit.table_path, row_index=unit.row_index, column_index=unit.column_index,
            physical_page_number=unit.physical_page_number, span_start=start, span_end=end,
        )

    def resolve(self, anchor: EvidenceAnchor | dict) -> str:
        anchor = EvidenceAnchor.model_validate(anchor)
        expected = self.anchor(anchor.evidence_id, anchor.span_start, anchor.span_end)
        if anchor != expected:
            raise ValueError("anchor identity or coordinates do not match this document")
        return self.unit(anchor.evidence_id).text[anchor.span_start:anchor.span_end]


def build_document_ir(file_path: str | Path, structure: DocStructure, *,
                      role: str = "analysis_source", original_path: str | Path | None = None
                      ) -> DocumentIR:
    if any("parse failed" in warning or "unavailable" in warning for warning in structure.warnings):
        raise ValueError("; ".join(structure.warnings))
    document_hash = sha256(Path(file_path).read_bytes()).hexdigest()
    original_hash = sha256(Path(original_path or file_path).read_bytes()).hexdigest()
    units: list[EvidenceUnit] = []
    nodes: list[dict] = []

    def walk_node(node, parent_id=None):
        nodes.append({
            "node_id": node.node_id, "parent_id": parent_id, "heading": node.heading,
            "level": node.level, "heading_index": node.heading_index,
            "direct_block_ids": node.direct_block_ids, "node_type": node.node_type,
        })
        for child in node.children:
            walk_node(child, node.node_id)

    if structure.section_tree is None:
        raise ValueError("parse did not produce a document root")
    walk_node(structure.section_tree)
    # A filename fallback is display metadata, not structure or semantic evidence.
    nodes[0]["heading"] = ""

    def append_unit(**kwargs):
        identity = {"document_hash": document_hash, "parser_version": structure.parser_version,
                    "ir_version": IR_VERSION, **kwargs}
        units.append(EvidenceUnit(evidence_id=stable_id("evidence", identity), **kwargs))

    def walk_table(table: dict, block: TableBlock):
        for cell in table["source_cells"]:
            for item in cell["blocks"]:
                if item["kind"] == "table":
                    walk_table(item["table"], block)
                    continue
                append_unit(
                    kind="cell_paragraph", text=item["text"],
                    section_node_id=block.section_node_id or "document",
                    block_id=block.block_id, paragraph_index=item["paragraph_index"],
                    table_path=table["table_path"], row_index=cell["row_index"],
                    column_index=cell["column_index"], source_cell_id=cell["cell_id"],
                    physical_page_number=block.physical_page_number,
                )

    tables = [asdict(table) for table in structure.tables]
    offsets: dict[int, int] = {}
    for block in structure.blocks:
        if isinstance(block, ParagraphBlock):
            append_unit(
                kind="heading" if block.heading_level else "paragraph", text=block.text,
                section_node_id=block.section_node_id or "document", block_id=block.block_id,
                paragraph_index=block.paragraph_index, fragment_index=block.fragment_index,
                paragraph_offset=offsets.get(block.paragraph_index, 0),
                physical_page_number=block.physical_page_number,
            )
            offsets[block.paragraph_index] = offsets.get(block.paragraph_index, 0) + len(block.text)
        elif isinstance(block, TableBlock):
            walk_table(tables[block.table_index], block)
    structural = {
        "parser_version": str(structure.parser_version), "ir_version": IR_VERSION,
        "structure_policy_version": STRUCTURE_POLICY_VERSION, "nodes": nodes,
        "blocks": [asdict(block) for block in structure.blocks], "tables": tables,
        "evidence_units": [unit.model_dump(mode="json") for unit in units],
        "pagination": asdict(structure.pagination),
    }
    structure_hash = evidence_hash(structural)
    analysis_id = stable_id("analysis", {
        "document_hash": document_hash, "structure_hash": structure_hash, "role": role,
        "original_document_hash": original_hash,
    })
    return DocumentIR(
        **structural, document_hash=document_hash, original_document_hash=original_hash,
        structure_hash=structure_hash, analysis_id=analysis_id, document_role=role,
        title=structure.title, diagnostics=list(structure.warnings),
    )


@dataclass(frozen=True)
class OffsetMap:
    text: str
    pieces: tuple[tuple[int, int, str, int], ...]
    ir: DocumentIR

    @classmethod
    def join(cls, ir: DocumentIR, evidence_ids: list[str], separator: str = "\n") -> OffsetMap:
        parts, pieces, cursor = [], [], 0
        for evidence_id in evidence_ids:
            if parts:
                parts.append(separator)
                cursor += len(separator)
            value = ir.unit(evidence_id).text
            pieces.append((cursor, cursor + len(value), evidence_id, 0))
            parts.append(value)
            cursor += len(value)
        return cls("".join(parts), tuple(pieces), ir)

    def source_anchor(self, start: int, end: int) -> EvidenceAnchor:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("span outside synthetic text")
        for left, right, evidence_id, source_offset in self.pieces:
            if left <= start < end <= right:
                return self.ir.anchor(evidence_id, start - left + source_offset,
                                      end - left + source_offset)
        raise ValueError("span crosses a separator or multiple source units")
