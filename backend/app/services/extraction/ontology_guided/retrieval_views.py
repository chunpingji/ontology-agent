"""Complete, role-preserving retrieval copies of the existing record index."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import MetadataSnapshot
from app.services.extraction.ontology_guided.records import RecordIndex

VIEW_POLICY_VERSION = "complete-record-view-v1"


class RetrievalView(EvidenceModel):
    record_id: str
    retrieval_view_hash: str
    source_snapshot_hash: str
    model_text: str
    structure_text: str
    metadata_text: str = ""
    source_refs: list[EvidenceAnchor]
    binding_refs: list[EvidenceAnchor] = Field(default_factory=list)
    omitted_refs: list[EvidenceAnchor] = Field(default_factory=list)
    source_cell_ids: list[str] = Field(default_factory=list)
    logical_row_id: str | None = None
    token_count: int = Field(ge=0)
    status: Literal["complete", "not_rerankable"] = "complete"
    view_policy_version: str = VIEW_POLICY_VERSION
    authority: Literal["retrieval_only"] = "retrieval_only"


def build_retrieval_views(
    index: RecordIndex,
    metadata: MetadataSnapshot,
    *,
    count_tokens: Callable[[str], int],
    max_record_tokens: int,
    count_tokens_batch: Callable[[list[str]], list[int]] | None = None,
) -> dict[str, RetrievalView]:
    if metadata.analysis_id != index.ir.analysis_id or max_record_tokens < 1:
        raise ValueError("retrieval view scope or budget mismatch")
    summaries = {item.node_id: item for item in metadata.node_summaries}
    record_views = {item.record_id: item for item in index.record_views}
    groups_by_record = getattr(index, "field_groups_by_record", None)
    if groups_by_record is None:
        groups_by_record = {}
        for group in index.field_groups:
            for record_id in group.record_ids:
                groups_by_record.setdefault(record_id, []).append(group)
    result = {}
    prepared = []
    for record in index.records:
        source = record_views[record.record_id]
        node = summaries.get(record.section_node_id)
        bindings = [*source.header_refs, *source.parent_context_refs, *source.note_refs]
        field_refs = []
        for group in groups_by_record.get(record.record_id, ()):
            for rid in group.record_ids:
                if rid != record.record_id:
                    field_refs.extend(record_views[rid].source_refs)
        bindings.extend(anchor for anchor in field_refs if anchor not in bindings)
        payload = {
            "heading_path": node.path if node else [],
            "headers": [index.ir.resolve(item) for item in source.header_refs],
            "parent_context": [index.ir.resolve(item) for item in source.parent_context_refs],
            "record": [index.ir.resolve(item) for item in source.source_refs],
            "notes": [index.ir.resolve(item) for item in source.note_refs],
            "field_group_context": [index.ir.resolve(item) for item in field_refs],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prepared.append((record, source, node, bindings, payload, text))
    texts = [item[-1] for item in prepared]
    counts = (
        count_tokens_batch(texts) if count_tokens_batch is not None
        else [count_tokens(text) for text in texts]
    )
    if len(counts) != len(prepared):
        raise ValueError("ranking_model_returned_incomplete_batch")
    for (record, source, node, bindings, payload, text), token_count in zip(
        prepared, counts, strict=True
    ):
        result[record.record_id] = RetrievalView(
            record_id=record.record_id,
            retrieval_view_hash=evidence_hash([payload, source.model_dump(mode="json")]),
            source_snapshot_hash=index.ir.document_hash,
            model_text=text,
            structure_text="\n".join(str(value) for values in payload.values() for value in values),
            metadata_text=node.summary or "" if node else "",
            source_refs=source.source_refs,
            binding_refs=bindings,
            source_cell_ids=source.source_cell_ids,
            logical_row_id=source.logical_row_id,
            token_count=token_count,
            status="complete" if token_count <= max_record_tokens else "not_rerankable",
        )
    return result
