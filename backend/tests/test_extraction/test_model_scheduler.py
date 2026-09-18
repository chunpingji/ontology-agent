import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Lock
from time import monotonic

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.llm.local_client import LocalModelClient, StructuredModelError, chat_with_schema
from app.services.llm.model_runtime import ModelCancelled, model_scope
from app.services.llm.model_scheduler import ModelSlotLost, RequestTicket, now, pool_key


@pytest.fixture
def request_db(isolated_model_scheduler):
    return isolated_model_scheduler()


def reply():
    return {
        "id": "test",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"ok":true}',
                },
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 8,
            "total_tokens": 128,
            "prompt_tokens_details": {"cached_tokens": 90},
        },
        "timings": {"prompt_ms": 20, "predicted_ms": 12},
    }


def invoke(client, **options):
    return chat_with_schema(
        client,
        system="system",
        user="evidence",
        schema={"type": "object"},
        max_attempts=1,
        raise_on_error=True,
        **options,
    )


def rows(bind):
    with Session(bind) as db:
        return list(db.scalars(select(LocalModelRequest).order_by(LocalModelRequest.sequence)))


@pytest.mark.parametrize("second_result", ["success", "truncated", "malformed"])
def test_summary_truncation_retry_increases_budget_keeps_schema_and_is_bounded(
    request_db, second_result,
):
    sent = []

    async def transport(request):
        sent.append(json.loads(request.content))
        response = reply()
        if len(sent) == 1 or second_result == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        elif second_result == "malformed":
            response["choices"][0]["message"]["content"] = "invalid JSON"
        return httpx.Response(200, json=response)

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=request_db):
        def run():
            return chat_with_schema(
                client, system="summary", user="source", schema={"type": "object"},
                max_tokens=512, truncation_max_tokens=1024, max_attempts=2,
                raise_on_error=True, total_timeout_s=3,
            )

        if second_result == "success":
            assert run() == {"ok": True}
        else:
            with pytest.raises(StructuredModelError):
                run()
    assert [r["max_tokens"] for r in sent] == [512, 1024]
    assert all(r["response_format"]["type"] == "json_schema" for r in sent)
    requests = rows(request_db)
    assert len(requests) == 2
    assert len({r.logical_call_id for r in requests}) == 1
    assert requests[0].deadline_at >= requests[1].deadline_at - timedelta(milliseconds=20)


def test_fifo_capacity_and_expired_slot_fencing(request_db):
    tickets = [
        RequestTicket("http://model.test/v1", str(i), 1, 20, bind=request_db, capacity=2)
        for i in range(4)
    ]
    assert not tickets[2].admit()
    assert tickets[1].admit() and tickets[0].admit()
    assert not tickets[3].admit()
    tickets[0].finish("complete")
    assert not tickets[3].admit()  # The earlier queued request gets the free capacity.
    assert tickets[2].admit()
    with Session(request_db) as db:
        db.execute(
            update(LocalModelRequest)
            .where(
                LocalModelRequest.request_id == tickets[1].request_id,
            )
            .values(lease_expires_at=now() - timedelta(seconds=1))
        )
        db.commit()
    assert tickets[3].admit()
    with pytest.raises(ModelSlotLost):
        tickets[1].heartbeat()
    assert rows(request_db)[1].status == "expired"
    assert pool_key("http://MODEL.test/v1") == pool_key("http://model.test:80/alias/v1/")


def test_shared_pool_caps_actual_http_across_jobs_and_model_consumers(request_db):
    state = {"active": 0, "peak": 0}
    lock = Lock()
    events = []

    async def transport(request):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.1)
            return httpx.Response(200, json=reply())
        finally:
            with lock:
                state["active"] -= 1

    def call(index):
        client = LocalModelClient(
            f"http://model.test/alias{index}/v1", "test-only", httpx.MockTransport(transport)
        )
        with model_scope(
            bind=request_db,
            job_id=f"job-{index}",
            model_calls=1,
            stage="entity_recall" if index < 2 else "chapter_summary",
            progress=events.append,
        ):
            return invoke(client, timeout_s=2, total_timeout_s=4)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(call, range(4))) == [{"ok": True}] * 4
    assert state == {"active": 0, "peak": 2}
    requests = rows(request_db)
    assert len(requests) == 4 and {r.status for r in requests} == {"complete"}
    assert all(r.started_at and r.finished_at for r in requests)
    assert all(r.metrics["cache_tokens"] == 90 and r.metrics["prompt_ms"] == 20 for r in requests)
    assert any(r.metrics["queue_seconds"] > 0.1 for r in requests)
    assert {e["status"] for e in events} == {"queued", "running", "complete"}
    assert all(e["http_attempts"] == 1 for e in events if e["status"] == "complete")


