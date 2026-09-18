"""Current, bounded Harness display. This cache never participates in recognition."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.document_analysis import DocumentRunCurrentState
from app.services.document_analysis.current_state import get_row, put_rows
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from app.services.document_analysis.state_artifacts import performance_policy

logger = logging.getLogger(__name__)
TEXT_LIMIT = 131072
OPERATION_LIMIT = 30
DETAIL_LIMIT = 16384


def _public(value):
    """Opaque continuation data is transported to Qwen, never exposed in this UI."""
    if isinstance(value, dict):
        return {key: ("[不透明推理字段已隐藏]" if key == "encrypted_content" else _public(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _detail(value):
    raw = value if isinstance(value, str) else json.dumps(_public(value), ensure_ascii=False)
    return {"text": raw[:DETAIL_LIMIT], "truncated": len(raw) > DETAIL_LIMIT}


def configuration(store, run):
    policy = performance_policy(store, run)
    options = policy.get("extraction_options") or {}
    return {
        "model": policy.get("model"), "model_revision": policy.get("model_revision"),
        "api_protocol": policy.get("api_protocol"),
        "gliner_enabled": bool(options.get("gliner2")),
        "mock_enabled": bool(options.get("external_sources")),
        "vocabulary_enabled": bool(options.get("vocabulary_overlay")),
        "request_budget": policy.get("request_budget"),
    }


def read_harness(store, run):
    return {
        "recognition_run_id": str(run.recognition_run_id),
        "configuration": configuration(store, run),
        "snapshot": get_row(store, run, "display:harness"),
    }


class HarnessObserver:
    """One writer per execution, short fenced transactions, no token/event history."""

    def __init__(self, bind, run_id, owner_id, token):
        self.bind, self.run_id, self.owner_id, self.token = bind, run_id, owner_id, token
        self.state = {"session_id": uuid4().hex, "sequence": 0, "call": None,
                      "output": "", "thinking": "", "truncated": [], "operations": [],
                      "tool_counts": {}, "updated_at": None}
        self.context = None
        self.context_dirty = False
        self.last_write = 0.0
        self.starts = {}

    def __call__(self, event_type, payload):
        now = datetime.now(UTC).isoformat()
        if event_type == "model_start":
            call_id = payload["call_id"]
            self.state.update(output="", thinking="", truncated=[])
            self.state["call"] = {key: payload.get(key) for key in (
                "call_id", "stage", "subject_label", "predicate_label", "input_tokens",
            )}
            self.state["call"].update(status="running", started_at=now,
                                      model=payload["request"]["model"], usage=None)
            self.context = {"call_id": call_id, "request": _public(payload["request"]),
                            "schema_card": deepcopy(payload["schema_card"]),
                            "class_labels": deepcopy(payload.get("class_labels", {}))}
            self.context_dirty = True
            self._start(call_id, "model", payload["request"]["model"], now)
        elif event_type == "delta":
            channel = payload["channel"]
            value = self.state[channel] + payload["text"]
            self.state[channel] = value[-TEXT_LIMIT:]
            if len(value) > TEXT_LIMIT and channel not in self.state["truncated"]:
                self.state["truncated"].append(channel)
        elif event_type == "model_end" and self.state["call"]:
            self.state["call"].update(status=payload["status"], usage=payload.get("usage"))
            # Some compatible endpoints emit readable reasoning only in final output items.
            for channel, item_type, part_types in (
                ("output", "message", {"output_text", "refusal"}),
                ("thinking", "reasoning", {"reasoning_text", "summary_text"}),
            ):
                if not self.state[channel]:
                    parts = [part.get("text", part.get("refusal", ""))
                             for item in payload.get("output_items", [])
                             if item.get("type") == item_type
                             for part in [*(item.get("content") or []),
                                          *(item.get("summary") or [])]
                             if isinstance(part, dict) and part.get("type") in part_types]
                    value = "\n".join(parts)
                    self.state[channel] = value[-TEXT_LIMIT:]
                    if len(value) > TEXT_LIMIT:
                        self.state["truncated"].append(channel)
            self._end(self.state["call"]["call_id"], payload["status"],
                      payload.get("usage") or payload.get("error_code"))
        elif event_type == "operation_start":
            self._start(payload["operation_id"], payload["kind"], payload["name"], now,
                        arguments=_detail(payload.get("arguments", "")))
            if payload["kind"] == "tool":
                counts = self.state["tool_counts"]
                # Invalid tool names must not create an unbounded display dictionary.
                from app.services.extraction.ontology_guided.tool_contracts import TOOL_DEFINITIONS
                name = payload["name"] if payload["name"] in TOOL_DEFINITIONS else "unknown_tool"
                counts[name] = counts.get(name, 0) + 1
        elif event_type == "operation_end":
            self._end(payload["operation_id"], payload["status"], payload.get("result"))
        elif event_type == "graph_update":
            identity = uuid4().hex
            self._start(identity, "graph", "关系图谱更新", now)
            self._end(identity, "completed", payload)
        else:
            return
        self.state["sequence"] += 1
        self.state["updated_at"] = now
        self.flush(force=event_type != "delta")

    def _start(self, identity, kind, name, now, **details):
        self.starts[identity] = monotonic()
        self.state["operations"].append({"id": identity, "kind": kind, "name": name,
            "status": "running", "started_at": now, "elapsed_ms": None, **details})
        self.state["operations"] = self.state["operations"][-OPERATION_LIMIT:]
        keep = {op["id"] for op in self.state["operations"]}
        self.starts = {key: value for key, value in self.starts.items() if key in keep}

    def _end(self, identity, status, result):
        for op in self.state["operations"]:
            if op["id"] == identity:
                op.update(status=status, result=_detail(result), elapsed_ms=round(
                    (monotonic() - self.starts.pop(identity, monotonic())) * 1000))

    def flush(self, *, force=False):
        if not force and monotonic() - self.last_write < 0.5:
            return
        try:
            with Session(self.bind) as db:
                store = DocumentAnalysisRunStore(db)
                store.assert_fence(self.run_id, self.owner_id, self.token, for_update=True)
                run = store.get_owned(self.run_id, self.owner_id)
                put_rows(store, run, DocumentRunCurrentState, "display:harness",
                         {"current": deepcopy(self.state)}, work_version=run.work_version)
                if self.context_dirty:
                    put_rows(store, run, DocumentRunCurrentState, "display:harness_context",
                             {"current": self.context}, work_version=run.work_version)
                db.commit()
                self.context_dirty = False
        except Exception:
            # Observation is optional display, never authority to fail/accept a task.
            logger.warning("Harness display update unavailable")
        self.last_write = monotonic()
