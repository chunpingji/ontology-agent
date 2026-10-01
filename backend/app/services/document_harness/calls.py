"""In-memory call ports for standalone harnesses; online execution uses Repository."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import deepcopy

from .work import digest


class MemoryCalls:
    def __init__(self, invoke):
        self.invoke = invoke
        self.requests, self.answers, self.futures = {}, {}, {}
        self.pool = None

    def prepare(self, stage, payload, schema, target):
        key = digest({"stage": stage, "payload": payload, "schema": schema})
        self.requests[key] = deepcopy({"payload": payload, "schema": schema})
        batch = {"call_key": key, "stage": stage, **deepcopy(target)}
        return {**batch, "batch_id": digest(batch)}

    def request(self, batch):
        return deepcopy(self.requests[batch["call_key"]])

    def result(self, key):
        value = self.answers[key]
        if isinstance(value, Exception):
            del self.answers[key]
            raise value
        return deepcopy(value)

    def submit(self, batch):
        key = batch["call_key"]
        if key not in self.answers and key not in self.futures:
            request = self.request(batch)
            if self.pool is None:
                self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="harness-model")
            self.futures[key] = self.pool.submit(
                copy_context().run,
                self.invoke,
                batch["stage"],
                request["payload"],
                request["schema"],
            )

    def collect(self, batches):
        keys = {batch["call_key"] for batch in batches}
        if not any(key in self.answers for key in keys):
            wait([self.futures[key] for key in keys], return_when=FIRST_COMPLETED)
        for key in keys:
            if key in self.futures and self.futures[key].done():
                try:
                    self.answers[key] = self.futures.pop(key).result()
                except Exception as exc:
                    self.answers[key] = exc
        return [
            (
                b,
                None
                if isinstance(self.answers[b["call_key"]], Exception)
                else deepcopy(self.answers[b["call_key"]]),
                self.answers[b["call_key"]]
                if isinstance(self.answers[b["call_key"]], Exception)
                else None,
            )
            for b in batches
            if b["call_key"] in self.answers
        ]

    def invoke_prepared(self, batch):
        self.submit(batch)
        self.collect([batch])
        return self.result(batch["call_key"])

    def settle(self):
        for key, future in list(self.futures.items()):
            try:
                self.answers[key] = future.result()
            except Exception as exc:
                self.answers[key] = exc
            del self.futures[key]

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True)
            self.pool = None
