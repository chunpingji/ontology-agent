"""Local LLM client wrapper (OpenAI-compatible endpoint).

Returns ``None`` when ``local_llm_enabled=False`` or the ``openai`` package
is not installed — callers gate on the return value so the pipeline degrades
gracefully to zero-LLM behaviour.

012: ``get_local_llm()`` — client factory.
013: ``chat_with_schema()`` — structured-output helper with prompt-based fallback.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4

from app.services.extraction.annotation_execution import ExecutionLost
from app.services.extraction.text_scanner import strip_tag_blocks
from app.services.llm.model_runtime import (
    ModelCancelled,
    ModelWaitFailure,
    check_cancelled,
    runtime,
)
from app.services.llm.model_scheduler import (
    HEARTBEAT_SECONDS,
    POLL_SECONDS,
    ModelSlotLost,
    RequestTicket,
)

logger = logging.getLogger(__name__)


class StructuredModelError(RuntimeError):
    """Stable failure code; provider responses and credentials stay out of diagnostics."""


def _model_error(exc):
    if isinstance(exc, StructuredModelError):
        return exc
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return StructuredModelError("model_timeout")
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return StructuredModelError("model_parse_error")
    return StructuredModelError("model_request_failed")


def _response_text(response):
    if not response.choices:
        raise StructuredModelError("model_empty_response")
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise StructuredModelError("model_output_truncated")
    refusal = getattr(choice.message, "refusal", None)
    if isinstance(refusal, str) and refusal:
        raise StructuredModelError("model_refusal")
    if not choice.message.content:
        raise StructuredModelError("model_empty_response")
    return choice.message.content


@dataclass(frozen=True)
class LocalModelClient:
    """A factory: each cancellable request owns and closes its async connection."""

    base_url: str
    api_key: str = field(repr=False)
    transport: Any = field(default=None, repr=False)

    def open(self):
        import httpx
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=self.transport, trust_env=False),
        )


def get_local_llm():
    from app.config import settings

    if not settings.local_llm_enabled:
        return None
    try:
        import openai  # noqa: F401
    except ImportError:
        logger.warning("openai package not installed — LLM gap filling disabled")
        return None
    return LocalModelClient(settings.local_llm_base_url, settings.local_llm_api_key)


def _usage(response):
    """Only numeric provider measurements; never response text or credentials."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    values = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cache_tokens": getattr(details, "cached_tokens", None),
    }
    timings = getattr(response, "timings", None)
    if isinstance(timings, dict):
        for key in ("prompt_ms", "predicted_ms", "cache_n", "prompt_n", "predicted_n"):
            values[key] = timings.get(key)
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


async def _send(client, kwargs):
    if isinstance(client, LocalModelClient):
        async with client.open() as connection:
            return await connection.chat.completions.create(**kwargs)
    # Injected async clients are also supported; their lifetime belongs to the caller.
    response = client.chat.completions.create(**kwargs)
    if not inspect.isawaitable(response):
        raise TypeError("chat_with_schema requires an async client or LocalModelClient factory")
    return await response


async def _http_attempt(client, kwargs, ticket, deadline, request_timeout):
    operation = None
    on_wait = runtime.get().get("on_model_wait")
    try:
        ticket.publish("queued")
        while True:
            check_cancelled()
            if on_wait is not None:
                on_wait()
            if monotonic() >= deadline:
                raise StructuredModelError("model_total_timeout")
            if ticket.admit():
                break
            await asyncio.sleep(min(POLL_SECONDS, max(0, deadline - monotonic())))
        check_cancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise StructuredModelError("model_total_timeout")
        limit = min(request_timeout, remaining)
        kwargs["timeout"] = limit

        # asyncio's deadline covers the complete HTTP response, including repeated
        # read chunks. Cancellation is awaited BEFORE the ticket releases its slot.
        async def request():
            async with asyncio.timeout(limit):
                check_cancelled()
                ticket.start()
                return await _send(client, kwargs)

        operation = asyncio.create_task(request())
        heartbeat = monotonic()
        while not operation.done():
            await asyncio.wait({operation}, timeout=POLL_SECONDS)
            check_cancelled()
            if on_wait is not None:
                on_wait()
            if monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                ticket.heartbeat()
                heartbeat = monotonic()
        return operation.result()
    finally:
        if operation:
            if not operation.done():
                operation.cancel()
            try:
                await operation
            except (Exception, asyncio.CancelledError):
                # Also retrieve a completed task's exception when ownership was
                # lost at the same instant; preserve the outer control signal.
                pass


