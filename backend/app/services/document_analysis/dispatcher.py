"""Neutral lifecycle dispatcher; extraction engines remain separate execution domains."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy.engine import Engine

from app.config import settings
from app.models.document_analysis import DocumentAnalysisRun
from app.services.document_analysis.run_store import (
    DocumentAnalysisRunStore,
    InvalidRunState,
    LeaseBusy,
    RunDeleted,
    RunNotFound,
)
from app.services.document_harness.application import is_harness_run, is_template_run
from app.services.document_harness.runtime import execute_claimed, session_factory

logger = logging.getLogger(__name__)


def _execute(db, store, run, token):
    if is_harness_run(db, run):
        execute_claimed(db, run, token)
    elif is_template_run(db, run):
        # Templates remain in their existing, separate execution domain.
        from app.services.document_analysis.execution import _execute_dispatched_run

        _execute_dispatched_run(db, store, run, token)
    else:
        store.update_stage(
            run.recognition_run_id, run.owner_id, token, expected_revision=run.revision,
            stage=run.stage, execution_status="blocked_dependency",
            stop_reason="retired_document_engine",
            error={"code": "ENGINE_RETIRED", "message": "旧文档引擎已隔离，请新建运行",
                   "retryable": False},
        )
        db.commit()


def dispatch_run(recognition_run_id: UUID | str, *, bind: Engine | None = None):
    with session_factory(bind)() as db:
        run = db.get(DocumentAnalysisRun, recognition_run_id)
        if (run is None or run.execution_status not in {"queued", "running", "pausing"}
                or run.deletion_state != "none"):
            return
        store = DocumentAnalysisRunStore(db)
        try:
            token = store.claim(
                run.recognition_run_id, run.owner_id, actor="durable-dispatcher",
                worker_id=settings.document_analysis_worker_id,
                lease_seconds=settings.document_analysis_lease_seconds,
            )
            db.commit()
        except (LeaseBusy, InvalidRunState, RunNotFound, RunDeleted):
            db.rollback()
            return
        _execute(db, store, store.get_owned(run.recognition_run_id, run.owner_id), token)


def dispatch_next_run(*, bind: Engine | None = None, worker_id: str | None = None):
    with session_factory(bind)() as db:
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
        _execute(db, store, store.get_owned(run.recognition_run_id, run.owner_id), token)
        return True


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
