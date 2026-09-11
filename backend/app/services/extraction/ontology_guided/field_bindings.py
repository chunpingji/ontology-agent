"""IR-derived role candidates. Structure locates evidence; it never proves a fact."""

from __future__ import annotations

import re

from pydantic import Field

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.evidence_identity import stable_id

FIELD_BINDING_VERSION = "ir-field-bindings-v1"
OWNER_BINDING_VERSION = "source-owned-binding-v2"


class FieldBinding(EvidenceModel):
    field_binding_id: str
    record_view_ref: str
    kind: str
    target_value_refs: list[EvidenceAnchor]
    label_refs: list[EvidenceAnchor]
    owner_candidate_refs: list[EvidenceAnchor] = Field(default_factory=list)
    scope_refs: list[EvidenceAnchor] = Field(default_factory=list)
    table_path: list[str] | None = None
    row_index: int | None = None
    column_indices: list[int] = Field(default_factory=list)
    source_cell_id: str | None = None
    row_span: int = 1
    column_span: int = 1
    mapping_status: str = "structural_candidate"


def field_bindings(index, record_id: str) -> list[FieldBinding]:
    def anchor(identity):
        unit = index.ir.unit(identity)
        return index.ir.anchor(identity, 0, len(unit.text))

    record = index.by_id[record_id]
    view = index.record_views_by_id[record_id]
    values = []
    if record.table_path:
        for unit in record.source_units:
            headers = [
                h
                for h in record.header_units
                if index.tables.columns(h) & index.tables.columns(unit)
            ]
            cell = index.tables.cells.get(unit.source_cell_id, {})
            values.append(
                dict(
                    kind="table",
                    target_value_refs=[anchor(unit.evidence_id)],
                    label_refs=[anchor(h.evidence_id) for h in headers],
                    owner_candidate_refs=[
                        anchor(u.evidence_id)
                        for u in record.source_units
                        if u.source_cell_id != unit.source_cell_id
                    ],
                    scope_refs=[
                        anchor(u.evidence_id) for u in (*record.note_units, *record.parent_units)
                    ],
                    table_path=list(record.table_path),
                    row_index=record.row_index,
                    column_indices=sorted(index.tables.columns(unit)),
                    source_cell_id=unit.source_cell_id,
                    row_span=cell.get("row_span", 1),
                    column_span=cell.get("column_span", 1),
                    mapping_status="structural_candidate" if headers else "incomplete",
                )
            )
    else:
        groups = index.field_groups_by_record.get(record_id, [])
        for group in groups:
            for unit in record.source_units:
                labels = [r for r in group.label_refs if r.evidence_id == unit.evidence_id]
                targets = [r for r in group.value_refs if r.evidence_id == unit.evidence_id]
                if labels and targets:
                    values.append(
                        dict(
                            kind="field_group",
                            target_value_refs=targets,
                            label_refs=labels,
                            owner_candidate_refs=[
                                anchor(r.evidence_id)
                                for r in group.value_refs
                                if r.evidence_id != unit.evidence_id
                            ],
                        )
                    )
    return [
        FieldBinding(
            field_binding_id=stable_id("field-binding", [FIELD_BINDING_VERSION, record_id, value]),
            record_view_ref=view.record_view_id,
            **value,
        )
        for value in values
    ]


def contains(outer: EvidenceAnchor, inner: EvidenceAnchor) -> bool:
    return (
        outer.model_copy(update={"span_start": inner.span_start, "span_end": inner.span_end})
        == inner
        and (outer.span_start or 0) <= (inner.span_start or 0)
        and (
            outer.span_end is None
            or inner.span_end is not None
            and outer.span_end >= inner.span_end
        )
    )


