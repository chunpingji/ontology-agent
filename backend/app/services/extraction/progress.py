"""抽取作业进度事件总线（R1, FR-002）。

进程内发布/订阅：流水线各阶段 ``publish`` 进度，SSE 端点经 ``stream`` 异步消费。
为可测性，事件按 ``job_id`` 缓存历史；订阅者先回放历史再增量等待，作业到达终态后关闭。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

TERMINAL_STAGES = {"reviewing", "done", "failed"}

# ---------------------------------------------------------------------------
# Annotation task control: pause / resume / rerun
# ---------------------------------------------------------------------------
_annotation_control: dict[str, str] = {}


def set_annotation_control(job_id: str, action: str) -> None:
    _annotation_control[job_id] = action


def get_annotation_control(job_id: str) -> str:
    return _annotation_control.get(job_id, "run")


def clear_annotation_control(job_id: str) -> None:
    _annotation_control.pop(job_id, None)


class ProgressBus:
    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = {}

    def publish(self, job_id: str, event: dict) -> None:
        self._events.setdefault(job_id, []).append(event)

    def reset(self, job_id: str) -> None:
        """Start a fresh observable run without replaying an earlier terminal event."""
        self._events.pop(job_id, None)

    def history(self, job_id: str) -> list[dict]:
        return list(self._events.get(job_id, []))

    def annotation_is_running(self, job_id: str) -> bool:
        """Return whether this process owns a live annotation run for ``job_id``.

        Database state alone cannot answer this after a process restart: a job can
        remain ``annotating`` even though its in-memory background task disappeared.
        The latest annotation event is therefore the process-local lease.  Terminal
        annotation events release it, while an empty history permits recovery.
        """
        for event in reversed(self._events.get(job_id, [])):
            if event.get("annotation_stage"):
                return event.get("status") == "running"
        return False

    def is_terminal(self, job_id: str) -> bool:
        for ev in self._events.get(job_id, []):
            if ev.get("annotation_stage"):
                continue
            if ev.get("stage") in TERMINAL_STAGES or ev.get("status") in TERMINAL_STAGES:
                return True
        return False

    async def stream(self, job_id: str, *, timeout: float = 30.0,
                     latest_only: bool = False) -> AsyncIterator[dict]:
        """异步生成器：回放并增量推送事件，作业终态后结束（带安全超时）。"""
        sent = max(0, len(self._events.get(job_id, [])) - 1) if latest_only else 0
        waited = 0.0
        interval = 0.05
        while True:
            events = self._events.get(job_id, [])
            while sent < len(events):
                yield events[sent]
                sent += 1
            if self.is_terminal(job_id) and sent >= len(self._events.get(job_id, [])):
                return
            if waited >= timeout:
                return
            await asyncio.sleep(interval)
            waited += interval


# 进程内单例。
progress_bus = ProgressBus()
