"""Operation-local measurements; nested stages are not added into wall time."""

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import isgeneratorfunction, signature
from time import perf_counter

_active = ContextVar("extraction_performance", default=None)


class Performance:
    def __init__(self):
        self.started = perf_counter()
        self.stages = defaultdict(lambda: {"calls": 0, "seconds": 0.0})

    def snapshot(self):
        wall = perf_counter() - self.started
        model = self.stages.get("model", {}).get("seconds", 0.0)
        return {
            "wall_seconds": round(wall, 6),
            "model_seconds": round(model, 6),
            "non_model_seconds": round(max(0.0, wall - model), 6),
            "stages": {
                name: {"calls": value["calls"], "seconds": round(value["seconds"], 6)}
                for name, value in sorted(self.stages.items())
            },
        }


@contextmanager
def tracking():
    current = _active.get()
    if current is not None:
        yield current
        return
    value = Performance()
    token = _active.set(value)
    try:
        yield value
    finally:
        _active.reset(token)


@contextmanager
def measure(name):
    current = _active.get()
    start = perf_counter()
    try:
        yield
    finally:
        if current is not None:
            current.stages[name]["calls"] += 1
            current.stages[name]["seconds"] += perf_counter() - start


def timed(name):
    def decorate(function):
        if isgeneratorfunction(function):

            @wraps(function)
            def iterate(*args, **kwargs):
                iterator = function(*args, **kwargs)
                while True:
                    with measure(name):
                        try:
                            value = next(iterator)
                        except StopIteration:
                            return
                    yield value  # downstream/model work is outside this stage

            return iterate

        @wraps(function)
        def call(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)

        return call

    return decorate


def profiled(name):
    """Expose operation timings on dictionary responses, without shared state."""

    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with tracking() as performance:
                with measure(name):
                    result = function(*args, **kwargs)
                result["performance"] = performance.snapshot()
                return result

        call.__signature__ = signature(function, eval_str=True)
        return call

    return decorate
