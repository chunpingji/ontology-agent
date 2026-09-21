"""Paid verifier batches advance execution time without overstating graph coverage."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.models.document_analysis import DocumentAnalysisExecution
from app.services.document_analysis import execution
from app.services.llm import local_client
from tests.test_extraction.test_record_discovery_current_state import (
    confirm,
    persist,
    record_protocol,
    reservation,
)
from tests.test_extraction.test_record_executor import record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def progress_clock(current_run, monkeypatch):
    store, run, _ = current_run
    stamp = datetime.now(UTC)
    clock = [stamp]
    row = store.db.get(DocumentAnalysisExecution, run.recognition_run_id)
    row.last_progress_at = stamp
    row.recovery_event_head = run.event_head
    row.lease_expires_at = stamp + timedelta(days=1)
    store.db.commit()
    monkeypatch.setattr(store, "_clock", lambda: clock[0])
    monkeypatch.setattr(settings, "document_analysis_no_progress_timeout_seconds", 900)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(execution, "datetime", ClockDateTime)
    return clock, row


def test_split_verification_survives_long_chain_with_durable_paid_progress(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    clock, row = progress_clock(current_run, monkeypatch)
    started = clock[0]
    transport = local_client.responses_create
    truncated = False

    def slow_split_batch(*arguments, **kwargs):
        nonlocal truncated
        assert not execution._execution_stalled(row)
        clock[0] += timedelta(seconds=360)
        # An ordinary lease heartbeat cannot hide genuinely stalled execution.
        assert not execution._execution_stalled(row)
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        turn = transport(*arguments, **kwargs)
        if view["stage"] == "verification" and not truncated:
            truncated = True
            return replace(
                turn, response_status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        return turn

    monkeypatch.setattr(local_client, "responses_create", slow_split_batch)
    result = executor(max_model_calls_per_record=8).run(**args, **hooks)

    assert truncated
    assert (clock[0] - started).total_seconds() > 900
    assert sum(view["stage"] == "verification" for view in requests) >= 3
    assert {prop.raw_value for prop in result.graph.properties} == {"A", "B"}
    assert not execution._execution_stalled(row)


def test_only_new_committed_answers_reset_progress_clock(current_run, monkeypatch):
    store, run, token = current_run
    clock, row = progress_clock(current_run, monkeypatch)
    initial = clock[0]
    protocol = record_protocol()
    persist(current_run, protocol)
    clock[0] += timedelta(seconds=100)
    receipt = reservation(current_run, protocol)
    persist(current_run, protocol, receipts=[receipt])
    store.heartbeat(run.recognition_run_id, run.owner_id, token, lease_seconds=3600)
    store.db.commit()
    assert row.last_progress_at.replace(tzinfo=UTC) == initial

    row.recovery_attempts = 2
    store.db.commit()
    confirm(current_run, protocol)
    confirmed = clock[0]
    assert row.last_progress_at.replace(tzinfo=UTC) == confirmed
    assert row.recovery_attempts == 0
    event_head = run.event_head

    clock[0] += timedelta(seconds=901)
    persist(current_run, protocol)
    store.heartbeat(run.recognition_run_id, run.owner_id, token, lease_seconds=3600)
    store.db.commit()
    assert row.last_progress_at.replace(tzinfo=UTC) == confirmed
    assert run.event_head == event_head
    assert execution._execution_stalled(row)