def test_pause_in_queue_never_sends_http(request_db):
    holders = [
        RequestTicket("http://model.test/v1", str(i), 1, 20, bind=request_db) for i in range(2)
    ]
    assert all(ticket.admit() for ticket in holders)
    sent = []
    client = LocalModelClient(
        "http://model.test/v1",
        "test-only",
        httpx.MockTransport(
            lambda request: sent.append(request) or httpx.Response(200, json=reply())
        ),
    )
    started = monotonic()
    with model_scope(bind=request_db, should_stop=lambda: monotonic() - started > 0.05):
        with pytest.raises(ModelCancelled):
            invoke(client, total_timeout_s=2)
    assert sent == []
    request = rows(request_db)[-1]
    assert request.status == "cancelled" and request.started_at is None


def test_cancel_closes_http_before_releasing_slot_and_does_not_retry(request_db):
    closed = []
    sent = []

    async def transport(request):
        sent.append(monotonic())
        try:
            await asyncio.sleep(10)
        finally:
            assert rows(request_db)[-1].status == "running"
            closed.append(True)

    client = LocalModelClient("http://model.test/v1", "test-only", httpx.MockTransport(transport))
    with model_scope(bind=request_db, should_stop=lambda: sent and monotonic() - sent[0] > 0.08):
        with pytest.raises(ModelCancelled):
            invoke(client, timeout_s=5, total_timeout_s=8, timeout_retries=3)
    assert monotonic() - sent[0] < 1
    assert closed == [True] and len(sent) == 1
    assert rows(request_db)[-1].status == "cancelled"


def test_total_deadline_covers_read_chunks_not_just_each_socket_read(request_db):
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.015)
                yield b" "

        async def aclose(self):
            closed.append(True)

    client = LocalModelClient(
        "http://model.test/v1",
        "test-only",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowBody())),
    )
    started = monotonic()
    with model_scope(bind=request_db):
        with pytest.raises(StructuredModelError, match="model_total_timeout"):
            invoke(client, timeout_s=5, total_timeout_s=0.15, timeout_retries=3)
    assert monotonic() - started < 0.8
    assert closed == [True] and len(rows(request_db)) == 1
    assert rows(request_db)[0].metrics["error_code"] == "model_total_timeout"


def test_retries_share_logical_id_and_total_deadline(request_db):
    events, sent = [], []

    async def transport(request):
        sent.append(request)
        await asyncio.sleep(0.02)
        if len(sent) < 3:
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(200, json=reply())

    client = LocalModelClient("http://model.test/v1", "test-only", httpx.MockTransport(transport))
    with model_scope(bind=request_db, job_id="test", model_calls=7, progress=events.append):
        assert invoke(client, timeout_s=1, total_timeout_s=2, timeout_retries=3) == {"ok": True}
    requests = rows(request_db)
    assert [r.attempt for r in requests] == [1, 2, 3]
    assert len({r.logical_call_id for r in requests}) == 1
    assert max(r.deadline_at for r in requests) - min(r.deadline_at for r in requests) < timedelta(
        seconds=0.01
    )
    assert len([e for e in events if e["status"] == "retrying"]) == 2
    assert events[-1]["http_attempts"] == 3 and events[-1]["model_calls"] == 7


def test_queue_time_uses_total_budget_even_before_http(request_db):
    tickets = [
        RequestTicket("http://model.test/v1", str(i), 1, 20, bind=request_db) for i in range(2)
    ]
    assert all(ticket.admit() for ticket in tickets)
    client = LocalModelClient("http://model.test/v1", "test-only")
    with model_scope(bind=request_db):
        with pytest.raises(StructuredModelError, match="model_total_timeout"):
            invoke(client, total_timeout_s=0.08, timeout_retries=3)
    assert rows(request_db)[-1].started_at is None
    assert rows(request_db)[-1].status == "failed"
