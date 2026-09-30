"""Fenced execution and current business rows for the independent harness.

This module has no dependency on the former recognition engine or its state
formats. Model results and request costs are separate from current work.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentRunCurrentState,
    DocumentRunRequest,
    DocumentRunResult,
)
from app.services.document_analysis.artifact_store import RunArtifactStorage
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    FenceViolation,
    RunDeleted,
    RunNotFound,
    content_hash,
)
from app.services.document_harness.application import ENGINE, require_harness, source_payload
from app.services.extraction.doc_converter import ensure_docx
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.word_analysis import analyze_word_core
from app.services.llm.local_client import StructuredModelError
from app.services.llm.model_runtime import ModelCancelled, model_scope

logger = logging.getLogger(__name__)
PREFIX = "harness:"
MODEL_CALL_MAX_ATTEMPTS = 3
RETRYABLE_MODEL_ERRORS = frozenset({"model_stream_incomplete", "model_request_failed"})


class HarnessCallFailed(RuntimeError):
    """A saved model response could not satisfy its independent stage protocol."""


def failure_reason(exc):
    value = str(exc)
    return value if re.fullmatch(r"[a-z][a-z0-9_]{1,100}", value) else type(exc).__name__


def read_rows(db, run, *, model=DocumentRunCurrentState, domains=None, exclude_domains=()):
    """Read only this engine's independently namespaced rows."""
    DocumentAnalysisRunStore(db).get_owned(run.recognition_run_id, run.owner_id)
    query = select(model).where(
        model.recognition_run_id == run.recognition_run_id,
        model.domain.startswith(PREFIX),
    )
    if domains is not None:
        query = query.where(model.domain.in_([PREFIX + domain for domain in domains]))
    if exclude_domains:
        query = query.where(model.domain.not_in([PREFIX + domain for domain in exclude_domains]))
    rows = db.scalars(query)
    result = {}
    for row in rows:
        if content_hash(row.payload) != row.content_hash:
            raise ValueError("harness_current_row_hash_mismatch")
        value = deepcopy(row.payload)
        result.setdefault(row.domain.removeprefix(PREFIX), {})[value["key"]] = value["value"]
    return result


def get_row(db, run, domain, key, *, model=DocumentRunCurrentState):
    DocumentAnalysisRunStore(db).get_owned(run.recognition_run_id, run.owner_id)
    row = db.get(model, (run.recognition_run_id, PREFIX + domain, content_hash(key)))
    if row is None:
        return None
    if content_hash(row.payload) != row.content_hash or row.payload["key"] != key:
        raise ValueError("harness_current_row_hash_mismatch")
    return deepcopy(row.payload["value"])


def call_summaries(db, run):
    """Read measured costs without repeatedly loading model inputs and raw answers."""
    DocumentAnalysisRunStore(db).get_owned(run.recognition_run_id, run.owner_id)
    value = DocumentRunRequest.payload["value"]
    fields = (
        "id", "stage", "status", "attempts", "seconds", "usage", "error", "unmeasured_attempts",
    )
    query = select(*(value[name].label(name) for name in fields)).where(
        DocumentRunRequest.recognition_run_id == run.recognition_run_id,
        DocumentRunRequest.domain == PREFIX + "calls",
    )
    return {row["id"]: dict(row) for row in db.execute(query).mappings()}


def business_counts(db, run):
    value = DocumentRunCurrentState.payload["value"]
    root = value["role"].as_string()
    query = select(func.count()).select_from(DocumentRunCurrentState).where(
        DocumentRunCurrentState.recognition_run_id == run.recognition_run_id,
        DocumentRunCurrentState.domain.in_([
            PREFIX + kind for kind in ("entities", "properties", "relations", "relation_groups")
        ]),
        or_(root.is_(None), root != "document_root"),
    )
    return (db.scalar(query), db.scalar(query.where(value["state"].as_string() == "accepted")))


