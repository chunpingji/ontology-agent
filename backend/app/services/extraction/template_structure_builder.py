"""Offline template skeletons; semantic models may enrich but never invent structure."""

from pydantic import model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR, EvidenceUnit
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.text_scanner import key_value_spans


class TemplateOrigin(EvidenceModel):
    document_hash: str
    parser_version: str
    structure_hash: str
    evidence_id: str
    label_anchor: EvidenceAnchor
    value_anchor: EvidenceAnchor | None = None

    @model_validator(mode="after")
    def coherent_identity(self):
        if self.evidence_id != self.label_anchor.evidence_id:
            raise ValueError("origin evidence differs from its label")
        for anchor in (self.label_anchor, self.value_anchor):
            if anchor and (anchor.document_hash, anchor.parser_version, anchor.structure_hash) != (
                self.document_hash,
                self.parser_version,
                self.structure_hash,
            ):
                raise ValueError("origin anchors must share document identity")
        return self


def origin_status(origin: TemplateOrigin | dict | None, ir: DocumentIR | dict | None) -> str:
    if origin is None:
        return "unavailable"
    if ir is None:
        return "invalid"
    try:
        origin = TemplateOrigin.model_validate(origin)
        ir = DocumentIR.model_validate(ir)
        if (origin.document_hash, origin.parser_version, origin.structure_hash) != (
            ir.document_hash,
            ir.parser_version,
            ir.structure_hash,
        ):
            return "invalid"
        if origin.evidence_id != origin.label_anchor.evidence_id:
            return "invalid"
        ir.resolve(origin.label_anchor)
        if origin.value_anchor:
            ir.resolve(origin.value_anchor)
        return "valid"
    except ValueError:
        return "invalid"


def build_template_structure(ir: DocumentIR) -> list[dict]:
    table_widths = {}

    def collect_widths(table):
        table_widths[tuple(table["table_path"])] = max(
            (len(row) for row in table["grid"]), default=0
        )
        for cell in table["source_cells"]:
            for block in cell["blocks"]:
                if block["kind"] == "table":
                    collect_widths(block["table"])

    for table in ir.tables:
        collect_widths(table)

    def origin(unit: EvidenceUnit, label=None, value=None):
        return TemplateOrigin(
            document_hash=ir.document_hash,
            parser_version=ir.parser_version,
            structure_hash=ir.structure_hash,
            evidence_id=unit.evidence_id,
            label_anchor=ir.anchor(unit.evidence_id, *(label or (None, None))),
            value_anchor=(
                ir.anchor(unit.evidence_id, *value) if value and value[0] < value[1] else None
            ),
        ).model_dump(mode="json")

    result = []
    for node in ir.nodes:
        units = [unit for unit in ir.evidence_units if unit.section_node_id == node["node_id"]]
        if node["node_type"] == "document" and not any(u.text.strip() for u in units):
            continue
        heading = next((unit for unit in units if unit.kind == "heading"), None)
        section_id = stable_id("template-section", [ir.structure_hash, node["node_id"]])
        groups: dict[str, dict] = {}
        for unit in units:
            if unit.kind == "heading" or not unit.text.strip():
                continue
            kv = key_value_spans(unit.text)
            # Narrative paragraphs become explicit writing placeholders, not
            # invented business fields. Two-column rows keep separate cell anchors.
            value_unit = None
            if (
                kv is None
                and unit.table_path
                and unit.column_index == 0
                and table_widths.get(tuple(unit.table_path)) == 2
            ):
                value_unit = next(
                    (
                        u
                        for u in units
                        if u.table_path == unit.table_path
                        and u.row_index == unit.row_index
                        and u.column_index == 1
                        and u.paragraph_index == 0
                        and u.text.strip()
                    ),
                    None,
                )
            if kv is None and not (unit.table_path and unit.row_index == 0):
                if unit.table_path and value_unit is None:
                    continue
            group_key = "/".join(unit.table_path) if unit.table_path else "fields"
            if kv is None and not unit.table_path:
                group_key = "narrative"
            if group_key not in groups:
                groups[group_key] = {
                    "id": stable_id("template-group", [section_id, group_key]),
                    "title": (
                        "表格"
                        if unit.table_path
                        else "段落行文"
                        if group_key == "narrative"
                        else "字段"
                    ),
                    "origin": origin(unit),
                    "candidates": [],
                }
            field_origin = origin(unit, kv[0], kv[1]) if kv else origin(unit)
            if value_unit:
                field_origin["value_anchor"] = ir.anchor(value_unit.evidence_id).model_dump(
                    mode="json"
                )
            groups[group_key]["candidates"].append(
                {
                    "id": stable_id("template-slot", [section_id, unit.evidence_id, kv]),
                    "label": (
                        unit.text[slice(*kv[0])]
                        if kv
                        else "段落行文"
                        if group_key == "narrative"
                        else unit.text.strip()
                    ),
                    "evidence_span": unit.text,
                    "evidence_offset": 0,
                    "origin": field_origin,
                }
            )
        result.append(
            {
                "id": section_id,
                "title": node["heading"] or ir.title,
                "parent_id": node["parent_id"],
                "origin": origin(heading) if heading else None,
                "groups": list(groups.values()),
            }
        )
    return result
