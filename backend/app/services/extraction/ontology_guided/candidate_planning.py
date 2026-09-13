"""Shared source scope and sparse, admitted recognition obligations.

Search eligibility is owned by the immutable RecordIndex. A missing ledger
entry means no recognition task was admitted, never a negative source fact.
"""

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    SPARSE_RETRIEVAL_POLICY_VERSION,
    PlannedRecord,
    RecallEntry,
)
from app.services.extraction.ontology_guided.state_delta import freeze_json


def is_sparse(plan):
    return plan.policy_version == SPARSE_RETRIEVAL_POLICY_VERSION


def shared_scope(index):
    cached = getattr(index, "_candidate_search_scope", None)
    if cached is None:
        ids = tuple(record.record_id for record in index.records)
        digest = evidence_hash(list(ids))
        reference = freeze_json({
            "scope_id": stable_id("record-search-scope", [
                index.ir.analysis_id, index.ir.document_hash, index.ir.structure_hash, digest,
            ]),
            "record_count": len(ids),
            "record_hash": digest,
        })
        cached = (ids, frozenset(ids), reference)
        index._candidate_search_scope = cached
    return cached


def record_universe(plan, index):
    return shared_scope(index)[0] if is_sparse(plan) else plan.frozen_record_ids


def admit_records(plan, index, record_ids, *, phase, epoch=None):
    """Materialize only the actual admitted source tasks, preserving prior receipts."""
    if not is_sparse(plan):
        return plan
    _ids, universe, reference = shared_scope(index)
    if plan.search_scope_ref != reference or not set(record_ids) <= universe:
        raise ValueError("candidate admission is outside the shared source scope")
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("candidate admission contains duplicate records")
    records = list(plan.records)
    ledger = dict(plan.ledger)
    ranks = {rid: rank for rank, rid in enumerate(epoch.ordered_record_ids, 1)} if epoch else {}
    for rid in record_ids:
        if rid in ledger:
            continue
        source = index.by_id[rid]
        records.append(PlannedRecord(
            record_id=rid, phase=phase, section_node_id=source.section_node_id,
            record_kind=source.kind, rationale=["candidate_admitted"],
            **({"ranking_epoch_id": epoch.epoch_id, "ranking_epoch_seq": epoch.epoch_seq,
                "pool_rank": ranks[rid], "ranking_mode": epoch.actual_ranking_mode}
               if rid in ranks else {}),
        ))
        ledger[rid] = RecallEntry(record_id=rid, phase=phase)
    return plan.model_copy(update={"records": records, "ledger": ledger})