class Repository:
    def __init__(self, db, run, token):
        self.db, self.run, self.token = db, run, token
        self.store = DocumentAnalysisRunStore(db)
        self._ranker = None

    def current(self):
        self.db.expire_all()
        self.run = self.store.get_owned(self.run.recognition_run_id, self.run.owner_id)
        self.store.assert_fence(
            self.run.recognition_run_id, self.run.owner_id, self.token, for_update=True,
        )
        return self.run

    def load(self):
        return read_rows(self.db, self.run, exclude_domains={"input"})

    def put(self, model, domain, values):
        key_field = (
            "business_key" if model is DocumentRunCurrentState
            else "request_key" if model is DocumentRunRequest else "result_key"
        )
        for key, value in values.items():
            identity = (self.run.recognition_run_id, PREFIX + domain, content_hash(key))
            row = self.db.get(model, identity)
            if value is None:
                if model is DocumentRunResult:
                    raise ValueError("paid_result_cannot_be_removed")
                if row is not None:
                    self.db.delete(row)
                continue
            payload = {"key": key, "value": value}
            digest = content_hash(payload)
            if row is not None:
                if row.content_hash == digest:
                    continue
                if model is DocumentRunResult:
                    raise ValueError("paid_result_cannot_change")
                row.payload, row.content_hash = payload, digest
                if model is DocumentRunCurrentState:
                    row.work_version += 1
            else:
                self.db.add(model(
                    recognition_run_id=self.run.recognition_run_id, domain=PREFIX + domain,
                    **{key_field: content_hash(key)}, payload=payload, content_hash=digest,
                    **({"work_version": 1} if model is DocumentRunCurrentState else {}),
                ))

    def progress(self, *, stage=None, status=None, error=None, stop_reason=None):
        run = self.run
        cursor = get_row(self.db, run, "cursor", "main") or {}
        requests = call_summaries(self.db, run)
        candidate_count, fact_count = business_counts(self.db, run)
        progress = {
            "engine": ENGINE, "extraction_protocol": ENGINE,
            "model_calls": sum(x.get("status") == "completed" for x in requests.values()),
            "candidate_count": candidate_count, "fact_count": fact_count,
            "cursor": cursor,
        }
        stage = stage or cursor.get("stage", run.stage)
        status = status or run.execution_status
        terminal = status in {"finished", "failed", "blocked_dependency", "cancelled"}
        if terminal:
            self._event(run, stage=stage, status=status, after_revision=run.revision + 2)
            run = self.store.get_owned(run.recognition_run_id, run.owner_id)
        run = self.store.update_stage(
            run.recognition_run_id, run.owner_id, self.token,
            expected_revision=run.revision, stage=stage,
            execution_status=status, progress=progress, error=error, stop_reason=stop_reason,
        )
        if not terminal:
            self._event(run, stage=stage, status=status, after_revision=run.revision + 1)
        self.db.commit()
        self.run = self.store.get_owned(run.recognition_run_id, run.owner_id)

    def _event(self, run, *, stage, status, after_revision):
        public_status = {"failed": "retryable_failure", "pausing": "running"}.get(status, status)
        public_stage = {
            "ingest": "accepted", "parse": "parsing", "complete": "complete",
        }.get(stage, "extracting")
        self.store.append_event(
            run.recognition_run_id, run.owner_id, self.token, expected_head=run.event_head,
            event_key=f"harness:{after_revision}", event_type="progress",
            payload={
                "contract_version": "document-analysis-runs-v1", "engine": ENGINE,
                "recognition_run_id": str(run.recognition_run_id),
                "run_revision": after_revision, "event_head": run.event_head + 1,
                "artifact_revision": run.artifact_revision,
                "stage": public_stage, "status": public_status,
            },
        )

    def complete_pause(self):
        run = self.current()
        self._event(run, stage=run.stage, status="paused", after_revision=run.revision + 2)
        run = self.store.get_owned(run.recognition_run_id, run.owner_id)
        self.store.complete_pause(
            run.recognition_run_id, run.owner_id, self.token, expected_revision=run.revision,
            expires_at=datetime.now(UTC) + timedelta(
                days=settings.document_analysis_retention_days,
            ),
        )
        self.db.commit()
        self.run = self.store.get_owned(run.recognition_run_id, run.owner_id)

    def save(self, changes):
        self.current()
        for domain, values in changes.items():
            self.put(DocumentRunCurrentState, domain, values)
        self.db.flush()
        self.progress()

    def should_stop(self):
        self.db.expire_all()
        run = self.store.get_owned(self.run.recognition_run_id, self.run.owner_id)
        return run.execution_status in {"pausing", "paused", "cancelled"}

    def rank(self, catalog, payload, card_budget):
        from app.services.document_harness.ranking import CardRanker

        policy = source_payload(self.db, self.run)["policy"]["card_ranking"]
        key = content_hash({
            "snapshot_id": catalog.snapshot_id, "input": payload,
            "card_budget": card_budget, "policy": policy,
        })
        self.current()
        completed = get_row(self.db, self.run, "rankings", key, model=DocumentRunResult)
        if completed is not None:
            return completed
        if self.should_stop():
            raise ModelCancelled()
        if self._ranker is None:
            self._ranker = CardRanker(policy, should_stop=self.should_stop)
        operation_start = len(self._ranker.semantic.observations) if self._ranker.semantic else 0
        started = monotonic()
        request = {"id": key, "stage": "card_ranking", "status": "running",
                   "snapshot_id": catalog.snapshot_id, "card_budget_bytes": card_budget}
        self.put(DocumentRunRequest, "rankings", {key: request})
        self.db.commit()
        try:
            result = self._ranker.rank(catalog, payload, card_budget)
        except Exception as exc:
            self.current()
            self.put(DocumentRunRequest, "rankings", {key: {
                **request, "status": "failed", "error": failure_reason(exc),
                "seconds": monotonic() - started,
                "operations": (
                    list(self._ranker.semantic.observations[operation_start:])
                    if self._ranker.semantic else []
                ),
            }})
            self.db.commit()
            raise
        self.current()
        self.put(DocumentRunResult, "rankings", {key: result})
        self.put(DocumentRunRequest, "rankings", {key: {
            **request, "status": "completed", "seconds": result["seconds"],
        }})
        self.db.commit()
        return result

    def close(self):
        if self._ranker is not None:
            self._ranker.close()

    def lookup(self, operation, catalog, argument):
        from app.services.ontology_engine import ontology_engine

        from .lookup_adapter import lookup_current

        self.current()
        if self.should_stop():
            raise ModelCancelled()
        with Session(self.db.get_bind(), autoflush=False) as source_db:
            result = lookup_current(source_db, ontology_engine, operation, catalog, argument)
        self.current()
        if self.should_stop():
            raise ModelCancelled()
        return result

    def invoke(self, stage, payload, schema):
        for attempt in range(MODEL_CALL_MAX_ATTEMPTS):
            try:
                return self._invoke_once(stage, payload, schema)
            except StructuredModelError as exc:
                if (
                    str(exc) not in RETRYABLE_MODEL_ERRORS
                    or attempt + 1 == MODEL_CALL_MAX_ATTEMPTS
                ):
                    raise

    def _invoke_once(self, stage, payload, schema):
        from app.services.document_harness.model import call_model

        policy = source_payload(self.db, self.run)["policy"]
        key = content_hash({"stage": stage, "payload": payload, "schema": schema, "policy": policy})
        self.current()
        completed = get_row(self.db, self.run, "calls", key, model=DocumentRunResult)
        if completed is not None:
            if completed.get("error") or not isinstance(completed.get("output"), dict):
                raise HarnessCallFailed(completed.get("error") or "model_output_invalid")
            return deepcopy(completed["output"])
        if self.should_stop():
            raise ModelCancelled()
        previous = get_row(self.db, self.run, "calls", key, model=DocumentRunRequest) or {}
        request = {
            "id": key, "stage": stage, "status": "running",
            "attempts": previous.get("attempts", 0) + 1,
            "started_at": datetime.now(UTC).isoformat(),
            "payload": deepcopy(payload), "schema": deepcopy(schema),
            "model": policy.get("model"), "model_revision": policy.get("model_revision"),
            "max_output_tokens": policy.get("max_output_tokens"),
            "seconds": previous.get("seconds", 0),
            "unmeasured_attempts": previous.get("unmeasured_attempts", 0),
        }
        self.put(DocumentRunRequest, "calls", {key: request})
        self.progress(stage=stage)
        started = monotonic()
        try:
            with model_scope(stage=stage):
                result = call_model(stage, payload, schema, policy)
        except Exception as exc:
            self.current()
            self.put(DocumentRunRequest, "calls", {key: {
                **request, "status": "failed", "error": failure_reason(exc),
                "seconds": request["seconds"] + monotonic() - started,
                "unmeasured_attempts": request["unmeasured_attempts"] + 1,
                "usage": None,
                "finished_at": datetime.now(UTC).isoformat(),
            }})
            self.db.commit()
            raise
        self.current()
        self.put(DocumentRunResult, "calls", {key: result})
        valid = not result.get("error") and isinstance(result.get("output"), dict)
        self.put(DocumentRunRequest, "calls", {key: {
            **request, "status": "completed" if valid else "failed",
            "error": result.get("error"), "usage": result.get("usage", {}),
            "seconds": request["seconds"] + result.get("seconds", 0),
            "finished_at": datetime.now(UTC).isoformat(),
        }})
        # The paid answer commits before the controller consumes it. Resume can
        # apply this exact answer even if pause lands between these boundaries.
        self.db.commit()
        if not valid:
            raise HarnessCallFailed(result.get("error") or "model_output_invalid")
        return deepcopy(result["output"])


