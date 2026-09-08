"""Physical mention registry and explicitly proven local coreference."""

from __future__ import annotations

from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import (
    LocalReferent,
    SemanticDecision,
    SourceMention,
    SourceSpan,
    VersionedRef,
)
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
