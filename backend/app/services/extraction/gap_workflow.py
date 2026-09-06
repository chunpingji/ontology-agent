"""Durable, compare-and-swap gap reservations; inference never holds a DB lock."""

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import update

from app.models.evidence import EvidenceJobState
from app.services.extraction.candidate_store import CandidateConflict


def update_run(db, job_id, revision, run):
    result = db.execute(
        update(EvidenceJobState)
        .where(EvidenceJobState.job_id == job_id, EvidenceJobState.revision == revision)
        .values(extraction_run=run, revision=revision + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise CandidateConflict("source/discovery state changed; reload before retrying")
    db.expire_all()


def reserve_round(db, job_id, revision, input_key, actor, max_rounds):
    state = db.get(EvidenceJobState, job_id, populate_existing=True)
    if state is None or state.revision != revision:
        raise CandidateConflict("source/discovery state changed; reload before retrying")
    run = deepcopy(state.extraction_run or {})
    history = run.setdefault("gap_history", [])
    if any(entry.get("input_key") == input_key for entry in history):
        return None, "identical_input_already_processed"
    if any(entry.get("reason") == "running" for entry in history):
        # A crashed attempt remains visible and consumes its reserved budget.
        # It must be reconciled explicitly, never silently retried as new work.
        return None, "gap_round_in_progress"
    if len(history) >= min(2, max_rounds):
        return None, "gap_round_budget_exhausted"
    entry = {
        "input_key": input_key,
        "round": len(history) + 1,
        "reason": "running",
        "actor": actor,
        "created": 0,
        "tasks": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    history.append(entry)
    update_run(db, job_id, revision, run)
    db.commit()
    return entry, None


def finish_round(db, job_id, input_key, reason, created, tasks):
    for _ in range(3):
        state = db.get(EvidenceJobState, job_id, populate_existing=True)
        run = deepcopy(state.extraction_run or {})
        entry = next((e for e in run.get("gap_history", []) if e["input_key"] == input_key), None)
        if entry is None:
            raise CandidateConflict("gap reservation disappeared")
        entry.update(reason=reason, created=created, tasks=tasks)
        try:
            update_run(db, job_id, state.revision, run)
            db.commit()
            return entry
        except CandidateConflict:
            db.rollback()
    raise CandidateConflict("gap completion raced with source updates; reload")
