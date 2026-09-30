import json
from copy import deepcopy
from dataclasses import dataclass

from app.services.document_harness import model


@dataclass
class Reply:
    response_status: str
    output_items: list
    usage: dict


def test_duplicate_response_keys_fail_without_losing_paid_raw_answer(monkeypatch):
    monkeypatch.setattr(model, "get_local_llm", lambda: object())
    text = '{"entities":{"E1":{"reason":"first"},"E1":{"reason":"second"}}}'

    def respond(_client, **kwargs):
        assert kwargs["max_output_tokens"] == 16384
        assert kwargs["extra_body"]["temperature"] == 0.1
        return Reply(
            response_status="completed",
            output_items=[{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            usage={"input_tokens": 100, "output_tokens": 20},
        )

    monkeypatch.setattr(model, "responses_create", respond)
    policy = {
        "protocol": "document-harness-v3", "max_output_tokens": 16384,
        "model": model.settings.local_llm_model,
        "model_revision": model.settings.local_llm_model_revision,
        "max_input_tokens": 32768, "timeout_seconds": 10,
        "temperature": 0.1,
    }
    result = model.call_model("type_alignment", {}, {}, policy)
    assert result["error"] == "harness_model_invalid_json"
    assert result["output"] is None
    assert result["raw_response"]["output_items"][0]["content"][0]["text"] == text
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_current_output_contract_is_visible_in_prompt_as_well_as_decoding_grammar(monkeypatch):
    monkeypatch.setattr(model, "get_local_llm", lambda: object())
    payload = {"sources": [{"source_id": "S1", "text": "原文字段"}]}
    schema = {"type": "object", "properties": {"answer": {
        "type": "string", "description": "按这个字段的语义回答",
    }}, "required": ["answer"], "additionalProperties": False}

    def respond(_client, **kwargs):
        visible = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        assert visible == {"input": payload, "schema": schema}
        assert kwargs["stream"] is True
        assert kwargs["text_format"]["schema"] == visible["schema"]
        assert kwargs["max_output_tokens"] == 16384
        assert model.request_size("discover", payload, schema) >= len(
            json.dumps(visible, ensure_ascii=False).encode()
        )
        return Reply(response_status="completed", output_items=[{
            "type": "message", "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
        }], usage={})

    monkeypatch.setattr(model, "responses_create", respond)
    policy = {
        "protocol": "document-harness-v3", "max_output_tokens": 16384,
        "model": model.settings.local_llm_model,
        "model_revision": model.settings.local_llm_model_revision,
        "max_input_tokens": 32768, "timeout_seconds": 10, "temperature": 0.1,
    }
    assert model.call_model("discover", payload, schema, policy)["output"] == {"answer": "ok"}


def test_gateway_schema_inlines_annotated_refs_and_requires_default_fields(monkeypatch):
    monkeypatch.setattr(model, "get_local_llm", lambda: object())
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"anchor": {"$ref": "#/$defs/Anchor", "description": "原文位置"}},
        "required": ["anchor"],
        "$defs": {"Anchor": {
            "type": "object", "additionalProperties": False,
            "properties": {"text": {"type": "string"},
                           "occurrence": {"enum": ["first", None], "default": None}},
            "required": ["text"],
        }},
    }
    original = deepcopy(schema)

    def respond(_client, **kwargs):
        adapted = kwargs["text_format"]["schema"]
        assert json.loads(kwargs["input_items"][0]["content"][0]["text"])["schema"] == adapted
        anchor = adapted["properties"]["anchor"]
        assert "$ref" not in anchor
        assert anchor["description"] == "原文位置"
        assert anchor["required"] == ["text", "occurrence"]
        assert anchor["properties"]["occurrence"] == {"enum": ["first", None],
                                                       "type": ["string", "null"]}
        return Reply(response_status="completed", output_items=[{
            "type": "message", "content": [{"type": "output_text", "text":
                                             '{"anchor":{"text":"车间","occurrence":null}}'}],
        }], usage={})

    monkeypatch.setattr(model, "responses_create", respond)
    policy = {
        "protocol": "document-harness-v3", "max_output_tokens": 16384,
        "model": model.settings.local_llm_model,
        "model_revision": model.settings.local_llm_model_revision,
        "max_input_tokens": 32768, "timeout_seconds": 10, "temperature": 0.1,
    }
    result = model.call_model("discover", {}, schema, policy)
    assert result["error"] is None
    assert schema == original


def test_legacy_input_budget_does_not_reject_large_model_request(monkeypatch):
    monkeypatch.setattr(model, "get_local_llm", lambda: object())
    payload = {"evidence": "文档证据" * 5000}
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    assert model.request_size("evidence_review", payload, schema) > 32768
    called = []

    def respond(_client, **kwargs):
        called.append(kwargs)
        return Reply(response_status="completed", output_items=[{
            "type": "message", "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
        }], usage={})

    monkeypatch.setattr(model, "responses_create", respond)
    policy = {
        "protocol": "document-harness-v3", "max_output_tokens": 16384,
        "model": model.settings.local_llm_model,
        "model_revision": model.settings.local_llm_model_revision,
        "max_input_tokens": 32768, "max_context_tokens": 65536,
        "timeout_seconds": 10, "temperature": 0.1,
    }
    assert model.call_model("evidence_review", payload, schema, policy)["output"] == {
        "answer": "ok",
    }
    assert len(called) == 1


def test_new_runs_do_not_freeze_input_or_context_budgets():
    policy = model.freeze_policy()
    assert "max_input_tokens" not in policy
    assert "max_context_tokens" not in policy