class LeaseKeeper:
    """Lease renewal only; a pause waits for the current paid answer to commit."""

    def __init__(self, bind, run, token):
        self.bind, self.run, self.token = bind, run, token
        self.stop_event, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        seconds = settings.document_analysis_lease_seconds
        while not self.stop_event.wait(max(0.05, min(15, seconds / 3))):
            with Session(self.bind) as db:
                try:
                    DocumentAnalysisRunStore(db).heartbeat(
                        self.run.recognition_run_id, self.run.owner_id, self.token,
                        lease_seconds=seconds,
                    )
                    db.commit()
                except (FenceViolation, RunNotFound, RunDeleted):
                    self.lost.set()
                    return
                except Exception:
                    db.rollback()
                    logger.exception("harness lease renewal failed")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop_event.set()
        self.thread.join()


def _input(repo):
    existing = read_rows(repo.db, repo.run, domains={"input"}).get("input", {}).get("document")
    if existing:
        ir = DocumentIR.model_validate(existing)
        if ir.original_document_hash != repo.run.document_hash:
            raise ValueError("source_identity_mismatch")
        return ir
    run = repo.run
    source = repo.db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    path = RunArtifactStorage(settings.document_analysis_storage_dir).resolve(
        run.recognition_run_id, source.storage_uri,
    )
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != run.document_hash:
            raise ValueError("source_hash_mismatch")
    repo.current()
    repo.progress(stage="parse")
    analysis = analyze_word_core(
        Path(ensure_docx(str(path))), source_filename=run.filename, original_path=path,
    )
    repo.save({"input": {"document": analysis.ir.model_dump(mode="json")}})
    return analysis.ir


