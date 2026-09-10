"""One cancellable ranking preparation, with main-thread durability barriers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from queue import Empty, Queue
from threading import Event

from app.services.extraction.ontology_guided.semantic_reranker import RankingService
from app.services.llm.model_runtime import ModelCancelled, model_scope, runtime


class RankingPreparation:
    """Prepare on a private service while ready semantic tasks keep running.

    Every charged model call blocks until the run owner has durably accepted
    its cumulative cost/cache snapshot. The worker never accesses a run store
    or mutates the active service/scheduler.
    """

    def __init__(self, service: RankingService, *, task_id: str, arguments: dict):
        self._updates: Queue = Queue()
        self._stopped = Event()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-ranking")
        self.service = service.fork(before_model_hook=self._before_model)
        parent_stop = runtime.get().get("should_stop")

        def prepare():
            with model_scope(
                task_id=task_id,
                should_stop=lambda: self._stopped.is_set() or bool(parent_stop and parent_stop()),
            ):
                return self.service.prepare_next_epoch(**arguments)

        self.future = self._pool.submit(copy_context().run, prepare)

    def _before_model(self, snapshot: dict):
        acknowledged = Event()
        result: list[BaseException] = []
        self._updates.put((snapshot, acknowledged, result))
        while not acknowledged.wait(0.05):
            if self._stopped.is_set():
                raise ModelCancelled()
        if result:
            raise result[0]
        if self._stopped.is_set():
            raise ModelCancelled()

    def drain(self, persist):
        while True:
            try:
                snapshot, acknowledged, result = self._updates.get_nowait()
            except Empty:
                return
            try:
                persist(snapshot)
            except BaseException as exc:
                result.append(exc)
                raise
            finally:
                acknowledged.set()

    def wait(self, seconds=0.02):
        # Cancellable short wait; the owner polls and drains durability requests.
        self._stopped.wait(seconds)

    def close(self):
        self._stopped.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
