"""Responses transport contracts against the real SDK and an isolated HTTP transport."""

import asyncio
import json
from copy import deepcopy
from time import monotonic

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_request import LocalModelRequest
from app.services.extraction.annotation_execution import ExecutionLost
from app.services.llm import local_client
from app.services.llm.local_client import LocalModelClient, StructuredModelError
from app.services.llm.model_runtime import ModelCancelled, ModelWaitFailure, model_scope
from app.services.llm.model_scheduler import RequestTicket


def response_body():
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 1,
        "model": "qwen-test",
        "status": "completed",
        "output": [
            {"id": "reasoning_1", "type": "reasoning", "summary": [],
             "encrypted_content": "opaque-synthetic-content"},
            {"id": "item_2", "type": "function_call", "call_id": "call_3",
             "name": "inspect_evidence", "arguments": '{"evidence_ids":["e1"]}',
             "status": "completed"},
            {"id": "message_4", "type": "message", "role": "assistant",
             "status": "completed", "content": [
                 {"type": "output_text", "text": "checking", "annotations": []},
             ]},
        ],
        "error": None,
        "incomplete_details": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 18,
            "total_tokens": 138,
            "input_tokens_details": {"cached_tokens": 90},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


def invoke(client, **options):
    return local_client.responses_create(
        client, input_items=[{"role": "user", "content": "synthetic evidence"}],
        instructions="Only inspect authorized evidence.", **options,
    )


def requests(bind):
    with Session(bind) as db:
        return list(db.scalars(select(LocalModelRequest).order_by(LocalModelRequest.sequence)))


def test_responses_preserves_output_order_identity_and_native_request_fields(
    isolated_model_scheduler,
):
    sent = []
    body = response_body()
    tool = {"type": "function", "name": "inspect_evidence", "description": "Read evidence",
            "strict": False, "parameters": {"type": "object", "properties": {}}}

    async def transport(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=body)

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=isolated_model_scheduler()):
        turn = invoke(client, model="qwen-test", tools=[tool], tool_choice="auto",
                      max_output_tokens=128, include=["reasoning.encrypted_content"],
                      reasoning={"effort": "none"})

    assert sent == [("/v1/responses", {
        "model": "qwen-test", "input": [{"role": "user", "content": "synthetic evidence"}],
        "instructions": "Only inspect authorized evidence.", "store": False,
        "tools": [tool], "tool_choice": "auto", "max_output_tokens": 128,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": "none"},
    })]
    assert turn.response_id == body["id"]
    assert turn.response_status == "completed"
    assert turn.output_items == body["output"]
    assert turn.output_items[1]["id"] != turn.output_items[1]["call_id"]
    assert turn.usage == body["usage"]
    metrics = requests(isolated_model_scheduler())[0].metrics
    assert metrics["prompt_tokens"] == 120
    assert metrics["completion_tokens"] == 18
    assert metrics["cache_tokens"] == 90
    assert metrics["reasoning_tokens"] == 10
    assert metrics["total_tokens"] == 138


def test_responses_answer_format_and_complete_items_can_be_resubmitted(
    isolated_model_scheduler,
):
    sent = []
    original = response_body()
    schema = {"type": "json_schema", "name": "answer", "strict": False,
              "schema": {"type": "object", "properties": {}}}

    async def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=original)

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=isolated_model_scheduler()):
        first = invoke(client)
        continued = [
            {"role": "user", "content": "synthetic evidence"}, *first.output_items,
            {"type": "function_call_output", "call_id": "call_3", "output": '{"ok":true}'},
        ]
        before = deepcopy(continued)
        local_client.responses_create(client, input_items=continued, instructions="Answer now.",
                                      text_format=schema, max_output_tokens=64)
    assert continued == before
    assert sent[1]["input"] == continued
    assert sent[1]["text"] == {"format": schema}
    assert sent[1]["instructions"] == "Answer now."
    assert "tools" not in sent[1] and "include" not in sent[1]
    assert "previous_response_id" not in sent[1] and "conversation" not in sent[1]
    assert len(requests(isolated_model_scheduler())) == 2


@pytest.mark.parametrize("status", ["incomplete", "failed", "queued", "completed"])
def test_response_failures_and_refusals_reach_controller_without_retries(
    isolated_model_scheduler, status,
):
    sent = []
    body = response_body()
    body["status"] = status
    body["incomplete_details"] = {"reason": "max_output_tokens"} if status == "incomplete" else None
    body["error"] = {"code": "server_error", "message": "synthetic"} if status == "failed" else None
    if status == "completed":
        body["output"][-1]["content"] = [{"type": "refusal", "refusal": "synthetic refusal"}]
    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(
        lambda request: sent.append(request) or httpx.Response(200, json=body),
    ))
    with model_scope(bind=isolated_model_scheduler()):
        turn = invoke(client)
    assert turn.response_status == status
    assert turn.output_items == body["output"]
    assert turn.incomplete_details == body["incomplete_details"]
    assert turn.error == body["error"]
    assert len(sent) == 1
    assert requests(isolated_model_scheduler())[0].status == "complete"


@pytest.mark.parametrize("usage", [None, {"input_tokens": 3}])
def test_missing_usage_is_unknown_and_not_zero(isolated_model_scheduler, usage):
    body = response_body()
    body["usage"] = usage
    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(
        lambda _: httpx.Response(200, json=body),
    ))
    with model_scope(bind=isolated_model_scheduler()):
        turn = invoke(client)
    assert turn.usage == usage
    metrics = requests(isolated_model_scheduler())[0].metrics
    assert "completion_tokens" not in metrics and "cache_tokens" not in metrics
    assert "reasoning_tokens" not in metrics and "total_tokens" not in metrics


