"""Per-execution controls propagated through synchronous model callers."""

from contextlib import contextmanager
from contextvars import ContextVar

runtime = ContextVar("local_model_runtime", default={})


class ModelCancelled(Exception):
    """An interrupted task is resumable; this is not a model refusal."""

    def __init__(self, candidates=None):
        super().__init__("model_cancelled")
        self.candidates = candidates or []


@contextmanager
def model_scope(**values):
    token = runtime.set({**runtime.get(), **values})
    try:
        yield
    finally:
        runtime.reset(token)


def check_cancelled():
    check = runtime.get().get("should_stop")
    if check and check():
        raise ModelCancelled()
