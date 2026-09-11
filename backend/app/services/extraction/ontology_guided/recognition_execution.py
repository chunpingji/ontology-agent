"""One model task in flight; the caller remains the sole domain writer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from queue import Empty, Queue
from threading import Event

from app.services.llm.model_runtime import ModelCancelled, model_scope, runtime


class RecognitionCall:
    def __init__(self, adapter, task, context, predicate, menu):
        self.cancelled = Event()
        self.requests = Queue()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recognition-model")
        parent_runtime = runtime.get()
        parent_stop = parent_runtime.get("should_stop")
        # A fresh Context cannot carry an owner's Session via unrelated
        # ContextVars or callbacks. The model scheduler opens its own Session
        # from the shared Engine; Connection/Session binds are not transferable.
        worker_runtime = {
            key: value for key, value in parent_runtime.items()
            if key in {
                "run_id", "job_id", "task_id", "stage", "input_tokens",
                "transport_version", "model_calls",
            } and isinstance(value, (str, int, float, bool, type(None)))
        }
        bind = parent_runtime.get("bind")
        if bind is not None:
            if getattr(bind, "engine", None) is not bind:
                raise ValueError("recognition worker requires an Engine bind")
            worker_runtime["bind"] = bind
        # Every mutable input is local to this worker. It cannot mutate the
        # coordinator's task, graph/menu or accounting callback.
        task = task.model_copy(deep=True)
        context = context.model_copy(deep=True)
        predicate = predicate.model_copy(deep=True)
        menu = menu.model_copy(deep=True)
        context.bind_model_call_hook(self.before_model)
        context.bind_protocol_hook(self.protocol_checkpoint)

        def invoke():
            with model_scope(
                **worker_runtime,
                should_stop=lambda: self.cancelled.is_set() or bool(parent_stop and parent_stop()),
                on_model_wait=None,
            ):
                if self.cancelled.is_set():
                    raise ModelCancelled()
                return adapter.inspect(task, context, predicate, menu)

        self.future = self.pool.submit(Context().run, invoke)

    def before_model(self, stage, ordinal):
        self._barrier("request", stage, ordinal)

    def protocol_checkpoint(self, state):
        self._barrier("protocol", state, None)

    def _barrier(self, kind, stage, ordinal):
        if self.cancelled.is_set():
            raise ModelCancelled()
        acknowledged, result = Event(), []
        self.requests.put((kind, stage, ordinal, acknowledged, result))
        while not acknowledged.wait(0.02):
            if self.cancelled.is_set():
                raise ModelCancelled()
        if result:
            raise result[0]
        if self.cancelled.is_set():
            raise ModelCancelled()

    def drain(self, reserve, checkpoint=None):
        while True:
            try:
                kind, stage, ordinal, acknowledged, result = self.requests.get_nowait()
            except Empty:
                return
            try:
                if kind == "request":
                    reserve(stage, ordinal)
                elif checkpoint is not None:
                    checkpoint(stage)
                else:
                    raise ValueError("protocol persistence callback is required")
            except BaseException as exc:
                result.append(exc)
                raise
            finally:
                acknowledged.set()

    def close(self):
        self.cancelled.set()
        while True:
            try:
                _kind, _stage, _ordinal, acknowledged, result = self.requests.get_nowait()
            except Empty:
                break
            result.append(ModelCancelled())
            acknowledged.set()
        # Cancellation is signalled to the actual client; future.cancel alone
        # would leave a paid request running outside the execution lifecycle.
        self.pool.shutdown(wait=True, cancel_futures=True)