def validate_local_owner(context, binding, original_refs, local_refs, bridge_refs, bridge_kind):
    """Two cited names do not establish identity across rows or tables.

    Accept the already established physical owner in this row/field group, or
    an explicitly assembled reference chain. Ordinary context and similarity
    cannot supply such a chain. Unknown cross-record identity stays unresolved.
    """
    originals = context.subject_evidence_refs
    if not originals or not all(
        any(contains(ref, owner) for ref in original_refs) for owner in originals
    ):
        return "owner_original_source_missing"
    local_sources = [f.anchor for f in context.fragments if f.fact_eligible]
    if not local_refs or not all(
        any(contains(source, ref) for source in local_sources) for ref in local_refs
    ):
        return "owner_field_source_missing"
    if binding is not None and binding.kind == "table":
        row_sources = [*binding.target_value_refs, *binding.owner_candidate_refs]
        established = [owner for owner in originals
                       if any(contains(source, owner) for source in row_sources)]
        if established:
            return None if any(
                contains(ref, owner) for ref in local_refs for owner in established
            ) else "owner_field_source_missing"
    elif any(
        contains(owner_field, owner)
        for owner_field in context.owner_field_refs for owner in originals
    ):
        # The IR field group has the exact already established owner, not just
        # another field with the same spelling. The value is a fact in that group.
        return None
    elif any(
        contains(source, owner) and any(contains(ref, owner) for ref in local_refs)
        for source in local_sources for owner in originals
    ):
        return None
    if (binding is None and not context.owner_field_refs
            and not any(owner.table_path for owner in originals)
            and bridge_kind == "explicit_assertion"):
        # A full narrative assertion explicitly naming its subject remains
        # eligible for the independent semantic coreference check. This does
        # not apply to cells/field groups or two isolated repeated names.
        if any(
            fragment.fact_eligible and not fragment.anchor.table_path
            and context.subject_label and context.subject_label in fragment.text
            and fragment.text.strip() != context.subject_label.strip()
            and any(contains(fragment.anchor, local) and contains(bridge, local)
                    for local in local_refs for bridge in bridge_refs)
            for fragment in context.fragments
        ):
            return None
    chains = [f.anchor for f in context.fragments if f.purpose == "resolved_reference_chain"]
    if bridge_kind == "resolved_reference_chain" and chains and all(
        any(contains(ref, source) for ref in bridge_refs) for source in chains
    ):
        return None
    return "owner_identity_unproven"


def validate_field_binding(context, predicate, endpoint, binding_id, role_refs, bridge):
    """Reject incorrect physical roles even when all model verdicts say supported."""
    matching = [
        b
        for b in context.field_bindings
        if any(contains(ref, endpoint) for ref in b.target_value_refs)
    ]
    selected = next((b for b in matching if b.field_binding_id == binding_id), None)
    if bridge in {"role_mapped_table", "owned_field_group"} or matching:
        if selected is None:
            return "field_binding_missing"
        if (
            bridge == "role_mapped_table"
            and selected.kind != "table"
            or bridge == "owned_field_group"
            and selected.kind != "field_group"
        ):
            return "field_binding_kind_mismatch"
        if selected.mapping_status != "structural_candidate" or not selected.label_refs:
            return "field_binding_ambiguous"
        if not all(any(contains(r, label) for r in role_refs) for label in selected.label_refs):
            return "field_role_source_missing"
        # When this very record supplies an exact ontology label, a different
        # column cannot be substituted. Unknown synonyms still need model proof.
        texts = {f.anchor.evidence_id: f for f in context.fragments}

        def label_text(ref):
            fragment = texts[ref.evidence_id]
            start = (ref.span_start or 0) - (fragment.anchor.span_start or 0)
            end = (ref.span_end or len(fragment.text)) - (fragment.anchor.span_start or 0)
            return re.sub(r"\s+", "", fragment.text[start:end]).strip("：:")

        expected = [
            b
            for b in context.field_bindings
            if any(label_text(r) == re.sub(r"\s+", "", predicate.label) for r in b.label_refs)
        ]
        if expected and selected not in expected:
            return "field_column_mismatch"
    elif binding_id:
        return "field_binding_outside_target"
    if not role_refs:
        return "field_role_source_missing"
    return None