def execute_claimed(db, run, token):
    """Run only a newly created harness run; no old state is decoded or migrated."""
    from app.services.document_harness.controller import Engine

    require_harness(db, run)
    repo = Repository(db, run, token)
    with LeaseKeeper(db.get_bind(), run, token) as keeper:
        try:
            if not repo.should_stop():
                ir = _input(repo)
                ref = repo.store.get_artifact(
                    run.recognition_run_id, run.owner_id, "ontology_snapshot",
                )
                artifact = db.get(DocumentAnalysisArtifact, ref.artifact_id)
                catalog = artifact.payload
                if content_hash(catalog) != run.ontology_snapshot_hash:
                    raise ValueError("schema_catalog_hash_mismatch")
                with model_scope(
                    run_id=str(run.recognition_run_id), bind=db.get_bind(),
                    should_stop=keeper.lost.is_set,
                ):
                    Engine(
                        ir=ir, catalog=catalog, state=repo.load(), invoke=repo.invoke,
                        save=repo.save, should_stop=repo.should_stop,
                        rank=repo.rank,
                        lookup=repo.lookup,
                    ).run()
            repo.current()
            if repo.run.execution_status == "pausing":
                repo.complete_pause()
            else:
                cursor = repo.load().get("cursor", {}).get("main", {})
                if cursor.get("stage") != "complete":
                    raise ValueError("harness_execution_incomplete")
                repo.progress(
                    stage="complete", status="finished",
                    stop_reason=None if cursor.get("scope_complete") else "incomplete_scope",
                )
        except (FenceViolation, RunDeleted, RunNotFound):
            db.rollback()
        except ModelCancelled:
            db.rollback()
            try:
                repo.current()
                if repo.run.execution_status == "pausing":
                    repo.complete_pause()
                else:
                    repo.progress(status="failed", error={
                        "code": "MODEL_INTERRUPTED", "message": "模型调用被中断", "retryable": True,
                    })
            except (FenceViolation, RunDeleted, RunNotFound):
                db.rollback()

        except Exception as exc:
            db.rollback()
            logger.exception("independent document harness failed run=%s", run.recognition_run_id)
            try:
                repo.current()
                repo.progress(status="failed", error={
                    "code": "HARNESS_STAGE_FAILED",
                    "message": f"阶段执行失败：{failure_reason(exc)}；已保存当前工作",
                    "retryable": True, "failure_type": type(exc).__name__,
                    "failure_reason": failure_reason(exc),
                })
            except (FenceViolation, RunDeleted, RunNotFound):
                db.rollback()

        finally:
            repo.close()


def session_factory(bind=None):
    if bind is None:
        from app.db import SessionLocal

        return SessionLocal
    return sessionmaker(bind=bind, expire_on_commit=False)
