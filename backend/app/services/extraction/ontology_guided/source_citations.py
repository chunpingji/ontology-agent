"""Pure source-quote parsing and exact anchor replay shared by all runners."""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR


class SpanProposal(EvidenceModel):
    evidence_id: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    text: str = Field(min_length=1)
    context: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def paired_coordinates(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("both source coordinates must be supplied or both omitted")
        if self.start is not None and self.end <= self.start:
            raise ValueError("source coordinates must be a nonempty half-open interval")
        return self


def resolve_source_anchor(
    span: SpanProposal,
    ir: DocumentIR,
    allowed: list[EvidenceAnchor],
    contexts: list[EvidenceAnchor] | None = None,
) -> EvidenceAnchor:
    """Replay one exact quote inside explicitly authorized source intervals."""
    try:
        return _resolve_source_anchor(span, ir, allowed, contexts)
    except ValueError as exc:
        if not hasattr(exc, "quote_details"):
            exc.quote_details = {
                "evidence_id": span.evidence_id,
                "quote": span.text[:240],
                "quote_truncated": len(span.text) > 240,
                "span_start": span.start,
                "span_end": span.end,
            }
        raise


def _resolve_source_anchor(
    span: SpanProposal,
    ir: DocumentIR,
    allowed: list[EvidenceAnchor],
    contexts: list[EvidenceAnchor] | None = None,
) -> EvidenceAnchor:
    if span.context:
        contexts = [
            resolve_source_anchor(
                SpanProposal(evidence_id=span.evidence_id, text=span.context), ir, allowed
            )
        ]
    local_contexts = [item for item in contexts or [] if item.evidence_id == span.evidence_id]
    if local_contexts:
        clipped = []
        length = len(ir.unit(span.evidence_id).text)
        for region in allowed:
            if region.evidence_id != span.evidence_id:
                continue
            for context in local_contexts:
                ir.resolve(context)
                left = max(region.span_start or 0, context.span_start or 0)
                right = min(
                    region.span_end if region.span_end is not None else length,
                    context.span_end if context.span_end is not None else length,
                )
                if left < right:
                    clipped.append(ir.anchor(span.evidence_id, left, right))
        allowed = clipped
    if span.start is None:
        text = ir.unit(span.evidence_id).text
        matches = set()
        for region in allowed:
            if region.evidence_id != span.evidence_id:
                continue
            left = region.span_start or 0
            right = region.span_end if region.span_end is not None else len(text)
            offset = text.find(span.text, left, right)
            while offset >= 0:
                matches.add((offset, offset + len(span.text)))
                if len(matches) > 1:
                    raise ValueError("ambiguous_source_quote")
                offset = text.find(span.text, offset + 1, right)
        if not matches:
            raise ValueError(
                "source_quote_outside_scope" if span.text in text else "source_excerpt_mismatch"
            )
        start, end = next(iter(matches))
        span = span.model_copy(update={"start": start, "end": end})
    anchor = ir.anchor(span.evidence_id, span.start, span.end)
    if ir.resolve(anchor) != span.text:
        raise ValueError("source_excerpt_mismatch")
    if not any(
        region.evidence_id == anchor.evidence_id
        and (region.span_start or 0)
        <= span.start
        < span.end
        <= (region.span_end if region.span_end is not None else len(ir.unit(span.evidence_id).text))
        for region in allowed
    ):
        raise ValueError("scope_violation")
    return anchor


def resolve_source_anchors(
    spans: list[SpanProposal], ir: DocumentIR, allowed: list[EvidenceAnchor]
) -> list[EvidenceAnchor]:
    """Resolve full quotations before using them to disambiguate co-quotes."""
    resolved: dict[int, EvidenceAnchor] = {}
    pending: list[tuple[int, SpanProposal]] = []
    for index, span in enumerate(spans):
        try:
            resolved[index] = resolve_source_anchor(span, ir, allowed)
        except ValueError as exc:
            if str(exc) != "ambiguous_source_quote":
                raise
            pending.append((index, span))
    for index, span in pending:
        resolved[index] = resolve_source_anchor(span, ir, allowed, list(resolved.values()))
    return [resolved[index] for index in range(len(spans))]