def chat_with_schema(
    client,
    *,
    system: str,
    user: str,
    schema: dict,
    schema_name: str = "response",
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool = False,
    timeout_s: float | None = None,
    max_attempts: int = 2,
    raise_on_error: bool = False,
    timeout_retries: int = 0,
    total_timeout_s: float | None = None,
) -> dict[str, Any] | None:
    """Shared admission, hard total deadline and cancellable, observable retries.

    Queueing, HTTP attempts and optional schema fallback share ONE total budget.
    SDK retries are disabled. Ownership and pause signals are never swallowed.
    This synchronous facade is used by existing worker/thread entry points.
    """
    from app.config import settings

    if client is None or max_attempts < 1:
        if raise_on_error:
            raise StructuredModelError("model_unavailable")
        return None
    total = total_timeout_s if total_timeout_s is not None else settings.local_llm_total_timeout_s
    deadline = monotonic() + total
    request_timeout = timeout_s if timeout_s is not None else total
    call_id = uuid4().hex
    kwargs = {
        "model": model or settings.local_llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        "temperature": temperature if temperature is not None else settings.local_llm_temperature,
        "max_tokens": max_tokens or settings.local_llm_max_tokens,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    }

    async def run():
        attempt, retries, fallback = 0, 0, False
        while True:
            check_cancelled()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise StructuredModelError("model_total_timeout")
            attempt += 1
            ticket = RequestTicket(str(client.base_url), call_id, attempt, remaining)
            try:
                response = await _http_attempt(
                    client, dict(kwargs), ticket, deadline, request_timeout
                )
                raw = _response_text(response)
                parsed = _extract_json_object(raw) if fallback else json.loads(raw)
                if not isinstance(parsed, dict):
                    raise StructuredModelError("model_parse_error")
                ticket.finish("complete", **_usage(response))
                return parsed
            except (ModelCancelled, ExecutionLost, ModelSlotLost, ModelWaitFailure):
                ticket.finish("cancelled")
                raise
            except Exception as exc:
                failure = _model_error(exc)
                if monotonic() >= deadline:
                    failure = StructuredModelError("model_total_timeout")
                ticket.finish("failed", error_code=str(failure))
                if str(failure) == "model_timeout" and retries < timeout_retries:
                    retries += 1
                    ticket.publish("retrying")
                    logger.warning("Evidence model timeout; retry %s/%s", retries, timeout_retries)
                    continue
                if (
                    not fallback
                    and max_attempts > 1
                    and str(failure) not in {"model_timeout", "model_total_timeout"}
                ):
                    fallback = True
                    kwargs.pop("response_format")
                    kwargs["messages"] = [
                        kwargs["messages"][0],
                        {
                            "role": "user",
                            "content": user
                            + "\n\n/no_think\nRespond ONLY with a valid JSON object "
                            "matching this schema:\n"
                            + json.dumps(schema, ensure_ascii=False),
                        },
                    ]
                    ticket.publish("retrying")
                    continue
                raise failure from exc

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(run())
        # A few legacy async API handlers call this synchronous facade directly.
        # Keep the loop/connection in one thread, propagating its ownership context.
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(copy_context().run, lambda: asyncio.run(run())).result()
    except StructuredModelError:
        if raise_on_error:
            raise
        return None


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """Decode one object without treating braces in JSON strings as structure."""
    text = strip_tag_blocks(raw, "think").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(text, start)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
