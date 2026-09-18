"""Offline template skeletons; semantic models may enrich but never invent structure."""

import re

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


def _business_label(text: str) -> str:
    text = " ".join(text.replace("\u200b", "").split()).strip(" ：:*")
    # Prefer the Chinese title in bilingual labels; retain short acronyms such as QA.
    bilingual = re.match(r"^([A-Za-z][A-Za-z /ⅠⅡⅢⅣ]*)\s+([\u4e00-\u9fff].*)$", text)
    if bilingual and len(bilingual[1].strip()) > 4:
        text = bilingual[2]
    return text


def _generic_title(text: str) -> bool:
    return not text or text in {"表格", "字段", "段落行文", "未命名分组"} or bool(
        re.fullmatch(r"(?:SECTION\s*)?[ⅠⅡⅢⅣIVX\d]*\s*部分\s*[一二三四五六七八九十\d]+", text, re.I)
    )


def _caption(text: str) -> str | None:
    text = _business_label(text)
    workshop = re.search(r"([A-Za-z0-9-]+)\s*车间\s*设备(?:见下表|如下表|清单|表)[：:]?$", text)
    if workshop:
        return f"{workshop[1]} 车间设备表"
    if _generic_title(text) or len(text) > 48 or re.search(r"[。；;！？!?：:]", text):
        return None
    if re.search(r"(?:表|清单|一览|描述|信息|情况|要求|说明|计划|记录)$", text):
        return text
    return None


def _table_titles(ir: DocumentIR) -> dict[tuple, str]:
    titles = {}

    def visit(table, preceding=""):
        path = tuple(table["table_path"])
        caption = _caption(preceding)
        if caption:
            titles[path] = caption
        width = max((len(row) for row in table["grid"]), default=0)
        for cell in table["source_cells"]:
            previous = ""
            for block in cell["blocks"]:
                if block["kind"] == "table":
                    # A nested table consumes the nearest paragraph in its own cell.
                    visit(block["table"], previous)
                    previous = ""
                    continue
                text = block.get("text", "").strip()
                if not text:
                    continue
                if (path not in titles and cell["row_index"] < 2
                        and cell["column_span"] == width and not key_value_spans(text)):
                    caption = _caption(text)
                    if caption:
                        titles[path] = caption
                previous = text

    previous, previous_section = "", None
    for block in ir.blocks:
        section = block.get("section_node_id")
        if section != previous_section:
            previous = ""
        previous_section = section
        if block.get("table_index") is not None:
            visit(ir.tables[block["table_index"]], previous)
            previous = ""
        elif block.get("text", "").strip():
            previous = block["text"]
    return titles


def template_structure_titles(schema: dict, analysis: dict | None) -> dict[str, str]:
    """Read-only authoring defaults; never rewrite a stored revision or its hash."""
    if schema.get("schema_version") != 2 or not analysis:
        return {}
    try:
        sections = build_template_structure(DocumentIR.model_validate(analysis))
    except ValueError:
        return {}
    return {g["id"]: g["title"] for s in sections for g in s["groups"]
            if not _generic_title(g["title"])}


def build_template_structure(ir: DocumentIR) -> list[dict]:
    table_widths = {}
    table_titles = _table_titles(ir)

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
                        table_titles.get(tuple(unit.table_path), "表格")
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
        for group in groups.values():
            if group["title"] == "表格":
                labels = list(dict.fromkeys(
                    _business_label(c["label"]) for c in group["candidates"]
                    if not _generic_title(_business_label(c["label"]))
                    and len(_business_label(c["label"])) <= 24
                ))
                if labels:
                    group["title"] = "、".join(labels[:3])
            elif group["title"] == "段落行文" and any(
                c["evidence_span"].lstrip().startswith("*") for c in group["candidates"]
            ):
                group["title"] = "附注说明"
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
