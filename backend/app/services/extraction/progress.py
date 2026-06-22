"""抽取作业进度事件总线（R1, FR-002）。

进程内发布/订阅：流水线各阶段 ``publish`` 进度，SSE 端点经 ``stream`` 异步消费。
为可测性，事件按 ``job_id`` 缓存历史；订阅者先回放历史再增量等待，作业到达终态后关闭。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

TERMINAL_STAGES = {"reviewing", "done", "failed"}


class ProgressBus:
    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = {}

    def publish(self, job_id: str, event: dict) -> None:
        self._events.setdefault(job_id, []).append(event)

    def history(self, job_id: str) -> list[dict]:
        return list(self._events.get(job_id, []))

    def is_terminal(self, job_id: str) -> bool:
        for ev in self._events.get(job_id, []):
            if ev.get("stage") in TERMINAL_STAGES or ev.get("status") in TERMINAL_STAGES:
                return True
        return False

    async def stream(self, job_id: str, *, timeout: float = 30.0) -> AsyncIterator[dict]:
        """异步生成器：回放并增量推送事件，作业终态后结束（带安全超时）。"""
        sent = 0
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
