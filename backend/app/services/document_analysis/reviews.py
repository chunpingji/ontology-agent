"""Explicit human review and bounded repair, isolated from legacy fact submission."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
)
from app.models.document_analysis_review import (
    DocumentPropertyRepair,
    DocumentPropertyReview,
    DocumentPropertyReviewHead,
)
from app.schemas.document_analysis_review import (
    PropertyRepair,
    PropertyRepairResponse,
    PropertyReview,
    PropertyReviewResponse,
)
from app.services import audit
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
)
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    HeadConflict,
    InvalidRunState,
    RunStoreError,
    content_hash,
)
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask

REVIEW_ROLES = {"senior_analyst", "qa"}
QUIET_STATES = {"finished", "paused", "failed"}
TERMINAL_REPAIR_STATES = {"completed", "unresolved", "failed", "cancelled"}


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _review_rows(db, run):
    return list(db.scalars(select(DocumentPropertyReview).where(
        DocumentPropertyReview.recognition_run_id == run.recognition_run_id,
    ).order_by(DocumentPropertyReview.created_at, DocumentPropertyReview.review_id)))


def _review_heads(db, run):
    return list(db.scalars(select(DocumentPropertyReview).join(
        DocumentPropertyReviewHead,
        DocumentPropertyReviewHead.review_id == DocumentPropertyReview.review_id,
    ).where(DocumentPropertyReview.recognition_run_id == run.recognition_run_id).order_by(
        DocumentPropertyReview.candidate_id, DocumentPropertyReview.candidate_revision,
    )))


def reviewed_snapshot_id(db, run, base_id=None):
    base = base_id if base_id is not None else run.graph_snapshot_id
    heads = _review_heads(db, run)
    if not heads or not base:
        return base
    return stable_id("reviewed-graph", [base, [str(row.review_id) for row in heads]])


def review_overlay(db, run, payload):
    """Build a read projection; never alter immutable artifacts or candidate heads."""
    heads = _review_heads(db, run)
    if not heads:
        return payload
    detached = deepcopy(payload)
    decisions = {(row.candidate_id, row.candidate_revision): row.decision for row in heads}
    dependencies = DependencyIndex.from_snapshot(detached.get("dependency_index"))
    for candidate in detached["graph"]["properties"]:
        key = (candidate["candidate_id"], candidate["revision"])
        if key in decisions:
            candidate["independent_review"] = decisions[key]
            if decisions[key] == "rejected":
                dependencies.invalidate(f"{key[0]}@{key[1]}")
    detached["dependency_index"] = dependencies.snapshot()
    detached["snapshot_id"] = reviewed_snapshot_id(db, run, payload["snapshot_id"])
    return detached


def review_operations(db, run):
    """All immutable review boundaries, including confirmations, for cold replay."""
    operations = [
        {**deepcopy(row.payload), "review_id": str(row.review_id),
         "candidate_id": row.candidate_id, "candidate_revision": row.candidate_revision,
         "review_revision": row.revision, "decision": row.decision,
         "graph_snapshot_id": row.graph_snapshot_id}
        for row in _review_rows(db, run)
    ]
    return sorted(operations, key=lambda item: item["sequence"])


def repair_operations(db, run):
    rows = db.scalars(select(DocumentPropertyRepair).where(
        DocumentPropertyRepair.recognition_run_id == run.recognition_run_id,
    ).order_by(DocumentPropertyRepair.created_at, DocumentPropertyRepair.operation_id))
    return [
        {**deepcopy(row.payload), "operation_id": str(row.operation_id),
         "review_id": str(row.review_id), "status": row.status,
         "base_checkpoint_artifact_id": row.base_checkpoint_artifact_id,
         "result": deepcopy(row.result)} for row in rows
    ]


def pending_review_operations(db, run):
    return [item for item in repair_operations(db, run)
            if item["status"] not in TERMINAL_REPAIR_STATES]


def cancel_pending_repair_operations(db, run, *, action):
    """Close pending repairs inside the fenced lifecycle-control transaction.

    The caller has locked execution then run, and revoked the execution token.
    Review inputs, original receipts and already committed cost remain intact.
    """
    if not ((action == "cancel" and run.execution_status == "cancelled")
            or (action == "delete" and run.deletion_state == "requested")):
        raise InvalidRunState("repair cancellation requires a fenced lifecycle transition")
    rows = list(db.scalars(select(DocumentPropertyRepair).where(
        DocumentPropertyRepair.recognition_run_id == run.recognition_run_id,
        DocumentPropertyRepair.status.in_({"queued", "running"}),
    ).with_for_update()))
    stamp = datetime.now(UTC)
    for row in rows:
        row.status = "cancelled"
        row.updated_at = stamp
        row.result = {
            **deepcopy(row.result),
            "reason_code": "run_cancelled" if action == "cancel" else "run_deleted",
            "reason": "运行已取消，局部重识别已停止。" if action == "cancel"
            else "运行已请求删除，局部重识别已停止。",
        }
    db.flush()
    return [str(row.operation_id) for row in rows]


def mark_repair_operation(db, run_id, owner_id, token, operation_id, status, result=None):
    """Worker-only operation update; caller commits with its graph/checkpoint batch."""
    store = DocumentAnalysisRunStore(db)
    store.assert_fence(run_id, owner_id, token, for_update=True)
    row = db.scalar(select(DocumentPropertyRepair).where(
        DocumentPropertyRepair.recognition_run_id == run_id,
        DocumentPropertyRepair.operation_id == UUID(str(operation_id)),
    ).with_for_update())
    if row is None:
        raise HeadConflict("repair operation does not belong to the run")
    if status not in {"queued", "running", *TERMINAL_REPAIR_STATES}:
        raise ValueError("invalid repair status")
    body = deepcopy(result if result is not None else row.result)
    if row.status in TERMINAL_REPAIR_STATES and (row.status != status or row.result != body):
        raise HeadConflict("terminal repair result is immutable")
    row.status, row.result, row.updated_at = status, body, datetime.now(UTC)
    db.flush()
    return row


class DocumentPropertyReviewService:
    def __init__(self, db, identity):
        self.db, self.identity = db, identity
        self.application = DocumentAnalysisApplication(db, ontology_engine=None)
        self.store = self.application.store

    def _run(self, run_id, *, lock=False):
        run = self.application.get_run(run_id, self.identity.username)
        if lock:
            # All lifecycle controls lock execution before the public run head.
            self.store._execution_for_update(run.recognition_run_id)
            run = self.store._run_for_update(run.recognition_run_id, self.identity.username)
        return run

    def _can_write(self, run):
        return (
            self.identity.role in REVIEW_ROLES and run.deletion_state == "none"
            and run.execution_status in QUIET_STATES
            and (run.expires_at is None or _aware(run.expires_at) > datetime.now(UTC))
        )

    def _assert_write(self, run, revision):
        if self.identity.role not in REVIEW_ROLES:
            raise DocumentAnalysisError(
                "ROLE_FORBIDDEN", "仅高级分析师和 QA 可审核", status_code=403,
            )
        if not self._can_write(run):
            raise DocumentAnalysisError(
                "RUN_STATE_CONFLICT", "请先暂停运行；已删除、取消或过期的运行不可审核或修复",
                status_code=409, current_revision=run.revision,
            )
        if run.revision != revision:
            raise HeadConflict("stale run revision", expected=revision, actual=run.revision)

    @staticmethod
    def public_review(row):
        return PropertyReview(
            review_id=row.review_id, revision=row.revision,
            candidate_id=row.candidate_id, candidate_revision=row.candidate_revision,
            graph_snapshot_id=row.graph_snapshot_id, decision=row.decision,
            reason_code=row.payload["reason_code"], reason=row.payload["reason"],
            author=row.owner_id, author_role=row.actor_role, created_at=_aware(row.created_at),
        ).model_dump(mode="json")

    @staticmethod
    def public_repair(row):
        target = row.payload["target"]
        return PropertyRepair(
            operation_id=row.operation_id, review_id=row.review_id, status=row.status,
            candidate_id=target["candidate_id"], candidate_revision=target["candidate_revision"],
            subject_ref={"entity_id": target["subject_ref"]["id"],
                         "revision": target["subject_ref"]["revision"]},
            predicate_iri=target["predicate_iri"],
            record_ids=target["record_ids"], result=row.result,
            created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
        ).model_dump(mode="json")

    def listing(self, run_id):
        run = self._run(run_id)
        self.application.assert_artifacts_readable(run)
        return {"run_revision": run.revision,
                "items": [self.public_review(row) for row in _review_rows(self.db, run)],
                "heads": [self.public_review(row) for row in _review_heads(self.db, run)],
                "can_review": self._can_write(run), "can_repair": self._can_write(run)}

    def repairs(self, run_id):
        run = self._run(run_id)
        self.application.assert_artifacts_readable(run)
        rows = self.db.scalars(select(DocumentPropertyRepair).where(
            DocumentPropertyRepair.recognition_run_id == run.recognition_run_id,
        ).order_by(DocumentPropertyRepair.created_at, DocumentPropertyRepair.operation_id))
        return {"run_revision": run.revision, "items": [self.public_repair(row) for row in rows],
                "can_repair": self._can_write(run)}

    def _replay(self, model, run, request):
        row = self.db.scalar(select(model).where(
            model.recognition_run_id == run.recognition_run_id,
            model.request_key == request.request_key,
        ))
        if row is not None and row.request_hash != content_hash(request.model_dump(mode="json")):
            raise DocumentAnalysisError("IDEMPOTENCY_CONFLICT", "提交标识已用于其他内容",
                                        status_code=409, current_revision=run.revision)
        if row is not None and self.identity.role not in REVIEW_ROLES:
            raise DocumentAnalysisError("ROLE_FORBIDDEN", "当前角色不可审核", status_code=403)
        return row

    def _checkpoint(self, run):
        reference = self.store.get_artifact(
            run.recognition_run_id, run.owner_id, "recognition_checkpoint",
        )
        if reference is None:
            raise DocumentAnalysisError("REPAIR_UNAVAILABLE", "运行没有可回放的识别检查点",
                                        status_code=409)
        artifact = self.db.get(DocumentAnalysisArtifact, reference.artifact_id)
        if artifact is None or content_hash(artifact.payload) != reference.content_hash:
            raise HeadConflict("checkpoint content hash mismatch")
        payload, _ = self.application._artifact_payload(run, "recognition_checkpoint")
        if payload.get("run_fingerprint") != run.run_fingerprint:
            raise DocumentAnalysisError(
                "REPAIR_UNAVAILABLE", "识别检查点身份不一致", status_code=409,
            )
        return reference, payload

    def _target(self, run, request):
        if reviewed_snapshot_id(self.db, run) != request.graph_snapshot_id:
            raise HeadConflict("stale graph snapshot")
        head = self.db.get(DocumentRunCandidateHead, (run.recognition_run_id, request.candidate_id))
        if head is None or head.revision != request.candidate_revision:
            raise HeadConflict("stale candidate revision")
        candidate = self.db.get(DocumentRunCandidate, (
            run.recognition_run_id, request.candidate_id, request.candidate_revision,
        ))
        if candidate is None or candidate.kind != "property":
            raise DocumentAnalysisError("INVALID_REVIEW_TARGET", "首版仅支持数据属性审核",
                                        status_code=422)
        if content_hash(candidate.payload) != candidate.payload_hash:
            raise HeadConflict("candidate content hash mismatch")
        graph, _ = self.application._artifact_payload(run, "graph")
        displayed = next((item for item in graph["graph"]["properties"]
                          if item["candidate_id"] == request.candidate_id
                          and item["revision"] == request.candidate_revision), None)
        if displayed is None:
            raise HeadConflict("candidate is not in the current graph")
        if content_hash(displayed) != candidate.payload_hash:
            raise HeadConflict("graph and exact candidate payload disagree")
        _reference, checkpoint = self._checkpoint(run)
        outcomes = checkpoint.get("task_outcomes", [])
        original = next((item["task"] for item in reversed(outcomes) if any(
            prop["candidate_id"] == candidate.candidate_id
            for prop in item.get("outcome", {}).get("properties", [])
        )), None)
        if original is None:
            raise DocumentAnalysisError(
                "REPAIR_UNAVAILABLE", "候选缺少可回放原始任务", status_code=409,
            )
        task = RecognitionTask.model_validate(original)
        prop = candidate.payload
        if (task.subject.entity_id != prop["subject_ref"]["id"]
                or task.subject.revision != prop["subject_ref"]["revision"]
                or task.predicate_iri != prop["predicate_iri"]):
            raise HeadConflict("candidate original task target mismatch")
        structured = self.application._artifact_payload(run, "structure")
        if structured is None:
            raise DocumentAnalysisError("REPAIR_UNAVAILABLE", "原文结构不可用", status_code=409)
        ir = DocumentIR.model_validate(structured[0]["analysis"])
        if ir.document_hash != run.document_hash:
            raise HeadConflict("source document hash mismatch")
        index = RecordIndex(ir)
        if task.record_id not in index.by_id:
            raise HeadConflict("original source record missing")
        evidence_ids = []
        for name, references in prop.items():
            if not name.endswith("evidence_refs") or not isinstance(references, list):
                continue
            for anchor in references:
                ir.resolve(anchor)
                evidence_ids.append(anchor["evidence_id"])
        records = [task.record_id]
        for evidence_id in dict.fromkeys(evidence_ids):
            records.extend(record.record_id for record in index.records_by_evidence[evidence_id])
        return {
            "sequence": run.event_head + 1,
            "reason": request.reason, "reason_code": request.reason_code,
            "after_outcomes": len(outcomes), "subject_ref": prop["subject_ref"],
            "predicate_iri": prop["predicate_iri"], "original_task": original,
            "record_ids": list(dict.fromkeys(records))[:16],
            "source_record_count": len(set(records)),
            "source_evidence_ids": list(dict.fromkeys(evidence_ids)),
            "document_hash": run.document_hash, "structure_hash": ir.structure_hash,
        }

    def _event(self, run, key):
        response = self.application.status_response(run, role=self.identity.role)
        self.store.append_owner_event(
            run.recognition_run_id, run.owner_id, expected_head=run.event_head,
            event_key=key, event_type="run_state", payload={
                "contract_version": run.contract_version,
                "recognition_run_id": str(run.recognition_run_id),
                "run_revision": run.revision + 1, "event_head": run.event_head + 1,
                "artifact_revision": run.artifact_revision,
                "status": response["status"], "stage": response["stage"],
            },
        )

    def create_review(self, run_id, request):
        try:
            run = self._run(run_id, lock=True)
            previous = self._replay(DocumentPropertyReview, run, request)
            if previous:
                return deepcopy(previous.receipt), True
            self._assert_write(run, request.expected_run_revision)
            target = self._target(run, request)
            key = (run.recognition_run_id, request.candidate_id, request.candidate_revision)
            head = self.db.get(DocumentPropertyReviewHead, key)
            if (head.revision if head else 0) != request.expected_review_revision:
                raise HeadConflict("stale review revision")
            row = DocumentPropertyReview(
                recognition_run_id=run.recognition_run_id, owner_id=run.owner_id,
                actor_role=self.identity.role, request_key=request.request_key,
                request_hash=content_hash(request.model_dump(mode="json")),
                candidate_id=request.candidate_id, candidate_revision=request.candidate_revision,
                revision=request.expected_review_revision + 1, decision=request.decision,
                graph_snapshot_id=request.graph_snapshot_id, payload=target,
            )
            self.db.add(row)
            self.db.flush()
            if head:
                head.review_id, head.revision = row.review_id, row.revision
            else:
                self.db.add(DocumentPropertyReviewHead(
                    recognition_run_id=key[0], candidate_id=key[1], candidate_revision=key[2],
                    review_id=row.review_id, revision=row.revision,
                ))
            self.db.flush()
            self._event(run, f"property-review:{row.review_id}")
            audit.append(self.db, "document_analysis.property_review", actor=run.owner_id,
                         details={"run_id": str(run.recognition_run_id),
                                  "review_id": str(row.review_id), "decision": row.decision,
                                  "candidate_id": row.candidate_id,
                                  "candidate_revision": row.candidate_revision}, commit=False)
            response = PropertyReviewResponse(
                review=self.public_review(row),
                run=self.application.status_response(run, role=self.identity.role),
            ).model_dump(mode="json")
            row.receipt = response
            self.db.commit()
            return response, False
        except Exception as exc:
            if isinstance(exc, IntegrityError):
                self.db.rollback()
                previous = self._replay(DocumentPropertyReview, self._run(run_id), request)
                if previous:
                    return deepcopy(previous.receipt), True
            self._raise(exc)

    def create_repair(self, run_id, request):
        try:
            run = self._run(run_id, lock=True)
            previous = self._replay(DocumentPropertyRepair, run, request)
            if previous:
                return deepcopy(previous.receipt), True
            self._assert_write(run, request.expected_run_revision)
            review = self.db.get(DocumentPropertyReview, request.review_id)
            if review is None or review.recognition_run_id != run.recognition_run_id:
                raise DocumentAnalysisError("RUN_NOT_FOUND", "审核记录不存在", status_code=404)
            head = self.db.get(DocumentPropertyReviewHead, (
                run.recognition_run_id, review.candidate_id, review.candidate_revision,
            ))
            candidate_head = self.db.get(DocumentRunCandidateHead, (
                run.recognition_run_id, review.candidate_id,
            ))
            if (review.decision != "rejected" or head is None or head.review_id != review.review_id
                    or candidate_head is None
                    or candidate_head.revision != review.candidate_revision):
                raise HeadConflict("repair requires the current exact rejected candidate")
            if self.db.scalar(select(DocumentPropertyRepair.operation_id).where(
                DocumentPropertyRepair.review_id == review.review_id,
            )):
                raise HeadConflict("this review already has a bounded repair operation")
            reference, checkpoint = self._checkpoint(run)
            target = next(item for item in review_operations(self.db, run)
                          if item["review_id"] == str(review.review_id))
            row = DocumentPropertyRepair(
                recognition_run_id=run.recognition_run_id, review_id=review.review_id,
                request_key=request.request_key,
                request_hash=content_hash(request.model_dump(mode="json")),
                base_checkpoint_artifact_id=reference.artifact_id, status="queued",
                payload={"after_outcomes": len(checkpoint.get("task_outcomes", [])),
                         "origin_execution_status": run.execution_status,
                         "base_checkpoint_content_hash": reference.content_hash,
                         "target": target, "max_tasks": 16, "max_model_calls": 32},
            )
            self.db.add(row)
            execution = self.store._execution_for_update(run.recognition_run_id)
            self.store._revoke_execution(execution)
            execution.pause_requested = execution.cancel_requested = False
            execution.recovery_attempts = 0
            execution.last_progress_at = datetime.now(UTC)
            execution.recovery_event_head = run.event_head
            self.store._cas_run(run, {
                "execution_status": "queued", "stage": "recognition", "finished_at": None,
                "paused_at": None, "expires_at": None, "stop_reason": None, "error": None,
                "control_action": "repair", "control_version": run.control_version + 1,
            })
            self.db.flush()
            self._event(run, f"property-repair:{row.operation_id}")
            audit.append(self.db, "document_analysis.property_repair", actor=run.owner_id,
                         details={"run_id": str(run.recognition_run_id),
                                  "operation_id": str(row.operation_id),
                                  "review_id": str(review.review_id)}, commit=False)
            response = PropertyRepairResponse(
                operation=self.public_repair(row),
                run=self.application.status_response(run, role=self.identity.role),
            ).model_dump(mode="json")
            row.receipt = response
            self.db.commit()
            return response, False
        except Exception as exc:
            if isinstance(exc, IntegrityError):
                self.db.rollback()
                previous = self._replay(DocumentPropertyRepair, self._run(run_id), request)
                if previous:
                    return deepcopy(previous.receipt), True
            self._raise(exc)

    def _raise(self, exc):
        self.db.rollback()
        if isinstance(exc, RunStoreError):
            raise self.application.map_store_error(exc) from exc
        if isinstance(exc, IntegrityError):
            raise DocumentAnalysisError("HEAD_CONFLICT", "并发操作冲突，请刷新后重试",
                                        status_code=409) from exc
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            raise DocumentAnalysisError("REVIEW_CONTEXT_INVALID", "审核原文或检查点内容无效",
                                        status_code=409) from exc
        raise exc
