"""Cross-process ownership and fencing for long-running document recognition.

Transactions cover only ownership changes and writes, never a model wait.
The worker renews its lease in a separate session while awaiting the model.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.extraction import AnnotationExecution

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "running")
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 15


class ExecutionBusy(Exception):
    pass


class ExecutionLost(Exception):
    """A stale worker must stop without changing candidates, files or progress."""


def now():
    return datetime.now(timezone.utc)


def claim(db, job_id, *, actor, options):
    """Claim atomically; caller commits together with job metadata and audit."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Annotation ownership requires PostgreSQL or SQLite")
    stamp = now()
    db.execute(insert(AnnotationExecution).values(
        job_id=job_id, run_id="", status="idle", actor=actor, options={}, progress={},
        pause_requested=False, lease_expires_at=stamp, updated_at=stamp,
    ).on_conflict_do_nothing(index_elements=["job_id"]))
    token = uuid4().hex
    acquired = db.execute(update(AnnotationExecution).where(
        AnnotationExecution.job_id == job_id,
        or_(AnnotationExecution.status.not_in(ACTIVE),
            AnnotationExecution.lease_expires_at <= stamp),
    ).values(run_id=token, status="queued", actor=actor, options=options,
             pause_requested=False, lease_expires_at=stamp + timedelta(seconds=LEASE_SECONDS),
             updated_at=stamp).execution_options(synchronize_session=False))
    if acquired.rowcount != 1:
        raise ExecutionBusy("该作业正在识别，请等待当前任务完成")
    return token


def _owned(job_id, run_id):
    return (AnnotationExecution.job_id == job_id, AnnotationExecution.run_id == run_id,
            AnnotationExecution.status.in_(ACTIVE), AnnotationExecution.lease_expires_at > now())


@contextmanager
def fence(db, job_id, run_id):
    """Lock/check the current generation before any durable side effect."""
    try:
        stamp = now()
        result = db.execute(update(AnnotationExecution).where(*_owned(job_id, run_id)).values(
            updated_at=stamp, lease_expires_at=stamp + timedelta(seconds=LEASE_SECONDS),
        ).execution_options(synchronize_session=False, autoflush=False))
        if result.rowcount != 1:
            raise ExecutionLost()
        head = db.get(AnnotationExecution, job_id, populate_existing=True)
        yield head
        db.commit()
    except BaseException:
        db.rollback()
        raise


def begin_worker(db, job_id, run_id):
    """Even duplicate delivery of the same queued run may start only one worker."""
    result = db.execute(update(AnnotationExecution).where(
        *_owned(job_id, run_id), AnnotationExecution.status == "queued",
    ).values(status="running").execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def request_pause(db, job_id):
    result = db.execute(update(AnnotationExecution).where(
        AnnotationExecution.job_id == job_id, AnnotationExecution.status.in_(ACTIVE),
        AnnotationExecution.lease_expires_at > now(),
    ).values(pause_requested=True).execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def public_progress(db, job_id):
    head = db.get(AnnotationExecution, job_id, populate_existing=True)
    if head is None:
        return None
    event = {**head.progress, "job_id": str(job_id), "run_id": head.run_id,
             "pause_requested": head.pause_requested}
    expiry = head.lease_expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if head.status in ACTIVE and expiry <= now():
        event.update(annotation_stage="interrupted", status="interrupted")
    return event


class WorkerLease:
    def __init__(self, bind, job_id, run_id):
        self.bind, self.job_id, self.run_id = bind, job_id, run_id
        self.stopped, self.lost = Event(), Event()
        self.thread = Thread(target=self._renew, daemon=True, name="annotation-lease")

    def _renew(self):
        while not self.stopped.wait(HEARTBEAT_SECONDS):
            try:
                with Session(self.bind) as db:
                    result = db.execute(update(AnnotationExecution).where(
                        *_owned(self.job_id, self.run_id),
                    ).values(lease_expires_at=now() + timedelta(seconds=LEASE_SECONDS))
                      .execution_options(synchronize_session=False))
                    db.commit()
                    if result.rowcount == 1:
                        continue
            except Exception:
                logger.warning("Annotation lease renewal failed job=%s", self.job_id,
                               exc_info=True)
            self.lost.set()
            return

    def check(self):
        if self.lost.is_set():
            raise ExecutionLost()
        with Session(self.bind) as db:
            pause = db.scalar(select(AnnotationExecution.pause_requested).where(
                *_owned(self.job_id, self.run_id),
            ))
        if pause is None:
            self.lost.set()
            raise ExecutionLost()
        return pause

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stopped.set()
        self.thread.join(timeout=2)
