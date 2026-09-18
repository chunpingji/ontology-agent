"""Physical mention registry and explicitly proven local coreference."""

from __future__ import annotations

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import (
    LocalReferent,
    SemanticDecision,
    SourceMention,
    SourceSpan,
    VersionedRef,
)
from app.services.extraction.ontology_guided.field_bindings import contains
from app.services.extraction.ontology_guided.records import RecordIndex


class MentionRegistry:
    def __init__(self, ir: DocumentIR, records: RecordIndex | None = None):
        self.ir = ir
        self.records = records or RecordIndex(ir)
        self._mentions: dict[tuple[str, int, int], SourceMention] = {}
        self._referents: dict[str, LocalReferent] = {}

    @property
    def mentions(self) -> list[SourceMention]:
        return list(self._mentions.values())

    @property
    def referents(self) -> list[LocalReferent]:
        return list(self._referents.values())

    def register(
        self,
        *,
        evidence_id: str,
        start: int,
        end: int,
        text: str,
        record_view_ref: str,
    ) -> SourceMention:
        anchor = self.ir.anchor(evidence_id, start, end)
        if self.ir.resolve(anchor) != text:
            raise ValueError("mention text does not replay from source")
        key = self.records.physical_mention_key(evidence_id, start, end)
        existing = self._mentions.get(key)
        if existing:
            if record_view_ref not in existing.record_view_refs:
                existing.record_view_refs.append(record_view_ref)
            return existing
        unit = self.ir.unit(evidence_id)
        span = SourceSpan(evidence_id=evidence_id, start=start, end=end, text=text)
        mention = SourceMention(
            mention_id=stable_id(
                "source-mention",
                [self.ir.analysis_id, key, text],
            ),
            analysis_id=self.ir.analysis_id,
            source_cell_id=unit.source_cell_id,
            source_spans=[span],
            text=text,
            record_view_refs=[record_view_ref],
        )
        self._mentions[key] = mention
        return mention

    def create_referent(self, mention_ids: list[str]) -> LocalReferent:
        unique = list(dict.fromkeys(mention_ids))
        known = {mention.mention_id for mention in self._mentions.values()}
        if not unique or any(item not in known for item in unique):
            raise ValueError("referent requires registered mentions")
        identity = [self.ir.analysis_id, sorted(unique)]
        referent = LocalReferent(
            referent_id=stable_id("local-referent", identity),
            mention_refs=unique,
        )
        self._referents[referent.referent_id] = referent
        return referent

    def create_record_referent(
        self, record_view_refs: list[str], *, component_refs: list[EvidenceAnchor],
        composition_decision: SemanticDecision, subject_role_decision: SemanticDecision,
    ) -> LocalReferent:
        """Register proven record composition without inventing a physical mention."""
        if (
            composition_decision.check_kind not in {"referent", "record_composition"}
            or composition_decision.verdict != "supported"
            or subject_role_decision.check_kind != "subject_role"
            or subject_role_decision.verdict != "supported"
            or composition_decision.target_id != subject_role_decision.target_id
            or not subject_role_decision.support_refs
        ):
            raise ValueError("record_referent_requires_composition_and_subject_role_proof")
        views = {view.record_view_id: view for view in self.records.record_views}
        selected = sorted(set(record_view_refs))
        if not selected or not component_refs or any(ref not in views for ref in selected):
            raise ValueError("record_referent_requires_registered_components")
        for anchor in [*component_refs, *composition_decision.support_refs,
                       *subject_role_decision.support_refs]:
            self.ir.resolve(anchor)
        component_refs = [self.ir.anchor(
            anchor.evidence_id, anchor.span_start or 0,
            anchor.span_end if anchor.span_end is not None
            else len(self.ir.unit(anchor.evidence_id).text),
        ) for anchor in component_refs]

        def view_covers(view_id, component):
            view = views[view_id]
            return any(contains(source, component) for source in [
                *view.source_refs, *view.header_refs, *view.note_refs, *view.parent_context_refs,
            ])

        if (
            any(not any(view_covers(view, component) for view in selected)
                for component in component_refs)
            or any(not any(view_covers(view, component) for component in component_refs)
                   for view in selected)
            or any(not any(contains(support, component)
                           for support in composition_decision.support_refs)
                   for component in component_refs)
        ):
            raise ValueError("record_composition_source_coverage_missing")
        components = sorted({
            (anchor.evidence_id, anchor.span_start, anchor.span_end) for anchor in component_refs
        })
        identity = [self.ir.analysis_id, selected, components,
                    composition_decision.decision_id, subject_role_decision.decision_id]
        referent_id = stable_id("record-referent", identity)
        if referent_id in self._referents:
            return self._referents[referent_id]
        referent = LocalReferent(
            referent_id=referent_id, kind="record", mention_refs=[], record_view_refs=selected,
            composition_decision_ref=VersionedRef(id=composition_decision.decision_id, revision=1),
        )
        self._referents[referent_id] = referent
        return referent

    def merge_referents(
        self,
        referent_ids: list[str],
        decision: SemanticDecision,
    ) -> LocalReferent:
        if decision.check_kind != "local_coreference" or decision.verdict != "supported":
            raise ValueError("local referents require a supported coreference decision")
        referents = [self._referents[item] for item in dict.fromkeys(referent_ids)]
        if len(referents) < 2:
            raise ValueError("merge requires at least two local referents")
        mentions = list(
            dict.fromkeys(mention for referent in referents for mention in referent.mention_refs)
        )
        merged = LocalReferent(
            referent_id=stable_id(
                "local-referent", [self.ir.analysis_id, sorted(mentions), decision.decision_id]
            ),
            revision=1,
            mention_refs=mentions,
            coreference_proof_refs=[VersionedRef(id=decision.decision_id, revision=1)],
            alternative_refs=[referent.referent_id for referent in referents],
        )
        self._referents[merged.referent_id] = merged
        return merged
