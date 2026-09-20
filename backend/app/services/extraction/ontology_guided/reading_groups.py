"""Bounded joint reading of explicit field structures, without changing source identity."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import stable_id

if TYPE_CHECKING:
    from app.services.extraction.document_ir import EvidenceUnit
    from app.services.extraction.ontology_guided.records import IndexedRecord, RecordIndex


@dataclass(frozen=True)
class ReadingGroupLink:
    left_section_id: str
    right_section_id: str
    reason_code: Literal["field_label_values", "split_field_value", "explicit_continuation"]
    binding_refs: tuple[EvidenceAnchor, ...]


@dataclass(frozen=True)
class ReadingGroup:
    group_id: str
    record_ids: tuple[str, ...]
    section_node_ids: tuple[str, ...]
    binding_refs: tuple[EvidenceAnchor, ...]
    reasons: tuple[ReadingGroupLink, ...]


@dataclass(frozen=True)
class _Section:
    node_id: str
    parent_id: str | None
    headings: tuple[EvidenceUnit, ...]
    records: tuple[IndexedRecord, ...]
    leaf: bool
    has_table: bool

    @property
    def title(self) -> str:
        return " ".join(unit.text for unit in self.headings).strip()

    @property
    def text(self) -> str:
        return "\n".join([self.title, *(record.text for record in self.records)])


# These are reasons to keep scopes apart, never positive evidence of continuity.
_SCOPE_BOUNDARY = re.compile(
    r"样品|样本|批次|批号|物料|"
    r"条件|除非|如果|若|仅(?:当|在|限)|否则|不(?:适用|应|得|能|含|满足|符合)|"
    r"未(?:检出|检测|进行|完成|批准|发现|满足|获得)|无(?:需|效|法)|阴性|"
    r"(?:^|[:：;；\s])(?:无|否)(?:$|[\s。；;])|"
    r"(?:[<>≤≥=]|高于|低于|超过|达到)[^。；;\n]{0,24}时|"
    r"(?:\d|℃|°)[^。；;\n]{0,8}(?:下|时)|"
    r"\b(?:sample|batch|lot|material\s+(?:name|number|code)|"
    r"if|unless|except|without|not|negative|conditional)\b",
    re.IGNORECASE,
)
_SECTION_NUMBER = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*[.、．]?|[一二三四五六七八九十百]+[、．.]|"
    r"[（(][一二三四五六七八九十\d]+[）)])\s*)"
)
_CONTINUATION = re.compile(
    r"^(?:续上(?:文|节|段)|接上(?:文|节|段)|续文|续段|continued|continuation)"
    r"(?:$|[\s:：，,。；;])", re.IGNORECASE,
)
_CONTINUATION_SUFFIX = re.compile(r"\s*[（(](?:续|continued)[）)]\s*$", re.IGNORECASE)


def _label(text: str) -> str:
    return _SECTION_NUMBER.sub("", text.strip()).strip().rstrip("：:=＝").strip().casefold()


def _heading_refs(index: RecordIndex, section_ids: Iterable[str], max_ancestors: int):
    selected = set()
    for section_id in section_ids:
        visited = set()
        current = section_id
        for _ in range(max_ancestors + 1):
            if current is None or current in visited:
                break
            selected.add(current)
            visited.add(current)
            current = index.nodes_by_id.get(current, {}).get("parent_id")
    return tuple(
        index.ir.anchor(unit.evidence_id, 0, len(unit.text))
        for unit in index.ir.evidence_units
        if unit.kind == "heading" and not unit.table_path and unit.text
        and unit.section_node_id in selected and not getattr(unit, "navigation_role", None)
    )


def heading_context(
    index: RecordIndex, record_id: str, *, max_ancestors: int = 4,
) -> tuple[EvidenceAnchor, ...]:
    """Return own/ancestor headings as binding anchors; never synthesize filename evidence."""
    if max_ancestors < 0:
        raise ValueError("reading_group_ancestor_limit_invalid")
    return _heading_refs(index, [index.by_id[record_id].section_node_id], max_ancestors)


def _sections(index: RecordIndex) -> list[_Section]:
    headings = defaultdict(list)
    records = defaultdict(list)
    tables = set()
    for unit in index.ir.evidence_units:
        if unit.kind == "heading" and not unit.table_path:
            headings[unit.section_node_id].append(unit)
        if unit.table_path:
            tables.add(unit.section_node_id)
    for record in index.records:
        records[record.section_node_id].append(record)
    parents = {node.get("parent_id") for node in index.ir.nodes}
    # Include ineligible headings as separators: no joining across a table,
    # directory, long section, or intervening child section.
    return [
        _Section(
            node_id=node_id, parent_id=index.nodes_by_id.get(node_id, {}).get("parent_id"),
            headings=tuple(units), records=tuple(records[node_id]), leaf=node_id not in parents,
            has_table=node_id in tables,
        )
        for node_id, units in sorted(
            headings.items(), key=lambda item: index.positions[item[1][0].evidence_id],
        )
    ]


def _eligible(section: _Section, max_section_chars: int) -> bool:
    return bool(
        section.leaf and not section.has_table and section.parent_id is not None
        and all(not getattr(unit, "navigation_role", None) for unit in section.headings)
        and all(record.kind == "paragraph" for record in section.records)
        and len(section.text) <= max_section_chars
        and not _SCOPE_BOUNDARY.search(section.text)
    )


def _field_value(section: _Section, labels: set[str]) -> bool:
    return bool(
        _label(section.title) in labels and section.records
        and all(record.text.strip() and not re.search(r"[。！？!?]", record.text)
                for record in section.records)
    )


def _isolated_label(section: _Section, labels: set[str]) -> bool:
    text = "\n".join(record.text for record in section.records).strip() or section.title
    return bool(
        text.endswith(("：", ":", "＝", "=")) and _label(text) in labels
    )


def _link(index: RecordIndex, left: _Section, right: _Section, labels: set[str]):
    if left.parent_id != right.parent_id:
        return None
    code = None
    if (_field_value(left, labels) and _field_value(right, labels)
            and _label(left.title) != _label(right.title)):
        code = "field_label_values"
    elif (_isolated_label(left, labels) and right.records
          and _label(right.title) in {"值", "数值", "内容", "value"}
          and all(not re.search(r"[。！？!?：:=＝]", record.text) for record in right.records)):
        code = "split_field_value"
    else:
        title = _SECTION_NUMBER.sub("", right.title).strip()
        continued_title = _CONTINUATION_SUFFIX.sub("", title)
        first_text = right.records[0].text.strip() if right.records else ""
        if (left.records and right.records and (
            _CONTINUATION.match(title) or _CONTINUATION.match(first_text)
            or (continued_title != title and _label(continued_title) == _label(left.title))
        )):
            code = "explicit_continuation"
    if code is None:
        return None
    anchors = tuple(index.ir.anchor(unit.evidence_id, 0, len(unit.text))
                    for unit in (*left.headings, *right.headings) if unit.text)
    if code == "split_field_value":
        anchors += tuple(index.ir.anchor(unit.evidence_id, 0, len(unit.text))
                         for record in left.records for unit in record.source_units if unit.text)
    elif code == "explicit_continuation":
        anchors += tuple(index.ir.anchor(unit.evidence_id, 0, len(unit.text))
                         for unit in right.records[0].source_units if unit.text)
    return ReadingGroupLink(left.node_id, right.node_id, code, anchors)


def build_reading_groups(
    index: RecordIndex, *, field_labels: Iterable[str] = (), max_section_chars: int = 240,
    max_group_chars: int = 1200, max_records: int = 8,
) -> tuple[ReadingGroup, ...]:
    """Jointly read explicit adjacent fields/continuations; no entity or permission merge.

    Labels come from the frozen ontology. Limits bound already justified groups;
    shortness or adjacency alone can never create a group. Groups are disjoint and
    retain all physical record IDs. Ungrouped records keep their ordinary tasks.
    """
    if min(max_section_chars, max_group_chars, max_records) < 1:
        raise ValueError("reading_group_limit_invalid")
    labels = {_label(label) for label in field_labels if _label(label)}
    groups, pending, links = [], [], []

    def flush():
        if links:
            record_ids = tuple(record.record_id for section in pending
                               for record in section.records)
            section_ids = tuple(section.node_id for section in pending)
            groups.append(ReadingGroup(
                group_id=stable_id("reading-group", [
                    index.ir.analysis_id, record_ids,
                    [(link.left_section_id, link.right_section_id, link.reason_code)
                     for link in links],
                ]),
                record_ids=record_ids, section_node_ids=section_ids,
                binding_refs=_heading_refs(index, section_ids, 4), reasons=tuple(links),
            ))
        pending.clear()
        links.clear()

    for section in _sections(index):
        if not _eligible(section, max_section_chars):
            flush()
            continue
        link = _link(index, pending[-1], section, labels) if pending else None
        size_ok = (
            sum(len(item.records) for item in pending) + len(section.records) <= max_records
            and sum(len(item.text) for item in pending) + len(section.text) <= max_group_chars
        )
        repeated_field = (
            link is not None and link.reason_code == "field_label_values"
            and any(_label(item.title) == _label(section.title) for item in pending)
        )
        if pending and (link is None or not size_ok or repeated_field):
            flush()
            link = None
        if link is not None:
            links.append(link)
        pending.append(section)
    flush()
    return tuple(groups)
