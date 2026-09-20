"""Source-bound scalar fields and legal candidate pairs; selection is never proof."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoverySchemaCard,
    compile_record_schema_card,
)

_NUMBER_LABELS = {"编号", "批号", "代码", "代号", "code", "number", "identifier", "id"}
_DOCUMENT_LABELS = {"文档编号", "文件编号", "报告编号", "documentnumber", "reportnumber"}
_CONFIDENTIALITY_LABELS = {"密级", "保密级别", "confidentialitylevel", "securityclassification"}
_IDENTITY_LABELS = {"名称", "产品名称", "物料名称", "样品名称", "设备名称"}
_TEMPORAL_LABELS = {"时间", "日期", "年月", "月份", "date", "time", "month"}
_FIELD = re.compile(r"(?:^|[\n；;。])\s*([^\n:：=＝；;。]{1,40}?)\s*[:：=＝]([^\n；;。！？]*)")
_HEADING_PREFIX = re.compile(r"^\s*\d+(?:\.\d+)*[.、．)]?\s+")


@dataclass(frozen=True)
class AttributeField:
    field_id: str
    record_id: str
    label: str
    value: str
    label_refs: tuple[EvidenceAnchor, ...]
    value_refs: tuple[EvidenceAnchor, ...]
    exclusive_record: bool


def _normalized(text):
    return re.sub(r"[\s_\-:：=＝]+", "", text).casefold()


def _number_label(label):
    return (label in _NUMBER_LABELS or label.endswith(("编号", "批号", "代码", "代号"))
            or label.endswith(("number", "identifier", "code")))


def _known_label(label, known):
    normalized = _normalized(label)
    return normalized in known or _number_label(normalized)


def _trimmed_span(text, start=0, end=None):
    end = len(text) if end is None else end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _exclusive(label, value):
    label = _normalized(label)
    if not value:
        return _number_label(label) or label in _CONFIDENTIALITY_LABELS
    if _number_label(label):
        return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/-]{0,79}", value) is not None
    return (label in _CONFIDENTIALITY_LABELS and len(value) <= 24
            and re.fullmatch(r"[\w \-]+", value) is not None)


def _field(index, record, label_unit, label_span, value_unit=None, value_span=None, *, only=False):
    label_start, label_end = _trimmed_span(label_unit.text, *label_span)
    label = label_unit.text[label_start:label_end]
    label_refs = (index.ir.anchor(label_unit.evidence_id, label_start, label_end),)
    value_refs, value = (), ""
    if value_unit is not None and value_span is not None:
        start, end = _trimmed_span(value_unit.text, *value_span)
        if start < end:
            value = value_unit.text[start:end]
            value_refs = (index.ir.anchor(value_unit.evidence_id, start, end),)
    # The same physical table field can occur in several logical row views.
    identity = [label_refs, value_refs, _normalized(label)]
    return AttributeField(
        field_id=stable_id("attribute-field", identity), record_id=record.record_id,
        label=label, value=value, label_refs=label_refs, value_refs=value_refs,
        exclusive_record=only and _exclusive(label, value),
    )


def extract_attribute_fields(
    index, *, property_labels: Iterable[str], reading_groups=(),
) -> tuple[AttributeField, ...]:
    """Read labelled short fields with exact source spans; do not guess bare identifiers."""
    known = {_normalized(label) for label in property_labels}
    known |= _NUMBER_LABELS | _DOCUMENT_LABELS | _CONFIDENTIALITY_LABELS | _IDENTITY_LABELS
    known |= _TEMPORAL_LABELS
    headings = {}
    for unit in index.ir.evidence_units:
        if unit.kind != "heading" or unit.navigation_role is not None or unit.table_path:
            continue
        start = _HEADING_PREFIX.match(unit.text)
        span = _trimmed_span(unit.text, start.end() if start else 0)
        if _known_label(unit.text[slice(*span)], known):
            headings[unit.section_node_id] = (unit, span)
    found = {}
    for record in index.records:
        record_fields = []
        for unit in record.source_units:
            for match in _FIELD.finditer(unit.text):
                if not _known_label(match.group(1), known) or len(match.group(2).strip()) > 160:
                    continue
                start, end = _trimmed_span(unit.text)
                only = (record.kind != "table_row" and len(record.source_units) == 1
                        and unit.text[start:end] == match.group().strip())
                record_fields.append(_field(
                    index, record, unit, match.span(1), unit, match.span(2), only=only,
                ))
            if not record_fields and _known_label(unit.text.strip(), known):
                record_fields.append(_field(
                    index, record, unit, (0, len(unit.text)),
                    only=record.kind != "table_row" and len(record.source_units) == 1,
                ))
        if record.kind == "table_row":
            for value_unit in record.source_units:
                if len(value_unit.text.strip()) > 160:
                    continue
                for header in record.header_units:
                    if (_known_label(header.text.strip(), known)
                            and index.tables.columns(header) & index.tables.columns(value_unit)):
                        record_fields.append(_field(
                            index, record, header, (0, len(header.text)),
                            value_unit, (0, len(value_unit.text)),
                        ))
        elif not record_fields and record.section_node_id in headings:
            first = index.first_record_by_section.get(record.section_node_id)
            if (first and first.record_id == record.record_id and len(record.source_units) == 1
                    and len(record.text.strip()) <= 160
                    and not re.search(r"[\n。！？；;:：]", record.text)):
                heading, span = headings[record.section_node_id]
                unit = record.source_units[0]
                record_fields.append(_field(
                    index, record, heading, span, unit, (0, len(unit.text)), only=True,
                ))
        for field in record_fields:
            found.setdefault(field.field_id, field)
    for group in reading_groups:
        for link in group.reasons:
            if link.reason_code != "split_field_value":
                continue
            records = [record for record in index.records
                       if record.section_node_id == link.right_section_id]
            if (len(records) != 1 or records[0].record_id not in group.record_ids
                    or records[0].kind != "paragraph" or len(records[0].source_units) != 1):
                continue
            record = records[0]
            value_unit = record.source_units[0]
            if (not value_unit.text.strip() or len(value_unit.text.strip()) > 160
                    or re.search(r"[\n。！？!?；;:：=＝]", value_unit.text)):
                continue
            labels = {}
            for anchor in link.binding_refs:
                if anchor.section_node_id != link.left_section_id:
                    continue
                index.ir.resolve(anchor)
                unit = next(unit for unit in index.ir.evidence_units
                            if unit.evidence_id == anchor.evidence_id)
                if unit.navigation_role is not None or unit.table_path:
                    continue
                start, end = _trimmed_span(unit.text, anchor.span_start, anchor.span_end)
                prefix = _HEADING_PREFIX.match(unit.text[start:end])
                if prefix and unit.kind == "heading":
                    start += prefix.end()
                if start >= end or unit.text[end - 1] not in ":：=＝":
                    continue
                span = _trimmed_span(unit.text, start, end - 1)
                if not _known_label(unit.text[slice(*span)], known):
                    continue
                candidate = _field(
                    index, record, unit, span, value_unit, (0, len(value_unit.text)), only=True,
                )
                labels[candidate.field_id] = candidate
            # Two distinct physical labels cannot silently claim the same value.
            if len(labels) != 1:
                continue
            candidate = next(iter(labels.values()))
            found = {identity: field for identity, field in found.items()
                     if field.value or field.label_refs != candidate.label_refs}
            found.setdefault(candidate.field_id, candidate)
    return tuple(found.values())


def _matches_property(field_label, prop, ontology):
    labels = [prop.label]
    if ontology.lexical_context is not None:
        labels.extend(term.text for term in ontology.lexical_context.annotations.get(prop.iri, []))
    names = {_normalized(label) for label in labels}
    local = prop.iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold()
    normalized = _normalized(field_label)
    if normalized in names:
        return True
    if normalized in _NUMBER_LABELS:
        return any(_number_label(name) for name in names | {local})
    if normalized in _DOCUMENT_LABELS:
        return bool(names & _DOCUMENT_LABELS) or local in _DOCUMENT_LABELS
    if normalized in _CONFIDENTIALITY_LABELS:
        return bool(names & _CONFIDENTIALITY_LABELS) or local in _CONFIDENTIALITY_LABELS
    if normalized in _TEMPORAL_LABELS:
        # Calendar syntax admits possible slots, never decides the business meaning.
        return any(iri in {"http://www.w3.org/2001/XMLSchema#" + name
                          for name in ("date", "dateTime", "gYearMonth", "gYear")}
                   for iri in prop.datatype_iris)
    return False


def compile_attribute_card(field, ontology, class_iris, analysis_scope_ref, profile):
    """Retain legal properties matching the field label, with their owning type constraints."""
    classes = list(dict.fromkeys(class_iris))
    if not classes:
        raise ValueError("attribute_card_requires_allowed_class")
    complete = compile_record_schema_card(
        ontology, class_iris=classes, analysis_scope_ref=analysis_scope_ref, profile=profile,
    )
    cards = []
    for card in complete.class_cards:
        properties = [prop for prop in card.properties
                      if _matches_property(field.label, prop, ontology)]
        if not properties:
            continue
        iris = {prop.iri for prop in properties}
        cards.append(card.model_copy(update={
            "properties": properties,
            "quantity_policies": [p for p in card.quantity_policies if p.predicate_iri in iris],
            "identity_keys": [key for key in card.identity_keys if set(key.property_iris) <= iris],
            "unsupported_constraints": [issue for issue in card.unsupported_constraints
                                        if issue.predicate_iri in iris],
        }))
    if not cards:
        cards = [complete.for_class(classes[0]).model_copy(update={
            "properties": [], "quantity_policies": [], "identity_keys": [],
            "unsupported_constraints": [],
        })]
    payload = dict(ontology_snapshot_id=complete.ontology_snapshot_id,
                   analysis_scope_ref=analysis_scope_ref, class_cards=cards)
    return RecordDiscoverySchemaCard(schema_card_id=evidence_hash(payload), **payload)


def field_payload(field):
    return {
        "field_id": field.field_id, "record_id": field.record_id,
        "label": field.label, "value": field.value,
        "label_refs": [ref.model_dump(mode="json") for ref in field.label_refs],
        "value_refs": [ref.model_dump(mode="json") for ref in field.value_refs],
        "exclusive_record": field.exclusive_record,
    }


def attribute_options(
    field, card, index, entities, *, context_record_ids=(), max_candidates=8,
    reference_resolutions=(),
):
    """Return candidates, never proof; the caller validates binding scope and dependencies."""
    record = index.by_id[field.record_id]
    allowed_records = {field.record_id, *context_record_ids}
    by_class = {item.class_iri: item.properties for item in card.class_cards}
    options, relevant, bindings = {}, {}, {}
    aliases = {}
    for resolution in reference_resolutions:
        if resolution.get("binding_ref") and resolution.get("entity_ref"):
            aliases.setdefault(canonical_json(resolution["entity_ref"]), []).append(resolution)

    def local_sources(anchors, *, root=False):
        sources = []
        for anchor in anchors:
            try:
                anchor = EvidenceAnchor.model_validate(anchor)
                index.ir.resolve(anchor)
            except ValueError:
                continue
            owners = index.records_by_evidence.get(anchor.evidence_id, [])
            if (root or anchor.section_node_id == record.section_node_id
                    or any(owner.record_id in allowed_records for owner in owners)):
                sources.append(anchor.model_dump(mode="json"))
        return sources

    for entity in entities:
        properties = by_class.get(entity.class_iri, [])
        if not properties:
            continue
        reference = entity.entity_ref.model_dump(mode="json")
        identity = canonical_json(reference)
        sources = local_sources(entity.source_refs, root=entity.grounding_kind == "document_root")
        for resolution in aliases.get(identity, []):
            alias_sources = local_sources(resolution.get("source_refs", []))
            if alias_sources:
                sources.extend(alias_sources)
                binding = resolution["binding_ref"]
                bindings.setdefault(identity, {})[canonical_json(binding)] = binding
        if not sources and entity.grounding_kind != "document_root":
            continue
        relevant.setdefault(identity, {})
        relevant[identity].update({canonical_json(source): source for source in sources})
        for prop in properties:
            option = dict(subject_ref=reference, class_iri=entity.class_iri, predicate_iri=prop.iri)
            options[canonical_json(option)] = option
    ordered = [options[key] for key in sorted(options)]
    reason = (
        "attribute_value_missing" if not field.value else
        "ontology_property_missing" if not any(by_class.values()) else
        "attribute_subject_missing" if not ordered else
        "attribute_candidate_capacity" if len(ordered) > max_candidates else None
    )
    signature = evidence_hash({
        "field": field_payload(field), "options": ordered,
        "relevant_sources": {key: [sources[anchor] for anchor in sorted(sources)]
                             for key, sources in sorted(relevant.items())},
        **({"reference_bindings": {
            key: [refs[ref] for ref in sorted(refs)] for key, refs in sorted(bindings.items())
        }} if bindings else {}),
    })
    return ordered, reason, signature
