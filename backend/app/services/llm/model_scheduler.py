"""FIFO admission shared across workers, jobs and local-model consumers.

One short database transaction locks the endpoint pool for each admission. No
transaction is held while waiting for inference. Dead owners expire; live owners
must renew before expiry and stop HTTP work if their slot is lost.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelPool, LocalModelRequest
from app.services.llm.model_runtime import ModelCancelled, runtime

LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5
POLL_SECONDS = 0.25


def pool_key(base_url):
    url = urlsplit(str(base_url))
    # Paths and model aliases on the same physical endpoint share its capacity.
    port = url.port or (443 if url.scheme == "https" else 80)
    endpoint = f"{url.scheme.lower()}://{url.hostname.lower()}:{port}"
    return sha256(endpoint.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc)


def default_bind():
    from app.db import engine

    return engine


class ModelSlotLost(ModelCancelled):
    pass


class RequestTicket:
    def __init__(self, base_url, call_id, attempt, remaining, *, bind=None, capacity=None):
        from app.config import settings

        self.meta = runtime.get()
        self.bind = bind or self.meta.get("bind") or default_bind()
        self.capacity = max(
            1, capacity if capacity is not None else settings.local_llm_max_concurrency
        )
        self.pool_id, self.request_id = pool_key(base_url), uuid4().hex
        self.call_id, self.attempt = call_id, attempt
        self.created = now()
        self.clock = monotonic()
        self.started = None
        self.metrics = {
            k: self.meta[k] for k in ("input_tokens", "transport_version") if k in self.meta
        }
        self.deadline = self.created + timedelta(seconds=remaining)
        with Session(self.bind) as db:
            self._lock_pool(db)
            db.add(
                LocalModelRequest(
                    request_id=self.request_id,
                    pool_id=self.pool_id,
                    logical_call_id=call_id,
                    attempt=attempt,
                    job_id=str(self.meta["job_id"]) if self.meta.get("job_id") else None,
                    run_id=self.meta.get("run_id"),
                    task_id=self.meta.get("task_id"),
                    stage=self.meta.get("stage", "local_model"),
                    status="queued",
                    created_at=self.created,
                    deadline_at=self.deadline,
                    lease_expires_at=self.created + timedelta(seconds=LEASE_SECONDS),
                    metrics=self.metrics,
                )
            )
            db.commit()

    def _lock_pool(self, db):
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.execute(
            insert(LocalModelPool)
            .values(pool_id=self.pool_id, capacity=self.capacity)
            .on_conflict_do_nothing(index_elements=["pool_id"])
        )
        # UPDATE obtains an exclusive row lock on PostgreSQL and a write lock on SQLite.
        db.execute(
            update(LocalModelPool)
            .where(LocalModelPool.pool_id == self.pool_id)
            .values(capacity=self.capacity)
        )

    def admit(self):
        stamp = now()
        with Session(self.bind) as db:
            self._lock_pool(db)
            db.execute(
                update(LocalModelRequest)
                .where(
                    LocalModelRequest.pool_id == self.pool_id,
                    LocalModelRequest.status.in_(("queued", "running")),
                    LocalModelRequest.lease_expires_at <= stamp,
                )
                .values(status="expired", finished_at=stamp)
            )
            row = db.scalar(
                select(LocalModelRequest).where(LocalModelRequest.request_id == self.request_id)
            )
            if row.status != "queued":
                raise ModelSlotLost()
            active = db.scalar(
                select(func.count())
                .select_from(LocalModelRequest)
                .where(
                    LocalModelRequest.pool_id == self.pool_id, LocalModelRequest.status == "running"
                )
            )
            ahead = db.scalar(
                select(func.count())
                .select_from(LocalModelRequest)
                .where(
                    LocalModelRequest.pool_id == self.pool_id,
                    LocalModelRequest.status == "queued",
                    LocalModelRequest.sequence < row.sequence,
                )
            )
            admitted = active + ahead < self.capacity
            row.lease_expires_at = stamp + timedelta(seconds=LEASE_SECONDS)
            if admitted:
                row.status = "running"
            db.commit()
        return admitted

    def start(self):
        self.started = monotonic()
        self.metrics["queue_seconds"] = round(self.started - self.clock, 3)
        with Session(self.bind) as db:
            result = db.execute(
                update(LocalModelRequest)
                .where(
                    LocalModelRequest.request_id == self.request_id,
                    LocalModelRequest.status == "running",
                    LocalModelRequest.lease_expires_at > now(),
                )
                .values(started_at=now(), metrics=dict(self.metrics))
            )
            db.commit()
            if result.rowcount != 1:
                raise ModelSlotLost()
        self.publish("running")

    def heartbeat(self):
        stamp = now()
        with Session(self.bind) as db:
            result = db.execute(
                update(LocalModelRequest)
                .where(
                    LocalModelRequest.request_id == self.request_id,
                    LocalModelRequest.status == "running",
                    LocalModelRequest.lease_expires_at > stamp,
                )
                .values(lease_expires_at=stamp + timedelta(seconds=LEASE_SECONDS))
            )
            db.commit()
            if result.rowcount != 1:
                raise ModelSlotLost()

    def finish(self, status, **metrics):
        stamp = now()
        self.metrics.update(metrics)
        self.metrics.update(
            queue_seconds=round((self.started or monotonic()) - self.clock, 3),
            request_seconds=round(monotonic() - self.started, 3) if self.started else 0,
            elapsed_seconds=round(monotonic() - self.clock, 3),
        )
        with Session(self.bind) as db:
            db.execute(
                update(LocalModelRequest)
                .where(
                    LocalModelRequest.request_id == self.request_id,
                    LocalModelRequest.status.in_(("queued", "running")),
                )
                .values(status=status, finished_at=stamp, metrics=dict(self.metrics))
            )
            db.commit()
        self.publish(status)

    def publish(self, status):
        sink = self.meta.get("progress")
        if not sink:
            return
        with Session(self.bind) as db:
            count = db.scalar(
                select(func.count())
                .select_from(LocalModelRequest)
                .where(
                    LocalModelRequest.job_id == str(self.meta.get("job_id")),
                    LocalModelRequest.started_at.is_not(None),
                )
            )
        sink(
            {
                "request_id": self.request_id,
                "logical_call_id": self.call_id,
                "attempt": self.attempt,
                "status": status,
                "stage": self.meta.get("stage", "local_model"),
                "task_id": self.meta.get("task_id"),
                "created_at": self.created.timestamp(),
                "deadline_at": self.deadline.timestamp(),
                "started_at": self.created.timestamp() + self.started - self.clock
                if self.started
                else None,
                "http_attempts": count,
                "model_calls": self.meta.get("model_calls"),
                **self.metrics,
            }
        )
