"""Durable dispatcher and worker for ontology-guided document runs.

The database queue row is authoritative.  Calling :func:`dispatch_run` is only
a best-effort wake-up: it first claims the durable execution generation and all
subsequent writes are fenced by the secret token.  A stale or cancelled worker
therefore cannot publish a late artifact.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunCandidateHead,
    DocumentVerificationProofHead,
)
from app.services.document_analysis.artifact_store import (
    RunArtifactStorage,
    SourceArtifactError,
)
from app.services.document_analysis.public_projection import build_selection_registry
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    InvalidRunState,
    LeaseBusy,
    RunDeleted,
    RunNotFound,
    content_hash,
)
from app.services.extraction.doc_converter import DocConversionError, ensure_docx
from app.services.extraction.document_annotator import annotate_word
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.context_records import CONTEXT_RECORDS_VERSION
from app.services.extraction.ontology_guided.contracts import (
    CONTRACT_VERSION,
    METADATA_POLICY_VERSION,
    ONTOLOGY_SNAPSHOT_VERSION,
    PROJECTION_POLICY_VERSION,
    PROOF_POLICY_VERSION,
    RETRIEVAL_POLICY_VERSION,
    GraphSnapshot,
    MetadataSnapshot,
    OntologySnapshot,
    RunProgress,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import (
    ExecutionBatch,
    OntologyGuidedExecutor,
)
from app.services.extraction.ontology_guided.ledger import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEnvelope,
    checkpoint_envelope,
    restore_checkpoint,
)
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.ontology_guided.model_adapter import (
    ADAPTER_VERSION,
    configured_model_adapter,
)
from app.services.extraction.ontology_guided.projection import project_graph
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.semantic_reranker import RankingService
from app.services.extraction.ontology_guided.task_citations import TASK_CITATION_VERSION
from app.services.extraction.ontology_guided.verification import (
    EligibilityPolicy,
    PredicatePolicyRegistry,
)
from app.services.extraction.word_analysis import analyze_word_core
from app.services.extraction.word_tree_summarizer import (
    fallback_word_tree_summaries,
    summarize_word_tree,
)
from app.services.llm.local_client import get_local_llm
from app.services.llm.model_runtime import ModelCancelled, model_scope
from app.services.llm.semantic_ranking import configured_ranking_service

logger = logging.getLogger(__name__)


class WorkerInterrupted(RuntimeError):
    """The active execution generation was paused, cancelled, or replaced."""


class FingerprintMismatch(RuntimeError):
    """A persisted run cannot resume under changed semantic dependencies."""


class CheckpointMismatch(RuntimeError):
    """A private checkpoint does not match its durable run waterline."""


class _LeaseKeeper:
    """Renew one execution lease from a session independent of worker work."""

    def __init__(
        self,
        *,
        bind: Engine | None,
        recognition_run_id: UUID | str,
        owner_id: str,
        token: str,
        lease_seconds: float,
    ) -> None:
        self._factory = _session_factory(bind)
        self._recognition_run_id = recognition_run_id
        self._owner_id = owner_id
        self._token = token
        self._lease_seconds = lease_seconds
        self._interval = max(0.05, min(15.0, lease_seconds / 3))
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._control_requested = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"document-analysis-lease-{recognition_run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def raise_if_lost(self) -> None:
        if self._lost.is_set():
            raise WorkerInterrupted("document-analysis execution lease was lost")

    def should_stop(self) -> bool:
        return self._lost.is_set() or self._control_requested.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            db = self._factory()
            try:
                execution = DocumentAnalysisRunStore(db).heartbeat(
                    self._recognition_run_id,
                    self._owner_id,
                    self._token,
                    lease_seconds=self._lease_seconds,
                )
                db.commit()
                if execution.pause_requested or execution.cancel_requested:
                    self._control_requested.set()
            except (FenceViolation, RunDeleted, RunNotFound):
                db.rollback()
                self._lost.set()
                return
            except Exception:
                db.rollback()
                logger.warning(
                    "document-analysis lease heartbeat failed for %s",
                    self._recognition_run_id,
                    exc_info=True,
                )
            finally:
                db.close()


def _session_factory(bind: Engine | None):
    if bind is None:
        return SessionLocal
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


def _artifact(
    db: Session,
    store: DocumentAnalysisRunStore,
    run_id: UUID | str,
    owner_id: str,
    kind: str,
) -> DocumentAnalysisArtifact:
    run_ref = store.get_artifact(run_id, owner_id, kind)
    if run_ref is None:
        raise RuntimeError(f"missing required {kind} artifact")
    artifact = db.get(DocumentAnalysisArtifact, run_ref.artifact_id)
    if artifact is None or artifact.content_hash != run_ref.content_hash:
        raise RuntimeError(f"invalid required {kind} artifact")
    return artifact


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_event(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    stage: str,
    public_stage: str,
) -> DocumentAnalysisRun:
    updated = store.update_stage(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_revision=run.revision,
        stage=stage,
        # A replacement worker may be finishing an operator-requested pause
        # after the previous lease expired.  Parsing and metadata publication
        # are still safe, but must not erase the durable ``pausing`` state.
        execution_status=run.execution_status,
    )
    event = store.append_event(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_head=updated.event_head,
        event_key=f"stage:{stage}:{updated.revision}",
        event_type="run_state",
        payload={
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(run.recognition_run_id),
            "run_revision": updated.revision + 1,
            "event_head": updated.event_head + 1,
            "artifact_revision": updated.artifact_revision,
            "status": "running",
            "stage": public_stage,
        },
    )
    db.commit()
    return store.get_owned(event.recognition_run_id, run.owner_id)


def _publish_artifact(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    kind: str,
    status: str,
    payload: dict[str, Any],
    analysis_id: str | None = None,
    metadata_snapshot_id: str | None = None,
    graph_snapshot_id: str | None = None,
) -> DocumentAnalysisRun:
    artifact_hash = content_hash(payload)
    head = store.get_artifact_head(run.recognition_run_id, run.owner_id, kind)
    expected_artifact_revision = head.revision if head is not None else 0
    event = store.append_event(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_head=run.event_head,
        event_key=f"artifact:{kind}:{artifact_hash}",
        event_type="artifact",
        payload={
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(run.recognition_run_id),
            "run_revision": run.revision + 2,
            "event_head": run.event_head + 1,
            "artifact_revision": run.artifact_revision + 1,
            "status": "running",
            "stage": {
                "structure": "parsing",
                "metadata": "preparing_metadata",
                "graph": "extracting",
            }.get(kind, run.stage),
            "artifact_kind": kind,
            "availability": status,
            "link": f"/api/document-analysis/runs/{run.recognition_run_id}/{kind}",
        },
    )
    store.update_artifact(
        run.recognition_run_id,
        run.owner_id,
        token,
        artifact_kind=kind,
        expected_revision=expected_artifact_revision,
        artifact_hash=artifact_hash,
        status=status,
        artifact_id=stable_id(f"{kind}-artifact", [str(run.recognition_run_id), artifact_hash]),
        media_type="application/json",
        payload=payload,
        event_head=event.sequence,
        analysis_id=analysis_id,
        metadata_snapshot_id=metadata_snapshot_id,
        graph_snapshot_id=graph_snapshot_id,
    )
    db.commit()
    return store.get_owned(run.recognition_run_id, run.owner_id)


def _append_progress_event(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    progress: RunProgress,
) -> tuple[DocumentAnalysisRun, int]:
    event = store.append_event(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_head=run.event_head,
        event_key=f"progress:{run.event_head}:{content_hash(progress.model_dump(mode='json'))}",
        event_type="progress",
        payload={
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(run.recognition_run_id),
            "run_revision": run.revision + 1,
            "event_head": run.event_head + 1,
            "artifact_revision": run.artifact_revision,
            "status": "running",
            "stage": "extracting",
            "progress": progress.model_dump(mode="json"),
        },
    )
    db.flush()
    return store.get_owned(run.recognition_run_id, run.owner_id), event.sequence


def _persist_graph_objects(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    graph: GraphSnapshot,
    execution_events: list[tuple[str, dict]],
) -> DocumentAnalysisRun:
    run, event_sequence = _append_progress_event(db, store, run, token, graph.progress)
    proofs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for event_type, payload in execution_events:
        if event_type != "task_outcome":
            continue
        outcome = payload.get("outcome") or {}
        decisions_by_target: dict[str, list[dict[str, Any]]] = {}
        for decision in outcome.get("decision_payloads") or []:
            decisions_by_target.setdefault(str(decision.get("target_id")), []).append(decision)
        for proof in outcome.get("proof_payloads") or []:
            proofs.append((proof, decisions_by_target.get(str(proof["target_id"]), [])))

    # Match the exact per-attempt bundle already persisted by the batch path.
    # A stable semantic target can have several technical attempts; collecting
    # all their decisions would silently rewrite every earlier proof payload.
    for proof, decisions in proofs:
        target_id = str(proof["target_id"])
        store.put_proof(
            run.recognition_run_id,
            run.owner_id,
            token,
            proof_id=str(proof["proof_id"]),
            proof_revision=int(proof.get("proof_revision", 1)),
            target_id=target_id,
            payload={
                "predicate_evidence": proof,
                "decisions": decisions,
            },
            event_sequence=event_sequence,
        )
        run = store.get_owned(run.recognition_run_id, run.owner_id)

    for kind, candidates in (
        ("entity", graph.nodes),
        ("relationship", graph.edges),
        ("property", graph.properties),
    ):
        for candidate in candidates:
            candidate_id = getattr(candidate, "candidate_id", None) or candidate.entity_id
            proof_ref = getattr(candidate, "proof_ref", None)
            store.put_candidate(
                run.recognition_run_id,
                run.owner_id,
                token,
                candidate_id=candidate_id,
                revision=candidate.revision,
                kind=kind,
                payload=candidate.model_dump(mode="json"),
                proof_refs=[proof_ref.model_dump(mode="json")] if proof_ref else [],
                event_sequence=event_sequence,
            )
            run = store.get_owned(run.recognition_run_id, run.owner_id)
    db.flush()
    return run


def _finish(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    status: str,
    public_status: str,
    public_stage: str,
    progress: RunProgress,
    stop_reason: str | None,
    error: dict[str, Any] | None = None,
) -> None:
    effective_progress = (
        progress.model_copy(update={"stop_reason": stop_reason})
        if stop_reason is not None
        else progress
    )
    event_type = "error" if error else "run_state"
    store.append_event(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_head=run.event_head,
        event_key=f"terminal:{status}:{run.event_head + 1}",
        event_type=event_type,
        payload={
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(run.recognition_run_id),
            "run_revision": run.revision + 2,
            "event_head": run.event_head + 1,
            "artifact_revision": run.artifact_revision,
            "status": public_status,
            "stage": public_stage,
            "progress": effective_progress.model_dump(mode="json"),
            "error": error,
        },
    )
    run = store.get_owned(run.recognition_run_id, run.owner_id)
    store.update_stage(
        run.recognition_run_id,
        run.owner_id,
        token,
        expected_revision=run.revision,
        stage={"complete": "finalize", "extracting": "recognition"}.get(public_stage, public_stage),
        execution_status=status,
        progress=effective_progress.model_dump(mode="json"),
        stop_reason=stop_reason,
        error=error,
        expires_at=datetime.now(UTC) + timedelta(days=settings.document_analysis_retention_days),
    )
    db.commit()


def _failure_payload(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, DocConversionError):
        return "INVALID_WORD", "文档转换失败"
    if isinstance(exc, SourceArtifactError):
        return exc.code, exc.message
    if isinstance(exc, ValueError):
        return "INVALID_WORD", "Word 文档解析失败"
    return "ANALYSIS_FAILED", "文档分析执行失败"


def _configured_ranking():
    return configured_ranking_service(settings)


def _restore_ranking_state(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    *,
    final_fingerprint: str,
) -> dict:
    ref = store.get_artifact(run.recognition_run_id, run.owner_id, "ranking_state")
    if ref is None:
        return {}
    artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id)
    if (
        artifact is None
        or artifact.payload is None
        or artifact.content_hash != ref.content_hash
        or content_hash(artifact.payload) != ref.content_hash
        or ref.event_head > run.event_head
    ):
        raise CheckpointMismatch("ranking artifact head is invalid")
    state = dict(artifact.payload)
    if (
        state.get("recognition_run_id") != str(run.recognition_run_id)
        or state.get("run_fingerprint") != final_fingerprint
    ):
        raise CheckpointMismatch("ranking state belongs to another run or fingerprint")
    return state


def _persist_ranking_state(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    final_fingerprint: str,
    state: dict,
) -> None:
    """Commit the complete ranking boundary before its first recognition call."""
    try:
        store.assert_fence(run.recognition_run_id, run.owner_id, token)
        current = store.get_owned(run.recognition_run_id, run.owner_id)
        if (
            current.run_fingerprint != final_fingerprint
            or state.get("run_fingerprint") != final_fingerprint
            or state.get("recognition_run_id") != str(current.recognition_run_id)
        ):
            raise FingerprintMismatch("ranking response has stale run dependencies")
        previous = _restore_ranking_state(db, store, current, final_fingerprint=final_fingerprint)
        prior_service = previous.get("service") or {}
        service = state.get("service") or {}
        prior_epochs = prior_service.get("epochs") or []
        epochs = service.get("epochs") or []
        if previous and (
            any(prior_service.get(key) != service.get(key) for key in ("policy", "model_identity"))
            or any(
                (state.get("committed_at") or {}).get(key) != value
                for key, value in (previous.get("committed_at") or {}).items()
            )
            or any(
                service.get("costs", {}).get(key, 0) < value
                for key, value in prior_service.get("costs", {}).items()
            )
            or any(
                service.get(ledger, {}).get(key, 0) < value
                for ledger in ("request_attempts", "record_call_counts", "slot_costs")
                for key, value in prior_service.get(ledger, {}).items()
            )
        ):
            raise CheckpointMismatch("ranking dependencies, commit positions or cost regressed")
        prior_receipts = prior_service.get("dispatch_receipts", [])
        receipts = service.get("dispatch_receipts", [])
        if receipts[:len(prior_receipts)] != prior_receipts:
            # A durable claim consumes the known-unused reservation. Erasing or
            # replacing its history would make the same reservation reusable.
            raise CheckpointMismatch("ranking dispatch receipts cannot be replaced or removed")
        by_id = {epoch["epoch_id"]: epoch for epoch in epochs}
        if len(by_id) != len(epochs) or any(
            by_id.get(epoch["epoch_id"]) != epoch for epoch in prior_epochs
        ):
            raise CheckpointMismatch("committed ranking epochs cannot be replaced or removed")
        digest = content_hash(state)
        if previous and content_hash(previous) == digest:
            return
        head = store.get_artifact_head(
            current.recognition_run_id, current.owner_id, "ranking_state"
        )
        event = store.append_event(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_head=current.event_head,
            event_key=f"ranking:{digest}",
            event_type="progress",
            payload={
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": str(current.recognition_run_id),
                "run_revision": current.revision + 2,
                "event_head": current.event_head + 1,
                "artifact_revision": current.artifact_revision + 1,
                "status": "running",
                "stage": "extracting",
                "ranking_epochs": len(epochs),
            },
        )
        store.update_artifact(
            current.recognition_run_id,
            current.owner_id,
            token,
            artifact_kind="ranking_state",
            expected_revision=head.revision if head else 0,
            artifact_hash=digest,
            status="ready",
            artifact_id=stable_id("ranking-state", [str(current.recognition_run_id), digest]),
            media_type="application/json",
            payload=state,
            event_head=event.sequence,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _validate_model_call_state(state: dict) -> None:
    """Validate private pre-request reservations without accepting request payloads."""
    if not isinstance(state, dict) or set(state) != {
        "version", "recognition_run_id", "run_fingerprint", "lineage_calls", "reservations"
    }:
        raise CheckpointMismatch("model call reservation envelope is invalid")
    counts = state["lineage_calls"]
    reservations = state["reservations"]
    if (
        type(state["version"]) is not int
        or state["version"] != 1
        or not isinstance(state["recognition_run_id"], str)
        or not isinstance(state["run_fingerprint"], str)
        or not isinstance(counts, dict)
        or not isinstance(reservations, list)
        or any(
            not isinstance(lineage, str) or not lineage or type(count) is not int or count < 0
            for lineage, count in counts.items()
        )
    ):
        raise CheckpointMismatch("model call reservation values are invalid")
    observed: dict[str, int] = {}
    for sequence, reservation in enumerate(reservations, 1):
        if (
            not isinstance(reservation, dict)
            or set(reservation) != {"sequence", "task_id", "stage", "ordinal", "lineage_id"}
            or type(reservation.get("sequence")) is not int
            or reservation["sequence"] != sequence
            or type(reservation.get("ordinal")) is not int
            or reservation["ordinal"] < 1
            or any(
                not isinstance(reservation.get(key), str) or not reservation[key]
                for key in ("task_id", "stage", "lineage_id")
            )
        ):
            raise CheckpointMismatch("model call reservation sequence is invalid")
        lineage = reservation["lineage_id"]
        observed[lineage] = observed.get(lineage, 0) + 1
    # Older completed outcomes may predate reservations.  Their conservative
    # count is carried forward when a subsequent request first reserves budget.
    if any(count > counts.get(lineage, 0) for lineage, count in observed.items()):
        raise CheckpointMismatch("model call reservation count is invalid")


def _restore_model_call_state(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    *,
    final_fingerprint: str,
) -> dict:
    ref = store.get_artifact(run.recognition_run_id, run.owner_id, "recognition-model-calls")
    if ref is None:
        return {}
    artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id)
    if (
        artifact is None
        or artifact.payload is None
        or artifact.content_hash != ref.content_hash
        or content_hash(artifact.payload) != ref.content_hash
        or ref.event_head > run.event_head
    ):
        raise CheckpointMismatch("model call reservation artifact head is invalid")
    state = deepcopy(artifact.payload)
    _validate_model_call_state(state)
    if (
        state["recognition_run_id"] != str(run.recognition_run_id)
        or state["run_fingerprint"] != final_fingerprint
    ):
        raise CheckpointMismatch("model call reservations belong to another run or fingerprint")
    return state


def _persist_model_call_state(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    final_fingerprint: str,
    state: dict,
) -> None:
    """Commit budget before chat, independently of the next completed batch."""
    try:
        store.assert_fence(run.recognition_run_id, run.owner_id, token)
        current = store.get_owned(run.recognition_run_id, run.owner_id)
        _validate_model_call_state(state)
        if (
            current.run_fingerprint != final_fingerprint
            or state["run_fingerprint"] != final_fingerprint
            or state["recognition_run_id"] != str(current.recognition_run_id)
        ):
            raise FingerprintMismatch("model call reservation has stale run dependencies")
        previous = _restore_model_call_state(
            db, store, current, final_fingerprint=final_fingerprint
        )
        if previous and (
            state["reservations"][: len(previous["reservations"])] != previous["reservations"]
            or any(
                state["lineage_calls"].get(lineage, 0) < count
                for lineage, count in previous["lineage_calls"].items()
            )
        ):
            raise CheckpointMismatch("model call reservations cannot be removed or rewritten")
        state = deepcopy(state)
        digest = content_hash(state)
        if previous and content_hash(previous) == digest:
            return
        reserved = sum(state["lineage_calls"].values())
        progress = dict(current.progress or {})
        progress.update(
            model_calls_reserved=reserved,
            model_calls_unresolved=max(0, reserved - int(progress.get("model_calls", 0))),
        )
        current = store.update_stage(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_revision=current.revision,
            stage=current.stage,
            progress=progress,
        )
        head = store.get_artifact_head(
            current.recognition_run_id, current.owner_id, "recognition-model-calls"
        )
        event = store.append_event(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_head=current.event_head,
            event_key=f"model-call-reservation:{digest}",
            event_type="progress",
            payload={
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": str(current.recognition_run_id),
                "run_revision": current.revision + 2,
                "event_head": current.event_head + 1,
                "artifact_revision": current.artifact_revision + 1,
                "status": "running",
                "stage": "extracting",
                "model_calls_reserved": sum(state["lineage_calls"].values()),
            },
        )
        store.update_artifact(
            current.recognition_run_id,
            current.owner_id,
            token,
            artifact_kind="recognition-model-calls",
            expected_revision=head.revision if head else 0,
            artifact_hash=digest,
            status="ready",
            artifact_id=stable_id(
                "recognition-model-calls", [str(current.recognition_run_id), digest]
            ),
            media_type="application/json",
            payload=state,
            event_head=event.sequence,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _recognition_fingerprint(
    run: DocumentAnalysisRun,
    ir: DocumentIR,
    metadata: MetadataSnapshot,
    ontology: OntologySnapshot,
    adapter: object | None,
    ranking_identity: dict | None = None,
    origin: dict | None = None,
) -> str:
    """Freeze every semantic/configuration dependency needed for safe resume."""

    return evidence_hash(
        {
            "document_hash": run.document_hash,
            "structure_hash": ir.structure_hash,
            "parser_version": ir.parser_version,
            "structure_policy_version": ir.structure_policy_version,
            "ontology_hash": ontology.ontology_hash,
            "ontology_schema_version": ONTOLOGY_SNAPSHOT_VERSION,
            "metadata_dependency_hash": metadata.dependency_hash,
            "metadata_policy_version": METADATA_POLICY_VERSION,
            "root_class_iri": run.root_class_iri,
            "metadata_mode": run.metadata_mode,
            "scope_mode": run.scope_mode,
            "focus_path": list(run.focus_path or []),
            "model_identity": getattr(adapter, "model_identity", None),
            "ranking": ranking_identity,
            "origin": origin,
            "tokenizer": {
                "backend": settings.local_llm_tokenizer_backend,
                "path": settings.local_llm_tokenizer_path,
                "server_model_path": settings.local_llm_server_model_path,
            },
            "budget": {
                "max_input_tokens": settings.evidence_max_input_tokens,
                "max_output_tokens": settings.evidence_max_output_tokens,
                "max_tasks": settings.evidence_max_tasks,
                "max_regions_per_task": settings.evidence_max_regions_per_task,
                "max_objects_per_task": settings.evidence_max_objects_per_task,
                "max_model_calls_per_record": settings.document_analysis_max_model_calls_per_record,
            },
            "retry": {
                "timeout_seconds": settings.evidence_timeout_s,
                "timeout_retries": settings.evidence_timeout_retries,
                "total_timeout_seconds": settings.evidence_total_timeout_s,
            },
            "protocol": {
                "contract": CONTRACT_VERSION,
                "checkpoint": CHECKPOINT_SCHEMA_VERSION,
                "retrieval": RETRIEVAL_POLICY_VERSION,
                "proof": PROOF_POLICY_VERSION,
                "projection": PROJECTION_POLICY_VERSION,
                "predicate_policy": PredicatePolicyRegistry.version,
                "eligibility": EligibilityPolicy.version,
                "executor": OntologyGuidedExecutor.version,
                "adapter": ADAPTER_VERSION,
                "citations": TASK_CITATION_VERSION,
                "context_records": CONTEXT_RECORDS_VERSION,
                "scheduler": "hierarchical-fair-scheduler-v1",
                "phase_interleaving": True,
                "phase_ratio": [4, 1],
                "cold_start": [1, 2],
                "exploration_every": 5,
            },
        }
    )


def _rebase_graph(
    graph: GraphSnapshot,
    *,
    run_revision: int,
    event_head: int,
) -> GraphSnapshot:
    return project_graph(
        recognition_run_id=graph.recognition_run_id,
        run_revision=run_revision,
        event_head=event_head,
        metadata_snapshot_id=graph.metadata_snapshot_id,
        root_ref=graph.root_ref,
        nodes=graph.nodes,
        edges=graph.edges,
        properties=graph.properties,
        coverage=graph.coverage,
        progress=graph.progress,
        projection="all",
        artifact_status=graph.artifact_status,
    )


def _persist_batch_objects(
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    batch: ExecutionBatch,
    *,
    event_sequence: int,
) -> None:
    decisions_by_target: dict[str, list[dict[str, Any]]] = {}
    for decision in batch.outcome.decision_payloads:
        decisions_by_target.setdefault(str(decision.get("target_id")), []).append(decision)
    for proof in batch.outcome.proof_payloads:
        target_id = str(proof["target_id"])
        proof_id = str(proof["proof_id"])
        proof_head = store.db.get(
            DocumentVerificationProofHead,
            (run.recognition_run_id, proof_id),
            populate_existing=True,
        )
        store.put_proof(
            run.recognition_run_id,
            run.owner_id,
            token,
            proof_id=proof_id,
            proof_revision=int(proof.get("proof_revision", 1)),
            target_id=target_id,
            payload={
                "predicate_evidence": proof,
                "decisions": decisions_by_target.get(target_id, []),
            },
            expected_head_revision=(proof_head.proof_revision if proof_head is not None else 0),
            event_sequence=event_sequence,
        )

    # A committed checkpoint must describe a completely reconstructable graph,
    # not only the delta returned by its final model call.  In particular the
    # document-root node is created by the executor rather than by an adapter;
    # omitting it caused a hard crash during finalization to leave candidate
    # heads ahead of the latest checkpoint and made safe recovery impossible.
    candidates_by_revision: dict[tuple[str, str, int], Any] = {}
    for kind, candidates in (
        ("entity", batch.outcome.nodes),
        ("relationship", batch.outcome.edges),
        ("property", batch.outcome.properties),
        ("entity", batch.graph.nodes),
        ("relationship", batch.graph.edges),
        ("property", batch.graph.properties),
    ):
        for candidate in candidates:
            candidate_id = getattr(candidate, "candidate_id", None) or candidate.entity_id
            candidates_by_revision[(kind, candidate_id, candidate.revision)] = candidate

    for (kind, candidate_id, _revision), candidate in candidates_by_revision.items():
        candidate_head = store.db.get(
            DocumentRunCandidateHead,
            (run.recognition_run_id, candidate_id),
            populate_existing=True,
        )
        proof_ref = getattr(candidate, "proof_ref", None)
        store.put_candidate(
            run.recognition_run_id,
            run.owner_id,
            token,
            candidate_id=candidate_id,
            revision=candidate.revision,
            kind=kind,
            payload=candidate.model_dump(mode="json"),
            proof_refs=[proof_ref.model_dump(mode="json")] if proof_ref else [],
            expected_head_revision=(candidate_head.revision if candidate_head is not None else 0),
            event_sequence=event_sequence,
        )


def _head_refs(db: Session, run_id: UUID | str, model, revision_field: str):
    rows = list(
        db.scalars(
            select(model)
            .where(model.recognition_run_id == run_id)
            .order_by(getattr(model, "candidate_id", getattr(model, "proof_id", None)))
        )
    )
    identifier = "candidate_id" if model is DocumentRunCandidateHead else "proof_id"
    return [
        VersionedRef(id=getattr(row, identifier), revision=getattr(row, revision_field))
        for row in rows
    ]


def _persist_recognition_batch(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    final_fingerprint: str,
    ontology: OntologySnapshot,
    ir: DocumentIR,
    metadata: MetadataSnapshot,
    index: RecordIndex,
    batch: ExecutionBatch,
) -> None:
    batch_hash = content_hash(
        {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_fingerprint": final_fingerprint,
            "batch": batch.model_dump(mode="json"),
        }
    )
    if (
        store.event_batch_receipt(
            run.recognition_run_id,
            run.owner_id,
            token,
            batch_id=batch.batch_id,
            batch_hash=batch_hash,
        )
        is not None
    ):
        return

    with db.begin_nested():
        current = store.get_owned(run.recognition_run_id, run.owner_id)
        event = store.append_event(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_head=current.event_head,
            event_key=f"recognition-batch:{batch.batch_id}",
            event_type="progress",
            payload={
                "contract_version": CONTRACT_VERSION,
                "recognition_run_id": str(current.recognition_run_id),
                "run_revision": current.revision + 1,
                "event_head": current.event_head + 1,
                "artifact_revision": current.artifact_revision,
                "status": "running",
                "stage": "extracting",
                "progress": batch.graph.progress.model_dump(mode="json"),
                "task": batch.task.model_dump(mode="json"),
                "outcome": batch.outcome.model_dump(mode="json"),
            },
        )
        current = store.get_owned(current.recognition_run_id, current.owner_id)
        _persist_batch_objects(
            store,
            current,
            token,
            batch,
            event_sequence=event.sequence,
        )
        current = store.get_owned(current.recognition_run_id, current.owner_id)
        graph = _rebase_graph(
            batch.graph,
            run_revision=current.revision,
            event_head=event.sequence,
        )
        graph_snapshot_id = stable_id(
            "graph-snapshot",
            [str(current.recognition_run_id), graph.generated_from_hash, event.sequence],
        )
        graph_payload = {
            "snapshot_id": graph_snapshot_id,
            "analysis_id": ir.analysis_id,
            "ontology_snapshot_id": ontology.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "graph": graph.model_dump(mode="json"),
            "selection_registry": build_selection_registry(
                recognition_run_id=str(current.recognition_run_id),
                analysis_id=ir.analysis_id,
                graph=graph,
                index=index,
            ),
            "retrieval_plans": list(batch.recall_ledger.values()),
            "dependency_index": batch.dependency_index,
            "diagnostics": batch.diagnostics,
            "ranking_state": batch.ranking_state,
        }
        graph_head = store.get_artifact_head(current.recognition_run_id, current.owner_id, "graph")
        store.update_artifact(
            current.recognition_run_id,
            current.owner_id,
            token,
            artifact_kind="graph",
            expected_revision=graph_head.revision if graph_head else 0,
            artifact_hash=content_hash(graph_payload),
            status=graph.artifact_status,
            artifact_id=stable_id(
                "graph-artifact",
                [str(current.recognition_run_id), graph_snapshot_id],
            ),
            media_type="application/json",
            payload=graph_payload,
            event_head=event.sequence,
            analysis_id=ir.analysis_id,
            metadata_snapshot_id=metadata.snapshot_id,
            graph_snapshot_id=graph_snapshot_id,
        )
        execution = store.assert_fence(current.recognition_run_id, current.owner_id, token)
        candidate_refs = _head_refs(
            db, current.recognition_run_id, DocumentRunCandidateHead, "revision"
        )
        proof_refs = _head_refs(
            db,
            current.recognition_run_id,
            DocumentVerificationProofHead,
            "proof_revision",
        )
        checkpoint = checkpoint_envelope(
            recognition_run_id=str(current.recognition_run_id),
            run_fingerprint=final_fingerprint,
            execution_generation=execution.generation,
            event_seq=event.sequence,
            frontier=batch.frontier,
            recall_ledger=batch.recall_ledger,
            candidate_head_refs=candidate_refs,
            proof_head_refs=proof_refs,
            resolution_events=[],
            dependency_index=batch.dependency_index,
            retry_queue=list(batch.frontier.get("retries") or []),
            scheduler_turns=int(batch.frontier.get("turn", 0)),
            task_outcomes=batch.task_outcomes,
            graph_state=graph.model_dump(mode="json"),
            diagnostics=batch.diagnostics,
            ranking_state=batch.ranking_state,
            model_call_state=batch.model_call_state,
            progress=graph.progress,
        )
        checkpoint_payload = checkpoint.model_dump(mode="json")
        checkpoint_head = store.get_artifact_head(
            current.recognition_run_id,
            current.owner_id,
            "recognition_checkpoint",
        )
        checkpoint_artifact_id = stable_id(
            "recognition-checkpoint-artifact",
            [str(current.recognition_run_id), checkpoint.content_hash],
        )
        store.update_artifact(
            current.recognition_run_id,
            current.owner_id,
            token,
            artifact_kind="recognition_checkpoint",
            expected_revision=checkpoint_head.revision if checkpoint_head else 0,
            artifact_hash=content_hash(checkpoint_payload),
            status="ready",
            artifact_id=checkpoint_artifact_id,
            media_type="application/vnd.slpra.document-recognition-checkpoint+json",
            payload=checkpoint_payload,
            event_head=event.sequence,
        )
        current = store.get_owned(current.recognition_run_id, current.owner_id)
        store.update_stage(
            current.recognition_run_id,
            current.owner_id,
            token,
            expected_revision=current.revision,
            stage="recognition",
            progress=graph.progress.model_dump(mode="json"),
        )
        store.put_event_batch(
            current.recognition_run_id,
            current.owner_id,
            token,
            batch_id=batch.batch_id,
            batch_hash=batch_hash,
            first_sequence=event.sequence,
            last_sequence=event.sequence,
            checkpoint_artifact_id=checkpoint_artifact_id,
        )
    db.commit()


def _restore_recognition_checkpoint(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    *,
    final_fingerprint: str,
) -> CheckpointEnvelope | None:
    ref = store.get_artifact(
        run.recognition_run_id,
        run.owner_id,
        "recognition_checkpoint",
    )
    if ref is None:
        return None
    artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id)
    if artifact is None or artifact.payload is None or artifact.content_hash != ref.content_hash:
        raise CheckpointMismatch("checkpoint artifact head is invalid")
    try:
        checkpoint = restore_checkpoint(
            artifact.payload,
            recognition_run_id=str(run.recognition_run_id),
            run_fingerprint=final_fingerprint,
        )
    except (TypeError, ValueError) as exc:
        raise CheckpointMismatch(str(exc)) from exc
    if ref.event_head != checkpoint.event_seq or checkpoint.event_seq > run.event_head:
        raise CheckpointMismatch("checkpoint event watermark mismatch")
    execution = db.get(
        DocumentAnalysisExecution,
        run.recognition_run_id,
        populate_existing=True,
    )
    if execution is None or checkpoint.execution_generation > execution.generation:
        raise CheckpointMismatch("checkpoint execution generation is invalid")
    batch = db.scalar(
        select(DocumentRecognitionEventBatch).where(
            DocumentRecognitionEventBatch.recognition_run_id == run.recognition_run_id,
            DocumentRecognitionEventBatch.checkpoint_artifact_id == artifact.artifact_id,
            DocumentRecognitionEventBatch.last_sequence == checkpoint.event_seq,
        )
    )
    if batch is None:
        raise CheckpointMismatch("checkpoint has no committed batch receipt")
    actual_candidates = _head_refs(db, run.recognition_run_id, DocumentRunCandidateHead, "revision")
    actual_proofs = _head_refs(
        db, run.recognition_run_id, DocumentVerificationProofHead, "proof_revision"
    )
    if (
        checkpoint.candidate_head_refs != actual_candidates
        or checkpoint.proof_head_refs != actual_proofs
    ):
        raise CheckpointMismatch("checkpoint object heads do not match durable heads")
    graph = GraphSnapshot.model_validate(checkpoint.graph_state)
    if (
        graph.recognition_run_id != str(run.recognition_run_id)
        or graph.metadata_snapshot_id != run.metadata_snapshot_id
        or graph.event_head != checkpoint.event_seq
    ):
        raise CheckpointMismatch("checkpoint graph identity or watermark mismatch")
    graph_ref = db.scalar(
        select(DocumentRunArtifact)
        .where(
            DocumentRunArtifact.recognition_run_id == run.recognition_run_id,
            DocumentRunArtifact.artifact_kind == "graph",
            DocumentRunArtifact.event_head == checkpoint.event_seq,
        )
        .order_by(DocumentRunArtifact.revision.desc())
    )
    graph_artifact = (
        db.get(DocumentAnalysisArtifact, graph_ref.artifact_id) if graph_ref is not None else None
    )
    expected_graph_snapshot_id = stable_id(
        "graph-snapshot",
        [str(run.recognition_run_id), graph.generated_from_hash, checkpoint.event_seq],
    )
    if (
        graph_ref is None
        or graph_artifact is None
        or graph_artifact.content_hash != graph_ref.content_hash
        or graph_artifact.payload is None
        or graph_artifact.payload.get("snapshot_id") != expected_graph_snapshot_id
        or graph_artifact.payload.get("graph") != checkpoint.graph_state
    ):
        raise CheckpointMismatch("checkpoint graph artifact is invalid")
    return checkpoint


def _complete_pause_at_boundary(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    public_stage: str,
) -> bool:
    """Finish a requested pause without starting work beyond a safe boundary."""

    execution = store.heartbeat(
        run.recognition_run_id,
        run.owner_id,
        token,
        lease_seconds=settings.document_analysis_lease_seconds,
    )
    pause_requested = execution.pause_requested
    db.commit()
    if not pause_requested:
        return False
    current = store.get_owned(run.recognition_run_id, run.owner_id)
    store.append_event(
        current.recognition_run_id,
        current.owner_id,
        token,
        expected_head=current.event_head,
        event_key=f"paused:{current.event_head + 1}",
        event_type="run_state",
        payload={
            "contract_version": CONTRACT_VERSION,
            "recognition_run_id": str(current.recognition_run_id),
            "run_revision": current.revision + 2,
            "event_head": current.event_head + 1,
            "artifact_revision": current.artifact_revision,
            "status": "paused",
            "stage": public_stage,
        },
    )
    current = store.get_owned(current.recognition_run_id, current.owner_id)
    store.complete_pause(
        current.recognition_run_id,
        current.owner_id,
        token,
        expected_revision=current.revision,
        expires_at=datetime.now(UTC) + timedelta(days=settings.document_analysis_retention_days),
    )
    db.commit()
    return True


def _execute_claimed(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
    *,
    interruption_check: Callable[[], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    check_interrupted = interruption_check or (lambda: None)
    check_interrupted()
    if _complete_pause_at_boundary(
        db,
        store,
        run,
        token,
        public_stage={
            "ingest": "accepted",
            "parse": "parsing",
            "metadata": "preparing_metadata",
            "recognition": "extracting",
        }.get(run.stage, "extracting"),
    ):
        return
    storage = RunArtifactStorage(settings.document_analysis_storage_dir)
    source_artifact = _artifact(db, store, run.recognition_run_id, run.owner_id, "source")
    origin = (source_artifact.payload or {}).get("origin")
    priority_paths = [tuple(path) for path in (origin or {}).get("priority_paths", [])]
    ontology_artifact = _artifact(
        db, store, run.recognition_run_id, run.owner_id, "ontology_snapshot"
    )
    if not source_artifact.storage_uri or not ontology_artifact.payload:
        raise RuntimeError("run input artifacts are incomplete")
    source_path = storage.resolve(run.recognition_run_id, source_artifact.storage_uri)
    if not source_path.is_file() or _hash_file(source_path) != run.document_hash:
        raise RuntimeError("source artifact hash mismatch")
    ontology = OntologySnapshot.model_validate(ontology_artifact.payload)
    if ontology.ontology_hash != run.ontology_snapshot_hash:
        raise RuntimeError("ontology artifact hash mismatch")

    metadata_ref = store.get_artifact(run.recognition_run_id, run.owner_id, "metadata")
    metadata_artifact = (
        db.get(DocumentAnalysisArtifact, metadata_ref.artifact_id)
        if metadata_ref is not None
        else None
    )
    if metadata_artifact is not None and metadata_artifact.payload is not None:
        if metadata_artifact.content_hash != metadata_ref.content_hash:
            raise CheckpointMismatch("metadata artifact head is invalid")
        metadata_payload = dict(metadata_artifact.payload)
        analysis_ir = DocumentIR.model_validate(metadata_payload["analysis"])
        metadata = MetadataSnapshot.model_validate(metadata_payload["metadata_snapshot"])
        if (
            analysis_ir.document_hash != run.document_hash
            or run.analysis_id != analysis_ir.analysis_id
            or run.metadata_snapshot_id != metadata.snapshot_id
            or metadata.analysis_id != analysis_ir.analysis_id
        ):
            raise CheckpointMismatch("metadata recovery identity mismatch")
    else:
        run = _stage_event(db, store, run, token, stage="parse", public_stage="parsing")
        check_interrupted()
        if _complete_pause_at_boundary(db, store, run, token, public_stage="parsing"):
            return
        docx_path = Path(ensure_docx(str(source_path)))
        check_interrupted()
        if _complete_pause_at_boundary(db, store, run, token, public_stage="parsing"):
            return
        analysis = analyze_word_core(
            docx_path,
            source_filename=run.filename,
            original_path=source_path,
        )
        check_interrupted()
        if _complete_pause_at_boundary(db, store, run, token, public_stage="parsing"):
            return
        analysis_ir = analysis.ir
        content, preview_warnings, _triples, parser_checkpoint = annotate_word(
            str(docx_path),
            engine=None,
            structure_only=True,
            rich_style=True,
            structure=analysis.structure,
            ir=analysis_ir,
        )
        check_interrupted()
        if _complete_pause_at_boundary(db, store, run, token, public_stage="parsing"):
            return
        if parser_checkpoint is not None:
            raise RuntimeError("structure-only parser returned an extraction checkpoint")
        section_tree = analysis.structure.section_tree
        if section_tree is None:
            raise ValueError("Word parser did not produce a section tree")
        common_payload = {
            "filename": run.filename,
            "content": content,
            "analysis": analysis_ir.model_dump(mode="json"),
            "section_tree": section_tree.to_dict(),
            "pagination": asdict(analysis.structure.pagination),
            "warnings": list(dict.fromkeys([*analysis.structure.warnings, *preview_warnings])),
        }
        run = _publish_artifact(
            db,
            store,
            run,
            token,
            kind="structure",
            status="ready",
            payload=common_payload,
            analysis_id=analysis_ir.analysis_id,
        )

        run = _stage_event(
            db,
            store,
            run,
            token,
            stage="metadata",
            public_stage="preparing_metadata",
        )
        if _complete_pause_at_boundary(
            db,
            store,
            run,
            token,
            public_stage="preparing_metadata",
        ):
            return
        warnings = list(common_payload["warnings"])
        generation_source = None
        summary_model_identity = None
        if run.metadata_mode == "generate_summary":
            try:
                client = get_local_llm()
                summarize_word_tree(
                    analysis.structure,
                    client,
                    should_stop_fn=should_stop,
                )
                if client is not None:
                    summary_model_identity = stable_id(
                        "summary-model",
                        [settings.local_llm_model, settings.local_llm_model_revision],
                    )
            except Exception:
                logger.warning(
                    "document summary failed; applying extractive fallback",
                    exc_info=True,
                )
                fallback_word_tree_summaries(analysis.structure)
                warnings.append("summary_failed_extractive_fallback")
            check_interrupted()
            if _complete_pause_at_boundary(
                db,
                store,
                run,
                token,
                public_stage="preparing_metadata",
            ):
                return
        elif run.metadata_mode == "cached_summary":
            # A cache hit is only legal for the same frozen dependency hash. The v1
            # implementation has no cross-run cache, so it honestly falls back.
            generation_source = "structure_only"
            warnings.append("cached_summary_unavailable_structure_only")
        else:
            generation_source = "structure_only"
        metadata = prepare_metadata(
            analysis_ir,
            section_tree=section_tree.to_dict(),
            summary_version=settings.word_tree_summary_prompt_version,
            summary_model_identity=summary_model_identity,
            generation_source=generation_source,
        )
        metadata_payload = {
            **common_payload,
            "section_tree": section_tree.to_dict(),
            "warnings": list(dict.fromkeys(warnings)),
            "metadata_snapshot": metadata.model_dump(mode="json"),
        }
        run = _publish_artifact(
            db,
            store,
            run,
            token,
            kind="metadata",
            status="ready",
            payload=metadata_payload,
            analysis_id=analysis_ir.analysis_id,
            metadata_snapshot_id=metadata.snapshot_id,
        )

    check_interrupted()
    if _complete_pause_at_boundary(db, store, run, token, public_stage="extracting"):
        return
    with model_scope(
        run_id=str(run.recognition_run_id), bind=db.get_bind(), should_stop=should_stop,
    ):
        adapter = configured_model_adapter()
        ranking_service, ranking_identity = _configured_ranking()
        # Budget enforcement/accounting is an audited run control, separate
        # from the frozen semantic dependencies. A pause/resume picks up its
        # current value without changing existing policy or epoch identities.
        ranking_service = RankingService(
            ranking_service.policy, ranking_service.model,
            budget_enabled=run.ranking_budget_enabled,
        )
    check_interrupted()
    final_fingerprint = _recognition_fingerprint(
        run, analysis_ir, metadata, ontology, adapter, ranking_identity, origin
    )
    if run.run_fingerprint is not None and run.run_fingerprint != final_fingerprint:
        if ranking_service.model is None and ranking_identity.get("unavailable_reason") in {
            "ranking_cuda_out_of_memory", "ranking_cuda_probe_timeout",
            "ranking_cuda_probe_failed", "ranking_cuda_unavailable", "ranking_cuda_kernel_failed",
        }:
            previous = _restore_ranking_state(
                db, store, run, final_fingerprint=run.run_fingerprint,
            )
            prior_service = previous.get("service") or {}
            prior_model = prior_service.get("model_identity")
            rechecked_identity = {
                **ranking_identity, "model_identity": prior_model, "unavailable_reason": None,
            }
            known_dependencies_match = bool(prior_model) and _recognition_fingerprint(
                run, analysis_ir, metadata, ontology, adapter, rechecked_identity, origin,
            ) == run.run_fingerprint
            # A failed probe supplies no new numeric identity. Preserve the last
            # successful checkpoint until it can be checked; other known input
            # changes still fail the fingerprint comparison below. A crash before
            # the first ranking state leaves nothing to compare and permits no work.
            if known_dependencies_match or not previous:
                pause = (prior_service.get("policy") or ranking_identity["policy"])[
                    "failure_policy"
                ] == "pause"
                _finish(
                    db, store, store.get_owned(run.recognition_run_id, run.owner_id), token,
                    status="paused" if pause else "failed",
                    public_status="paused" if pause else "retryable_failure",
                    public_stage="extracting",
                    progress=RunProgress.model_validate(run.progress or {}),
                    stop_reason="ranking_paused" if pause else "ranking_unavailable",
                    error={
                        "code": "RANKING_PAUSED" if pause else "RANKING_UNAVAILABLE",
                        "message": "GPU 排序设备暂时无法核验，已保留冻结身份和恢复水位",
                        "retryable": True,
                    },
                )
                return
        raise FingerprintMismatch("recognition dependencies changed after checkpointing")
    if run.run_fingerprint is None or run.stage != "recognition":
        run = store.update_stage(
            run.recognition_run_id,
            run.owner_id,
            token,
            expected_revision=run.revision,
            stage="recognition",
            analysis_id=analysis_ir.analysis_id,
            metadata_snapshot_id=metadata.snapshot_id,
            run_fingerprint=final_fingerprint,
        )
        db.commit()

    checkpoint = _restore_recognition_checkpoint(
        db,
        store,
        run,
        final_fingerprint=final_fingerprint,
    )
    index = RecordIndex(analysis_ir)
    ranking_state = _restore_ranking_state(
        db, store, run, final_fingerprint=final_fingerprint
    )
    model_call_state = _restore_model_call_state(
        db, store, run, final_fingerprint=final_fingerprint
    )

    def progress_hook(_boundary: str) -> bool:
        execution = store.heartbeat(
            run.recognition_run_id,
            run.owner_id,
            token,
            lease_seconds=settings.document_analysis_lease_seconds,
        )
        db.commit()
        return not execution.pause_requested and not execution.cancel_requested

    def batch_hook(batch: ExecutionBatch) -> None:
        _persist_recognition_batch(
            db,
            store,
            store.get_owned(run.recognition_run_id, run.owner_id),
            token,
            final_fingerprint=final_fingerprint,
            ontology=ontology,
            ir=analysis_ir,
            metadata=metadata,
            index=index,
            batch=batch,
        )

    def ranking_hook(state: dict) -> None:
        check_interrupted()
        _persist_ranking_state(
            db, store, run, token, final_fingerprint=final_fingerprint, state=state
        )

    def model_call_hook(state: dict) -> None:
        check_interrupted()
        _persist_model_call_state(
            db, store, run, token, final_fingerprint=final_fingerprint, state=state
        )

    try:
        with model_scope(
            run_id=str(run.recognition_run_id),
            bind=db.get_bind(),
            should_stop=should_stop,
        ):
            result = OntologyGuidedExecutor(
                ontology=ontology,
                # The frozen snapshot is authoritative; a live engine must not widen it.
                engine=object(),
                adapter=adapter,
                max_tasks=settings.evidence_max_tasks,
                max_model_calls_per_record=settings.document_analysis_max_model_calls_per_record,
                progress_hook=progress_hook,
                ranking_service=ranking_service,
                priority_paths=priority_paths,
            ).run(
                recognition_run_id=str(run.recognition_run_id),
                run_fingerprint=final_fingerprint,
                ir=analysis_ir,
                metadata=metadata,
                root_class_iri=run.root_class_iri,
                root_class_label=run.root_class_label,
                filename=run.filename,
                run_revision=run.revision,
                event_head=run.event_head,
                resume_state=(
                    {
                        "task_outcomes": checkpoint.task_outcomes,
                        "frontier": checkpoint.frontier,
                        "recall_ledger": checkpoint.recall_ledger,
                        "dependency_index": checkpoint.dependency_index,
                        "diagnostics": checkpoint.diagnostics,
                    }
                    if checkpoint is not None
                    else None
                ),
                batch_hook=batch_hook,
                ranking_state=ranking_state or (checkpoint.ranking_state if checkpoint else None),
                ranking_hook=ranking_hook,
                model_call_state=(
                    model_call_state or (checkpoint.model_call_state if checkpoint else None)
                ),
                model_call_hook=model_call_hook,
            )
    except ValueError as exc:
        if checkpoint is not None:
            raise CheckpointMismatch(str(exc)) from exc
        raise
    finally:
        close_ranking_model = getattr(ranking_service.model, "close", None)
        if callable(close_ranking_model):
            close_ranking_model()
    check_interrupted()
    store.assert_fence(run.recognition_run_id, run.owner_id, token)
    run = store.get_owned(run.recognition_run_id, run.owner_id)
    durable_checkpoint = _restore_recognition_checkpoint(
        db,
        store,
        run,
        final_fingerprint=final_fingerprint,
    )
    if durable_checkpoint is not None:
        # Every successful model attempt commits its complete graph and object
        # heads atomically with the checkpoint receipt.  Reuse that immutable
        # boundary for terminal publication so a crash between graph publishing
        # and the terminal state cannot move durable heads beyond the checkpoint.
        terminal_graph = GraphSnapshot.model_validate(durable_checkpoint.graph_state)
    else:
        # No logical task ran (for example, an empty ontology menu or a pause
        # before the first call), so there is no recognition checkpoint to reuse.
        terminal_graph = result.graph
        run = _persist_graph_objects(db, store, run, token, terminal_graph, result.events)
        selection_registry = build_selection_registry(
            recognition_run_id=str(run.recognition_run_id),
            analysis_id=analysis_ir.analysis_id,
            graph=terminal_graph,
            index=index,
        )
        graph_snapshot_id = stable_id(
            "graph-snapshot",
            [str(run.recognition_run_id), terminal_graph.generated_from_hash, run.event_head],
        )
        graph_payload = {
            "snapshot_id": graph_snapshot_id,
            "analysis_id": analysis_ir.analysis_id,
            "ontology_snapshot_id": ontology.snapshot_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "graph": terminal_graph.model_dump(mode="json"),
            "selection_registry": selection_registry,
            "retrieval_plans": result.retrieval_plans,
            "dependency_index": (
                durable_checkpoint.dependency_index
                if durable_checkpoint is not None
                else {"requirements": {}, "invalidated": [], "subscriptions": {}}
            ),
            "diagnostics": result.diagnostics,
            "ranking_state": result.ranking_state,
        }
        run = _publish_artifact(
            db,
            store,
            run,
            token,
            kind="graph",
            status=terminal_graph.artifact_status,
            payload=graph_payload,
            analysis_id=analysis_ir.analysis_id,
            metadata_snapshot_id=metadata.snapshot_id,
            graph_snapshot_id=graph_snapshot_id,
        )

    # Stop causes are known after the last durable batch. Keep that batch's
    # immutable graph and coverage counts, but publish the terminal execution
    # outcome instead of its still-running progress marker.
    terminal_progress = terminal_graph.progress.model_copy(update={
        "stop_reason": result.graph.progress.stop_reason,
        "completion": result.graph.progress.completion,
    })
    current = store.get_owned(run.recognition_run_id, run.owner_id)
    if _complete_pause_at_boundary(db, store, current, token, public_stage="extracting"):
        return

    if result.graph.progress.stop_reason == "ranking_paused":
        _finish(
            db, store, current, token,
            status="paused", public_status="paused", public_stage="extracting",
            progress=terminal_progress, stop_reason="ranking_paused",
            error={
                "code": "RANKING_PAUSED",
                "message": "排序能力未完成，已按本次运行的冻结政策暂停并保留覆盖",
                "retryable": True,
            },
        )
        return

    if adapter is None:
        _finish(
            db,
            store,
            current,
            token,
            status="failed",
            public_status="retryable_failure",
            public_stage="extracting",
            progress=terminal_progress,
            stop_reason="recognition_model_not_configured",
            error={
                "code": "MODEL_UNAVAILABLE",
                "message": "关系识别模型未配置；已保留结构、元数据和真实未完成覆盖",
                "retryable": True,
            },
        )
        return
    terminal_status = (
        "finished" if terminal_progress.completion == "in_scope_complete" else "failed"
    )
    _finish(
        db,
        store,
        current,
        token,
        status=terminal_status,
        public_status="finished" if terminal_status == "finished" else "retryable_failure",
        public_stage="complete" if terminal_status == "finished" else "extracting",
        progress=terminal_progress,
        stop_reason=terminal_progress.stop_reason,
        error=(
            None
            if terminal_status == "finished"
            else {
                "code": "ANALYSIS_INCOMPLETE",
                "message": "部分记录未完成，已保留可回放的部分图谱",
                "retryable": True,
            }
        ),
    )


def _execute_dispatched_run(
    db: Session,
    store: DocumentAnalysisRunStore,
    run: DocumentAnalysisRun,
    token: str,
) -> None:
    """Execute a claimed generation with lease renewal and fenced failures."""

    recognition_run_id = run.recognition_run_id
    keeper = _LeaseKeeper(
        bind=db.get_bind(),
        recognition_run_id=recognition_run_id,
        owner_id=run.owner_id,
        token=token,
        lease_seconds=settings.document_analysis_lease_seconds,
    )
    keeper.start()
    try:
        try:
            _execute_claimed(
                db,
                store,
                run,
                token,
                interruption_check=keeper.raise_if_lost,
                should_stop=keeper.should_stop,
            )
        except (FenceViolation, RunDeleted, RunNotFound, WorkerInterrupted):
            # A control action or a newer worker owns the generation. Never
            # publish a late error or overwrite its state.
            db.rollback()
        except ModelCancelled:
            db.rollback()
            try:
                current = store.get_owned(run.recognition_run_id, run.owner_id)
                store.assert_fence(current.recognition_run_id, current.owner_id, token)
                public_stage = {
                    "ingest": "accepted", "parse": "parsing", "metadata": "preparing_metadata",
                }.get(current.stage, "extracting")
                if _complete_pause_at_boundary(
                    db, store, current, token, public_stage=public_stage
                ):
                    return
                # An interrupted request without a durable operator pause is
                # incomplete. Preserve its reservations for an explicit retry.
                _finish(
                    db, store, current, token,
                    status="failed", public_status="retryable_failure", public_stage=public_stage,
                    progress=RunProgress.model_validate(current.progress or {}),
                    stop_reason="model_interrupted",
                    error={"code": "MODEL_INTERRUPTED", "message": "模型请求被中断，已保留恢复水位",
                           "retryable": True},
                )
            except (FenceViolation, RunDeleted, RunNotFound):
                # Cancel/delete/replacement has already revoked this worker.
                db.rollback()
        except (FingerprintMismatch, CheckpointMismatch) as exc:
            db.rollback()
            logger.error(
                "document-analysis recovery rejected for %s: %s",
                recognition_run_id,
                exc,
            )
            try:
                current = store.get_owned(run.recognition_run_id, run.owner_id)
                store.assert_fence(current.recognition_run_id, current.owner_id, token)
                _finish(
                    db,
                    store,
                    current,
                    token,
                    status="blocked_dependency",
                    public_status="blocked_dependency",
                    public_stage="extracting",
                    progress=RunProgress.model_validate(current.progress or {}),
                    stop_reason="fingerprint_mismatch",
                    error={
                        "code": (
                            "FINGERPRINT_MISMATCH"
                            if isinstance(exc, FingerprintMismatch)
                            else "CHECKPOINT_MISMATCH"
                        ),
                        "message": "运行依赖或恢复水位与冻结检查点不一致",
                        "retryable": False,
                    },
                )
            except (FenceViolation, RunDeleted, RunNotFound):
                db.rollback()
        except Exception as exc:
            db.rollback()
            logger.exception("document-analysis run failed: %s", recognition_run_id)
            try:
                current = store.get_owned(run.recognition_run_id, run.owner_id)
                store.assert_fence(current.recognition_run_id, current.owner_id, token)
                code, message = _failure_payload(exc)
                progress = RunProgress.model_validate(current.progress or {})
                _finish(
                    db,
                    store,
                    current,
                    token,
                    status="failed",
                    public_status="retryable_failure",
                    public_stage={
                        "ingest": "accepted",
                        "parse": "parsing",
                        "metadata": "preparing_metadata",
                        "recognition": "extracting",
                    }.get(current.stage, "extracting"),
                    progress=progress,
                    stop_reason=code.lower(),
                    error={
                        "code": code, "message": message, "retryable": True,
                        # Internal diagnostics carry no prompts, exception
                        # values or credentials. Public error mapping omits them.
                        "failure_type": type(exc).__name__,
                        "failure_frames": [
                            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
                        ],
                    },
                )
            except (FenceViolation, RunDeleted, RunNotFound):
                db.rollback()
    finally:
        keeper.stop()


def dispatch_run(
    recognition_run_id: UUID | str,
    *,
    bind: Engine | None = None,
) -> None:
    """Claim and execute one specific run; safe to deliver more than once."""

    factory = _session_factory(bind)
    db = factory()
    try:
        run = db.get(DocumentAnalysisRun, recognition_run_id)
        if (
            run is None
            or run.execution_status not in {"queued", "running", "pausing"}
            or run.deletion_state != "none"
        ):
            return
        store = DocumentAnalysisRunStore(db)
        try:
            token = store.claim(
                run.recognition_run_id,
                run.owner_id,
                actor="durable-dispatcher",
                worker_id=settings.document_analysis_worker_id,
                lease_seconds=settings.document_analysis_lease_seconds,
            )
            db.commit()
        except (LeaseBusy, InvalidRunState, RunNotFound, RunDeleted):
            db.rollback()
            return
        run = store.get_owned(run.recognition_run_id, run.owner_id)
        _execute_dispatched_run(db, store, run, token)
    finally:
        db.close()


def dispatch_next_run(
    *,
    bind: Engine | None = None,
    worker_id: str | None = None,
) -> bool:
    """Atomically claim and execute one available durable queue head."""

    factory = _session_factory(bind)
    db = factory()
    try:
        store = DocumentAnalysisRunStore(db)
        try:
            claimed = store.claim_next(
                actor="durable-dispatcher",
                worker_id=worker_id or settings.document_analysis_worker_id,
                lease_seconds=settings.document_analysis_lease_seconds,
            )
            if claimed is None:
                db.rollback()
                return False
            run, token = claimed
            db.commit()
        except (LeaseBusy, InvalidRunState, RunNotFound, RunDeleted):
            db.rollback()
            return False
        run = store.get_owned(run.recognition_run_id, run.owner_id)
        _execute_dispatched_run(db, store, run, token)
        return True
    finally:
        db.close()


def recover_document_analysis_runs(
    *,
    bind: Engine | None = None,
    limit: int = 100,
) -> int:
    """Synchronously drain at most ``limit`` available durable queue heads."""

    if limit < 1:
        raise ValueError("recovery limit must be positive")
    recovered = 0
    while recovered < limit and dispatch_next_run(bind=bind):
        recovered += 1
    return recovered


class DocumentAnalysisDispatcher:
    """Persistent, bounded dispatcher for queued work and expired leases."""

    def __init__(
        self,
        *,
        bind: Engine | None = None,
        poll_interval_seconds: float | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.bind = bind
        self.poll_interval_seconds = (
            settings.document_analysis_dispatch_poll_seconds
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        self.max_concurrency = (
            settings.document_analysis_dispatch_concurrency
            if max_concurrency is None
            else max_concurrency
        )
        if self.poll_interval_seconds <= 0:
            raise ValueError("dispatcher poll interval must be positive")
        if self.max_concurrency < 1:
            raise ValueError("dispatcher concurrency must be positive")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stopping: asyncio.Event | None = None
        self._runner: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()

    @property
    def is_running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    @property
    def worker_task_count(self) -> int:
        return len(self._workers)

    def start(self) -> asyncio.Task[None]:
        """Start the dispatcher on the current event loop."""

        global _active_document_analysis_dispatcher
        if self.is_running:
            raise RuntimeError("document-analysis dispatcher is already running")
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._runner = self._loop.create_task(
            self._run(),
            name="document-analysis-dispatcher",
        )
        _active_document_analysis_dispatcher = self
        self._wake.set()
        return self._runner

    def notify(self) -> bool:
        """Best-effort, thread-safe notification that durable work may exist."""

        loop = self._loop
        wake = self._wake
        if loop is None or wake is None or not self.is_running:
            return False
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            return False
        return True

    async def stop(self) -> None:
        """Stop claiming new work and await every in-flight worker."""

        global _active_document_analysis_dispatcher
        runner = self._runner
        if runner is None:
            return
        if self._stopping is not None:
            self._stopping.set()
        if self._wake is not None:
            self._wake.set()
        await runner
        self._runner = None
        self._loop = None
        self._wake = None
        self._stopping = None
        if _active_document_analysis_dispatcher is self:
            _active_document_analysis_dispatcher = None

    async def _run(self) -> None:
        self._workers = {
            asyncio.create_task(
                self._worker(slot),
                name=f"document-analysis-dispatcher-{slot}",
            )
            for slot in range(self.max_concurrency)
        }
        try:
            await asyncio.gather(*self._workers)
        finally:
            self._workers.clear()

    async def _worker(self, slot: int) -> None:
        stopping = self._stopping
        wake = self._wake
        if stopping is None or wake is None:
            return
        worker_id = f"{settings.document_analysis_worker_id}:{slot}"
        while not stopping.is_set():
            try:
                worked = await asyncio.to_thread(
                    dispatch_next_run,
                    bind=self.bind,
                    worker_id=worker_id,
                )
            except asyncio.CancelledError:
                raise
            except BaseException:
                logger.exception("document-analysis dispatcher slot %s failed", slot)
                worked = False
            if stopping.is_set():
                return
            if worked:
                # Do not impose a scan-batch ceiling: keep draining while this
                # slot can atomically claim work.
                continue
            wake.clear()
            if stopping.is_set():
                return
            try:
                await asyncio.wait_for(
                    wake.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                pass


_active_document_analysis_dispatcher: DocumentAnalysisDispatcher | None = None


def notify_document_analysis_dispatcher() -> bool:
    """Wake the in-process dispatcher, returning false outside app lifespan."""

    dispatcher = _active_document_analysis_dispatcher
    return dispatcher.notify() if dispatcher is not None else False
