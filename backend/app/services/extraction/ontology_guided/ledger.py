"""Append-only domain events and reconstructable checkpoint envelopes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import RunProgress, VersionedRef

CHECKPOINT_SCHEMA_VERSION = "document-recognition-checkpoint-v1"


class LedgerEvent(EvidenceModel):
    sequence: int = Field(ge=1)
    event_key: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload_hash: str = Field(min_length=64, max_length=64)
    payload: dict


class CheckpointEnvelope(EvidenceModel):
    checkpoint_schema_version: Literal[
        "document-recognition-checkpoint-v1", "document-recognition-checkpoint-v2",
        "document-recognition-checkpoint-v3",
    ] = (
        CHECKPOINT_SCHEMA_VERSION
    )
    recognition_run_id: str = Field(min_length=1)
    run_fingerprint: str = Field(min_length=1)
    # The generation is safe recovery metadata.  The bearer token itself is a
    # process-local secret and must never cross this persistence boundary.
    execution_generation: int = Field(ge=1)
    event_seq: int = Field(ge=0)
    frontier: dict
    recall_ledger: dict
    candidate_head_refs: list[VersionedRef] = Field(default_factory=list)
    proof_head_refs: list[VersionedRef] = Field(default_factory=list)
    resolution_events: list[dict] = Field(default_factory=list)
    dependency_index: dict
    retry_queue: list[dict] = Field(default_factory=list)
    scheduler_turns: int = Field(default=0, ge=0)
    task_outcomes: list[dict] = Field(default_factory=list)
    graph_state: dict
    diagnostics: list[str] = Field(default_factory=list)
    ranking_state: dict = Field(default_factory=dict)
    model_call_state: dict = Field(default_factory=dict)
    progress: RunProgress
    content_hash: str = Field(min_length=64, max_length=64)

    def json_payload(self):
        """Reuse already-JSON state; the storage boundary detaches changed values."""
        if self.checkpoint_schema_version != "document-recognition-checkpoint-v3":
            return self.model_dump(mode="json")
        states = {"frontier", "recall_ledger", "resolution_events", "dependency_index",
                  "retry_queue", "task_outcomes", "graph_state", "ranking_state",
                  "model_call_state"}
        return {**self.model_dump(mode="json", exclude=states),
                **{name: getattr(self, name) for name in states}}


class EventLedger:
    def __init__(self):
        self.events: list[LedgerEvent] = []
        self._batches: dict[str, tuple[str, int]] = {}

    @property
    def head(self) -> int:
        return len(self.events)

    def append_batch(
        self,
        *,
        expected_head: int,
        batch_id: str,
        events: list[tuple[str, dict]],
    ) -> int:
        if expected_head != self.head:
            raise ValueError("stale_event_head")
        content_hash = evidence_hash(events)
        previous = self._batches.get(batch_id)
        if previous:
            if previous[0] != content_hash:
                raise ValueError("idempotency_key_reused_with_different_content")
            return previous[1]
        staged = []
        for offset, (event_type, payload) in enumerate(events, start=1):
            sequence = self.head + offset
            payload_hash = evidence_hash(payload)
            staged.append(
                LedgerEvent(
                    sequence=sequence,
                    event_key=stable_id(
                        "recognition-event", [batch_id, offset, event_type, payload_hash]
                    ),
                    event_type=event_type,
                    payload_hash=payload_hash,
                    payload=payload,
                )
            )
        self.events.extend(staged)
        self._batches[batch_id] = (content_hash, self.head)
        return self.head


def checkpoint_envelope(*, structural_digest=None, **values) -> CheckpointEnvelope:
    payload = {key: value for key, value in values.items() if key != "content_hash"}
    if payload.get("model_call_state", {}).get("version") == 2:
        payload["checkpoint_schema_version"] = "document-recognition-checkpoint-v2"
    if structural_digest is not None:
        payload["checkpoint_schema_version"] = "document-recognition-checkpoint-v3"
    unchecked = CheckpointEnvelope(**payload, content_hash="0" * 64)
    canonical = unchecked.json_payload()
    canonical.pop("content_hash")
    digest = structural_digest(canonical) if structural_digest else evidence_hash(canonical)
    return unchecked.model_copy(update={"content_hash": digest})


def restore_checkpoint(
    raw: dict,
    *,
    recognition_run_id: str,
    run_fingerprint: str,
) -> CheckpointEnvelope:
    envelope = CheckpointEnvelope.model_validate(raw)
    if envelope.recognition_run_id != recognition_run_id:
        raise ValueError("checkpoint belongs to another recognition run")
    if envelope.run_fingerprint != run_fingerprint:
        raise ValueError("checkpoint fingerprint mismatch; create a new run")
    payload = envelope.model_dump(mode="json", exclude={"content_hash"})
    if envelope.checkpoint_schema_version == "document-recognition-checkpoint-v3":
        from app.services.extraction.ontology_guided.state_delta import snapshot_delta

        digest = snapshot_delta(payload)[0].digest
    else:
        digest = evidence_hash(payload)
    if digest != envelope.content_hash:
        raise ValueError("checkpoint content hash mismatch")
    return envelope