@pytest.mark.parametrize("failure", ["http_500", "timeout"])
def test_responses_http_failure_has_one_attempt_and_no_chat_fallback(
    isolated_model_scheduler, failure,
):
    sent = []

    async def transport(request):
        sent.append(request.url.path)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(500, json={"error": {"message": "synthetic"}})

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    code = "model_timeout" if failure == "timeout" else "model_request_failed"
    with model_scope(bind=isolated_model_scheduler()):
        with pytest.raises(StructuredModelError, match=code):
            invoke(client, total_timeout_s=3)
    assert sent == ["/v1/responses"]
    rows = requests(isolated_model_scheduler())
    assert len(rows) == 1 and rows[0].status == "failed"


def test_injected_async_sdk_cannot_retry_behind_the_single_attempt_contract(
    isolated_model_scheduler,
):
    sent = []
    client = AsyncOpenAI(base_url="http://model.test/v1", api_key="test", max_retries=2,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(
                             lambda request: sent.append(request.url.path)
                             or httpx.Response(500, json={"error": {"message": "synthetic"}}),
                         )))

    async def run():
        with model_scope(bind=isolated_model_scheduler()):
            with pytest.raises(StructuredModelError, match="model_request_failed"):
                invoke(client, total_timeout_s=3)
        assert not client.is_closed()
        await client.close()

    asyncio.run(run())
    assert sent == ["/v1/responses"]
    assert len(requests(isolated_model_scheduler())) == 1


def test_responses_queue_pause_sends_no_http(isolated_model_scheduler):
    bind = isolated_model_scheduler()
    holders = [RequestTicket("http://model.test/v1", str(i), 1, 20, bind=bind) for i in range(2)]
    assert all(ticket.admit() for ticket in holders)
    sent = []
    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(
        lambda request: sent.append(request) or httpx.Response(200, json=response_body()),
    ))
    started = monotonic()
    with model_scope(bind=bind, should_stop=lambda: monotonic() - started > 0.05):
        with pytest.raises(ModelCancelled):
            invoke(client, total_timeout_s=2)
    assert not sent
    row = requests(bind)[-1]
    assert row.status == "cancelled" and row.started_at is None


def test_responses_cancel_closes_request_before_releasing_slot(isolated_model_scheduler):
    bind = isolated_model_scheduler()
    sent, closed = [], []

    async def transport(request):
        sent.append(monotonic())
        try:
            await asyncio.sleep(5)
        finally:
            assert requests(bind)[-1].status == "running"
            closed.append(True)

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=bind, should_stop=lambda: sent and monotonic() - sent[0] > 0.05):
        with pytest.raises(ModelCancelled):
            invoke(client, total_timeout_s=3)
    assert len(sent) == 1 and closed == [True]
    assert requests(bind)[-1].status == "cancelled"


@pytest.mark.parametrize("failure_type", [ExecutionLost, ModelWaitFailure])
def test_responses_ownership_and_durability_failures_propagate(
    isolated_model_scheduler, failure_type,
):
    failure = failure_type("synthetic barrier failure")

    def fail_barrier():
        raise failure

    client = LocalModelClient("http://model.test/v1", "test")
    with model_scope(bind=isolated_model_scheduler(), on_model_wait=fail_barrier):
        with pytest.raises(failure_type) as caught:
            invoke(client)
    assert caught.value is failure
    assert requests(isolated_model_scheduler())[-1].status == "cancelled"


def test_responses_total_deadline_stops_read_chunks(isolated_model_scheduler):
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(
        lambda _: httpx.Response(200, stream=SlowBody()),
    ))
    with model_scope(bind=isolated_model_scheduler()):
        with pytest.raises(StructuredModelError, match="model_total_timeout"):
            invoke(client, timeout_s=3, total_timeout_s=0.1)
    assert closed == [True]
    rows = requests(isolated_model_scheduler())
    assert len(rows) == 1 and rows[0].status == "failed"


@pytest.mark.parametrize("ending", ["complete", "truncated", "cancel"])
def test_real_sdk_stream_delivers_before_final_and_closes(isolated_model_scheduler, ending):
    observed, closed, sent = [], [], []
    bind = isolated_model_scheduler()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for channel, value in [("output_text", '{"ok":'), ("reasoning_text", "可读思考")]:
                payload = {"type": f"response.{channel}.delta", "delta": value,
                           "item_id": "i", "output_index": 0, "content_index": 0,
                           "sequence_number": 1}
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
                assert observed[-1][0] == "delta"  # callback ran BEFORE next network chunk
            if ending == "cancel":
                await asyncio.sleep(5)
            elif ending == "complete":
                payload = {"type": "response.completed", "response": response_body(),
                           "sequence_number": 3}
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(True)

    async def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Body())

    client = LocalModelClient("http://model.test/v1", "test", httpx.MockTransport(transport))
    with model_scope(bind=bind, on_harness_event=lambda kind, value: observed.append((kind, value)),
                     should_stop=lambda: ending == "cancel" and len(observed) >= 2):
        if ending == "complete":
            result = invoke(client)
            assert result.output_items == response_body()["output"]
            assert result.usage == response_body()["usage"]
        else:
            with pytest.raises(ModelCancelled if ending == "cancel" else StructuredModelError):
                invoke(client, total_timeout_s=2)
    assert sent[0]["stream"] is True and len(sent) == 1
    assert closed
    assert observed[0] == ("delta", {"channel": "output", "text": '{"ok":'})
    assert observed[1] == ("delta", {"channel": "thinking", "text": "可读思考"})
    assert requests(bind)[-1].status == {"complete": "complete", "cancel": "cancelled",
                                       "truncated": "failed"}[ending]
