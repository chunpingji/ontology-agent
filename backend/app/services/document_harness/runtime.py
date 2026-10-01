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

from sqlalchemy import select
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
    HeadConflict,
    RunDeleted,
    RunNotFound,
    content_hash,
)
from app.services.document_harness.accounting import (
    call_accounting,
    empty_metrics,
    measured_tokens,
    update_business_metrics,
    update_call_metrics,
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


def initialize_state(db, run, catalog):
    """Initialize the two current read rows in the run creation transaction."""
    from .projection import build_graph_base

    for domain, key, value in (
        ("metrics", "main", empty_metrics()),
        ("display", "graph", {"work_version": run.work_version,
                               "base": build_graph_base({}, catalog)}),
    ):
        payload = {"key": key, "value": value}
        db.add(DocumentRunCurrentState(
            recognition_run_id=run.recognition_run_id, domain=PREFIX + domain,
            business_key=content_hash(key), work_version=1,
            content_hash=content_hash(payload), payload=payload,
        ))


def call_digest(row):
    return content_hash({"payload": row.payload, "accounting": call_accounting(row)})


def public_work(row):
    if not row or row.get("kind") not in {"relation_alignment", "coreference_review"}:
        return None
    from .projection import candidate_work

    return candidate_work(row)


class Repository:
    def __init__(self, db, run, token):
        self.db, self.run, self.token = db, run, token
        self.store = DocumentAnalysisRunStore(db)
        self._ranker = None
        self._catalog = None

    def current(self):
        self.db.expire_all()
        self.run = self.store.get_owned(self.run.recognition_run_id, self.run.owner_id)
        self.store.assert_fence(
            self.run.recognition_run_id, self.run.owner_id, self.token, for_update=True,
        )
        return self.run

    def load(self):
        return read_rows(self.db, self.run, exclude_domains={"input", "metrics", "display"})

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

    def metrics(self):
        value = get_row(self.db, self.run, "metrics", "main")
        if value is None:
            raise ValueError("harness_metrics_not_initialized")
        return value

    def progress(self, *, stage=None, status=None, error=None, stop_reason=None,
                 work_changed=False, request_changed=False, ranking_changed=False, commit=True):
        run = self.run
        cursor = get_row(self.db, run, "cursor", "main") or {}
        metrics = self.metrics()
        progress = {
            "engine": ENGINE, "extraction_protocol": ENGINE,
            "model_calls": metrics["completed_calls"],
            "candidate_count": metrics["candidate_count"], "fact_count": metrics["fact_count"],
            "cursor": {key: cursor[key] for key in (
                "stage", "scope_complete", "reading", "windows_total",
                "windows_discovered", "windows_reviewed",
            ) if key in cursor},
        }
        stage = stage or cursor.get("stage", run.stage)
        status = status or run.execution_status
        terminal = status in {"finished", "failed", "blocked_dependency", "cancelled"}
        versions = {
            "work_version": run.work_version + int(work_changed),
            "request_version": run.request_version + int(request_changed),
            "ranking_version": run.ranking_version + int(ranking_changed),
        }
        if terminal:
            self._event(run, stage=stage, status=status, after_revision=run.revision + 2)
            run = self.store.get_owned(run.recognition_run_id, run.owner_id)
        run = self.store.update_stage(
            run.recognition_run_id, run.owner_id, self.token,
            expected_revision=run.revision, stage=stage,
            execution_status=status, progress=progress, error=error, stop_reason=stop_reason,
            **versions,
        )
        if not terminal:
            self._event(run, stage=stage, status=status, after_revision=run.revision + 1)
        if commit:
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

    def _business_changes(self, changes, metrics):
        """Read only changed business keys to maintain counters, never scan old rows."""
        public_changed = False
        for domain, values in changes.items():
            if domain in {"metrics", "display"}:
                raise ValueError("harness_repository_owned_partition")
            for key, value in values.items():
                previous = get_row(self.db, self.run, domain, key)
                if previous == value:
                    continue
                metrics = update_business_metrics(metrics, domain, previous, value)
                if domain in {"entities", "properties", "relations", "relation_groups",
                              "observations", "fields", "coreferences"}:
                    public_changed = True
                if domain == "windows" and (
                    (previous or {}).get("guidance_class_iris")
                    != (value or {}).get("guidance_class_iris")
                ):
                    public_changed = True
                if domain == "work" and public_work(previous) != public_work(value):
                    public_changed = True
            self.put(DocumentRunCurrentState, domain, values)
        return metrics, public_changed

    def _write_display(self):
        from .projection import FIELDS, _catalog, build_graph_base

        self.db.flush()
        state = read_rows(
            self.db, self.run, domains={*FIELDS, "fields", "coreferences", "work", "windows"},
        )
        if self._catalog is None:
            self._catalog = _catalog(self.db, self.run)
        self.put(DocumentRunCurrentState, "display", {"graph": {
            "work_version": self.run.work_version + 1,
            "base": build_graph_base(state, self._catalog),
        }})

    def save(self, changes):
        try:
            self.current()
            metrics, public_changed = self._business_changes(changes, self.metrics())
            self.put(DocumentRunCurrentState, "metrics", {"main": metrics})
            if public_changed:
                self._write_display()
            self.db.flush()
            self.progress(work_changed=public_changed)
        except Exception:
            self.db.rollback()
            raise

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
            self.db.commit()
            return completed
        if self.should_stop():
            self.db.rollback()
            raise ModelCancelled()
        if self._ranker is None:
            self._ranker = CardRanker(policy, should_stop=self.should_stop)
        operation_start = len(self._ranker.semantic.observations) if self._ranker.semantic else 0
        started = monotonic()
        request = {"id": key, "stage": "card_ranking", "status": "running",
                   "snapshot_id": catalog.snapshot_id, "card_budget_bytes": card_budget}
        self.put(DocumentRunRequest, "rankings", {key: request})
        self.progress(ranking_changed=True)
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
            self.progress(ranking_changed=True)
            raise
        self.current()
        self.put(DocumentRunResult, "rankings", {key: result})
        self.put(DocumentRunRequest, "rankings", {key: {
            **request, "status": "completed", "seconds": result["seconds"],
        }})
        self.progress(ranking_changed=True)
        return result

    def rank_pairs(self, pairs):
        from math import isfinite

        from app.services.llm.semantic_ranking import configured_semantic_ranking

        from .ranking import CardRanker

        policy = source_payload(self.db, self.run)["policy"]["card_ranking"]
        config = policy["semantic"]
        if not config["enabled"]:
            raise ValueError("harness_pair_ranking_not_enabled")
        key = content_hash({"operation": "relation_pair_ranking", "pairs": pairs, "policy": config})
        self.current()
        completed = get_row(self.db, self.run, "rankings", key, model=DocumentRunResult)
        if completed is not None:
            self.db.commit()
            return completed["scores"]
        if self.should_stop():
            self.db.rollback()
            raise ModelCancelled()
        request = {"id": key, "stage": "relation_pair_ranking", "status": "running",
                   "pairs": deepcopy(pairs), "policy": config}
        self.put(DocumentRunRequest, "rankings", {key: request})
        self.progress(ranking_changed=True)
        started = monotonic()
        observations = []
        try:
            if self._ranker is None:
                self._ranker = CardRanker(policy, should_stop=self.should_stop)
            if self._ranker.semantic is None:
                self._ranker.semantic = configured_semantic_ranking(config)
            if self._ranker.semantic is None:
                raise ValueError("harness_semantic_ranking_unavailable")
            operation_start = len(self._ranker.semantic.observations)
            scores = self._ranker._batch("score_pairs", pairs)
            observations = self._ranker.semantic.observations[operation_start:]
            if len(scores) != len(pairs) or any(
                type(score) not in {int, float} or not isfinite(score) for score in scores
            ):
                raise ValueError("harness_ranking_invalid_pair_scores")
        except Exception as exc:
            self.current()
            self.put(DocumentRunRequest, "rankings", {key: {
                **request, "status": "failed", "error": failure_reason(exc),
                "seconds": monotonic() - started, "operations": observations,
            }})
            self.progress(ranking_changed=True)
            raise
        self.current()
        result = {"scores": scores, "seconds": monotonic() - started,
                  "operations": observations}
        self.put(DocumentRunResult, "rankings", {key: result})
        self.put(DocumentRunRequest, "rankings", {
            key: {**request, **result, "status": "completed"},
        })
        self.progress(ranking_changed=True)
        return scores

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

    def _call_row(self, key):
        row = self.db.get(DocumentRunRequest,
                          (self.run.recognition_run_id, PREFIX + "calls", content_hash(key)))
        if row is not None and (row.payload["key"] != key or call_digest(row) != row.content_hash):
            raise ValueError("harness_call_hash_mismatch")
        return row

    def get_call_record(self, call_key):
        row = self._call_row(call_key)
        return {**deepcopy(row.payload["value"]), **call_accounting(row)} if row else None

    def _prepared_row(self, key, stage, payload, schema, policy):
        row = self._call_row(key)
        if row is not None:
            return row
        value = {"id": key, "payload": deepcopy(payload), "schema": deepcopy(schema),
                 "model": policy.get("model"), "model_revision": policy.get("model_revision"),
                 "max_output_tokens": policy.get("max_output_tokens")}
        row = DocumentRunRequest(
            recognition_run_id=self.run.recognition_run_id, domain=PREFIX + "calls",
            request_key=content_hash(key), payload={"key": key, "value": value}, content_hash="",
            call_stage=stage, call_status="prepared", call_attempts=0,
            call_started_at=None, call_finished_at=None, call_error=None,
            call_duration_us=0, call_input_tokens=0, call_output_tokens=0,
            call_unknown_input=0, call_unknown_output=0, call_unmeasured_attempts=0,
        )
        row.content_hash = call_digest(row)
        self.db.add(row)
        self.db.flush()
        return row

    def prepare_batch(self, stage, payload, schema, application_target):
        """Persist the only recovery entry and its exact request before model dispatch."""
        try:
            self.current()
            policy = source_payload(self.db, self.run)["policy"]
            key = content_hash({"stage": stage, "payload": payload, "schema": schema,
                                "policy": policy})
            cursor = get_row(self.db, self.run, "cursor", "main") or {}
            batch = {"call_key": key, "stage": stage,
                     "window_id": application_target.get("window_id"),
                     "window_phase": application_target.get("window_phase"),
                     "targets": deepcopy(application_target.get("targets", [])),
                     "alias_bindings": deepcopy(application_target.get("alias_bindings", {})),
                     "source_bindings": deepcopy(application_target.get("source_bindings", {}))}
            if cursor.get("active_batch"):
                if cursor["active_batch"] != batch:
                    raise HeadConflict("harness_batch_already_active")
                self.db.commit()
                return batch
            self._prepared_row(key, stage, payload, schema, policy)
            for target in batch["targets"]:
                if target["domain"] not in {"windows", "entities", "referent_work", "work"}:
                    raise ValueError("harness_batch_target_domain_invalid")
                if target["domain"] == "work":
                    item = get_row(self.db, self.run, "work", target["id"])
                    if item is None or item.get("dependency_hash") != target["dependency_hash"]:
                        raise HeadConflict("harness_batch_dependency_changed")
                    self.put(DocumentRunCurrentState, "work", {
                        target["id"]: {**item, "last_call_key": key},
                    })
            self.put(DocumentRunCurrentState, "cursor", {"main": {**cursor, "active_batch": batch}})
            self.progress(request_changed=True)
            return batch
        except Exception:
            self.db.rollback()
            raise

    def batch_request(self, batch):
        record = self.get_call_record(batch["call_key"])
        if record is None or record["stage"] != batch["stage"]:
            raise ValueError("harness_batch_request_missing")
        policy = source_payload(self.db, self.run)["policy"]
        if content_hash({"stage": record["stage"], "payload": record["payload"],
                         "schema": record["schema"], "policy": policy}) != batch["call_key"]:
            raise ValueError("harness_batch_policy_changed")
        return {"payload": record["payload"], "schema": record["schema"]}

    def invoke_prepared(self, batch):
        cursor = get_row(self.db, self.run, "cursor", "main") or {}
        if cursor.get("active_batch") != batch:
            raise HeadConflict("harness_batch_not_active")
        request = self.batch_request(batch)
        return self.invoke(batch["stage"], request["payload"], request["schema"])

    def begin_attempt(self, call_key, stage, payload, schema, policy):
        try:
            self.current()
            row = self._prepared_row(call_key, stage, payload, schema, policy)
            before = call_accounting(row)
            if row.call_status == "running":
                row.call_unmeasured_attempts += 1
            row.call_attempts += 1
            row.call_status = "running"
            row.call_unknown_input += 1
            row.call_unknown_output += 1
            row.call_started_at, row.call_finished_at = datetime.now(UTC), None
            row.call_error = None
            row.content_hash = call_digest(row)
            metrics = update_call_metrics(self.metrics(), before, call_accounting(row))
            self.put(DocumentRunCurrentState, "metrics", {"main": metrics})
            attempt = row.call_attempts
            self.progress(stage=stage, request_changed=True)
            return attempt
        except Exception:
            self.db.rollback()
            raise

    def finish_attempt(self, call_key, attempt, result, measured_us):
        try:
            self.current()
            row = self._call_row(call_key)
            if row is None or row.call_attempts != attempt:
                raise HeadConflict("harness_attempt_changed")
            if row.call_status in {"completed", "failed"}:
                self.db.commit()
                return
            if row.call_status != "running":
                raise HeadConflict("harness_attempt_not_running")
            before = call_accounting(row)
            error = result.get("error")
            valid = not error and isinstance(result.get("output"), dict)
            if not result.get("transport_failure"):
                self.put(DocumentRunResult, "calls", {call_key: result})
            row.call_status = "completed" if valid else "failed"
            row.call_error = None if valid else error or "model_output_invalid"
            row.call_finished_at = datetime.now(UTC)
            if type(measured_us) is int and measured_us >= 0:
                row.call_duration_us += measured_us
            else:
                row.call_unmeasured_attempts += 1
            for name, alternate in (("input", "prompt_tokens"), ("output", "completion_tokens")):
                value = measured_tokens(result.get("usage"), name + "_tokens", alternate)
                if value is not None:
                    setattr(row, "call_" + name + "_tokens",
                            getattr(row, "call_" + name + "_tokens") + value)
                    setattr(row, "call_unknown_" + name, getattr(row, "call_unknown_" + name) - 1)
            row.content_hash = call_digest(row)
            metrics = update_call_metrics(self.metrics(), before, call_accounting(row))
            failure = None if valid else {
                "id": "call-failure:" + call_key, "label": row.call_stage + " 调用失败",
                "reason": row.call_error, "evidence": [], "kind": "failure", "field_id": None,
            }
            metrics, changed = self._business_changes({"observations": {
                "call-failure:" + call_key: failure,
            }}, metrics)
            self.put(DocumentRunCurrentState, "metrics", {"main": metrics})
            if changed:
                self._write_display()
            self.progress(stage=row.call_stage, request_changed=True, work_changed=changed)
        except Exception:
            self.db.rollback()
            raise

    def invoke(self, stage, payload, schema):
        for attempt in range(MODEL_CALL_MAX_ATTEMPTS):
            try:
                return self._invoke_once(stage, payload, schema)
            except StructuredModelError as exc:
                if str(exc) not in RETRYABLE_MODEL_ERRORS or attempt + 1 == MODEL_CALL_MAX_ATTEMPTS:
                    raise

    def _invoke_once(self, stage, payload, schema):
        from app.services.document_harness.model import call_model
        from app.services.document_harness.protocols import validate_paid_output

        policy = source_payload(self.db, self.run)["policy"]
        key = content_hash({"stage": stage, "payload": payload, "schema": schema, "policy": policy})
        self.current()
        completed = get_row(self.db, self.run, "calls", key, model=DocumentRunResult)
        if completed is not None:
            self.db.commit()
            if completed.get("error") or not isinstance(completed.get("output"), dict):
                raise HarnessCallFailed(completed.get("error") or "model_output_invalid")
            return deepcopy(completed["output"])
        if self.should_stop():
            self.db.rollback()
            raise ModelCancelled()
        attempt = self.begin_attempt(key, stage, payload, schema, policy)
        started = monotonic()
        try:
            with model_scope(stage=stage):
                result = call_model(stage, payload, schema, policy)
        except Exception as exc:
            self.finish_attempt(key, attempt, {
                "error": failure_reason(exc), "transport_failure": True, "usage": None,
            }, round((monotonic() - started) * 1_000_000))
            raise
        measured_us = round((monotonic() - started) * 1_000_000)
        if not result.get("error") and isinstance(result.get("output"), dict):
            try:
                validate_paid_output(stage, payload, result["output"], schema=schema)
            except (ValueError, TypeError, KeyError) as exc:
                result = {**result, "error": failure_reason(exc)}
        self.finish_attempt(key, attempt, result, measured_us)
        if result.get("error") or not isinstance(result.get("output"), dict):
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
                        lookup=repo.lookup, prepare_batch=repo.prepare_batch,
                        invoke_prepared=repo.invoke_prepared, batch_request=repo.batch_request,
                        policy=source_payload(db, repo.run)["policy"], rank_pairs=repo.rank_pairs,
                        max_request_bytes=source_payload(db, repo.run)["policy"].get(
                            "execution_policy", {},
                        ).get("wire_bytes_per_call", 96000),
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
