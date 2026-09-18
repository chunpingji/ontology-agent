"""Carry physical coordinates through deterministic Finder value operations."""

from copy import deepcopy


class LocatedText(str):
    def __new__(cls, value, source_ref, *, computed=False):
        obj = super().__new__(cls, value)
        obj.source_ref, obj.computed = source_ref, computed
        return obj

    def __getitem__(self, key):
        value = super().__getitem__(key)
        ref = deepcopy(self.source_ref)
        if isinstance(key, slice) and key.step in (None, 1) and ref.get("kind") == "paragraph":
            start, stop, _ = key.indices(len(self))
            offset = ref.get("span_start", 0)
            ref.update(span_start=offset + start, span_end=offset + stop)
        return LocatedText(value, ref, computed=self.computed)

    def strip(self, chars=None):
        start = len(self) - len(super().lstrip(chars))
        end = len(super().rstrip(chars))
        return self[start : max(start, end)]


def located_computed(value, inputs):
    refs = [item.source_ref for item in inputs if hasattr(item, "source_ref")]
    return LocatedText(value, {"kind": "multiple", "refs": refs}, computed=True)


def locate_structure(structure):
    """Private working copy; never mutate the structure used to render the original."""
    result = deepcopy(structure)
    paragraphs = {}
    for section in result.sections:
        for offset, text in enumerate(section.paras):
            if offset < len(section.para_indices):
                index = section.para_indices[offset]
                value = LocatedText(text, {"kind": "paragraph", "paragraph_index": index})
                section.paras[offset] = value
                paragraphs[index] = value
        section.heading = LocatedText(
            section.heading,
            {
                "kind": "section",
                "heading_index": section.heading_index,
            },
        )
    result.paragraphs = [paragraphs.get(i, text) for i, text in enumerate(result.paragraphs)]
    for table in result.tables:

        def cell(value, row, column):
            return LocatedText(
                value,
                {
                    "kind": "table_cell",
                    "table_path": table.table_path,
                    "row": row,
                    "column": column,
                },
            )

        table.cells = [
            [cell(v, ri, ci) for ci, v in enumerate(row)] for ri, row in enumerate(table.cells)
        ]
        for ri, row in enumerate(table.rows):
            raw_row = (
                table.row_indices[ri]
                if ri < len(table.row_indices)
                else table.header_row_count + ri
            )
            for key, value in row.items():
                row[key] = cell(value, raw_row, table.headers.index(key))
    return result


def _tables(tables):
    for table in tables:
        yield table
        for cell in table["source_cells"]:
            for block in cell["blocks"]:
                if block["kind"] == "table":
                    yield from _tables([block["table"]])


def source_for(ir, ref, *, computed=False, raw_value=None):
    """Resolve recorded coordinates only. Never search the document for a result value."""
    anchors = []
    label = "计算/拼接值" if computed else "原文"
    if isinstance(ref, str):
        label = ref
    elif isinstance(ref, dict):
        if ref.get("kind") == "multiple":
            for part in ref.get("refs", []):
                anchors.extend(source_for(ir, part)["anchors"])
        else:
            path = ref.get("table_path")
            if path is None and ref.get("table") is not None:
                path = [f"table:{ref['table']}"]
            source_cell = None
            if path and ref.get("row") is not None and ref.get("column") is not None:
                table = next((t for t in _tables(ir.tables) if t["table_path"] == path), None)
                if table:
                    try:
                        source_cell = table["grid"][ref["row"]][ref["column"]]
                    except (IndexError, TypeError):
                        pass
                if source_cell is None:
                    return {
                        "kind": "computed" if computed else "document",
                        "label": "未定位",
                        "anchors": [],
                        "raw_value": raw_value,
                    }
            paragraph = ref.get("paragraph_index")
            if paragraph is None and ref.get("kind") == "section":
                paragraph = ref.get("heading_index")
            for unit in ir.evidence_units:
                if path:
                    matches = unit.table_path == path and (
                        unit.source_cell_id == source_cell if source_cell else True
                    )
                    if ref.get("row") is not None and ref.get("column") is None:
                        matches = matches and unit.row_index == ref["row"]
                else:
                    matches = (
                        paragraph is not None
                        and unit.table_path is None
                        and (unit.paragraph_index == paragraph)
                    )
                if not matches or not unit.text:
                    continue
                start, end = None, None
                if not path and ref.get("span_start") is not None:
                    start = max(0, ref["span_start"] - unit.paragraph_offset)
                    end = min(len(unit.text), ref["span_end"] - unit.paragraph_offset)
                    if start >= end:
                        continue
                anchor = ir.anchor(unit.evidence_id, start, end)
                ir.resolve(anchor)
                anchors.append(anchor.model_dump(mode="json"))
    unique = {str(anchor): anchor for anchor in anchors}
    return {
        "kind": "computed" if computed else "document",
        "label": label if anchors else f"{label} · 未定位",
        "anchors": list(unique.values()),
        "raw_value": raw_value,
    }


def attach_sources(ir, relationships):
    counts = {"nodes": 0, "properties": 0, "located_properties": 0}

    def walk(nodes):
        for node in nodes:
            counts["nodes"] += 1
            node["source"] = source_for(ir, node.get("source_ref"))
            for prop in node.get("object_data_properties", []):
                counts["properties"] += 1
                prop["source"] = source_for(
                    ir,
                    prop.get("source_ref"),
                    computed=prop.pop("computed", False),
                    raw_value=prop.get("raw_value"),
                )
                counts["located_properties"] += bool(prop["source"]["anchors"])
            walk(node.get("sub_relationships", []))

    walk(relationships)
    return counts
