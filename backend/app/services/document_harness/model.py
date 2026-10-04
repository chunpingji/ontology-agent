"""Local model transport with fresh prompts and schemas, independent of old adapters."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from time import monotonic

from app.config import settings
from app.services.llm.local_client import get_local_llm, responses_create

from .protocols import INSTRUCTIONS, OUTPUT_TOKENS, PROTOCOL
from .ranking import freeze_ranking_policy


def gateway_schema(schema, *, inline_annotated_refs=True):
    """Express the current contract in the gateway's strict JSON Schema subset."""
    definitions = schema.get("$defs", {})

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = deepcopy(value)
        if inline_annotated_refs and "$ref" in value and len(value) > 1:
            ref = value.pop("$ref")
            if not ref.startswith("#/$defs/"):
                raise ValueError("unsupported_nonlocal_schema_reference")
            value = {**deepcopy(definitions[ref.rsplit("/", 1)[1]]), **value}
        result = {key: visit(item) for key, item in value.items()
                  if key not in {"default", "title"}}
        if result.get("type") == "object" and "properties" in result:
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        if "type" not in result and "enum" in result:
            values = result["enum"]
            if values and all(isinstance(item, str) for item in values):
                result["type"] = "string"
            elif values and all(item is None or isinstance(item, str) for item in values):
                result["type"] = ["string", "null"]
        if "type" not in result and isinstance(result.get("const"), str):
            result["type"] = "string"
        elif "type" not in result and result.get("const") is None and "const" in result:
            result["type"] = "null"
        return result

    return visit(schema)


def prompt_schema(stage, schema):
    """Read shared definitions once; the gateway still receives its strict schema."""
    return gateway_schema(schema, inline_annotated_refs=stage != "discover")


def freeze_policy() -> dict:
    from .evidence_gate import normalize_table_rules
    from .work import DEFAULT_POLICY

    if settings.local_llm_max_tokens < OUTPUT_TOKENS:
        raise ValueError("harness_requires_16384_output_tokens")
    return {
        "protocol": PROTOCOL,
        "model": settings.local_llm_model,
        "model_revision": settings.local_llm_model_revision,
        "temperature": settings.local_llm_temperature,
        "max_output_tokens": OUTPUT_TOKENS,
        "timeout_seconds": settings.local_llm_total_timeout_s,
        "card_ranking": freeze_ranking_policy(settings),
        "execution_policy": {**DEFAULT_POLICY, "table_relation_rules": normalize_table_rules(
            settings.document_harness_table_rules_json,
        )},
    }


def request_size(stage, payload, schema):
    """Estimate prompt/schema UTF-8 bytes for the independent batch packing limit.

    A fixed margin covers message delimiters. This is not a tokenizer count or
    a measurement of paid usage; actual usage comes from the model response.
    """
    return (
        len(
            json.dumps(
                {"instructions": INSTRUCTIONS[stage], "input": payload,
                 "schema": prompt_schema(stage, schema)},
                ensure_ascii=False,
            ).encode()
        )
        + 512
    )


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def call_model(stage, payload, schema, policy):
    if policy.get("protocol") != PROTOCOL or policy.get("max_output_tokens") != OUTPUT_TOKENS:
        raise ValueError("harness_policy_mismatch")
    if (
        policy["model"] != settings.local_llm_model
        or policy["model_revision"] != settings.local_llm_model_revision
    ):
        raise ValueError("harness_model_dependency_changed")
    started = monotonic()
    wire = gateway_schema(schema)
    turn = responses_create(
        get_local_llm(),
        model=policy["model"],
        input_items=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        # A decoding grammar constrains tokens but need not expose
                        # field meanings to the model. Send the same current
                        # contract as readable input; request_size already counts it.
                        "text": json.dumps(
                            {"input": payload, "schema": prompt_schema(stage, schema)},
                            ensure_ascii=False, separators=(",", ":"),
                        ),
                    }
                ],
            }
        ],
        instructions=INSTRUCTIONS[stage],
        stream=True,
        max_output_tokens=OUTPUT_TOKENS,
        text_format={
            "type": "json_schema",
            "name": f"harness_{stage}",
            "strict": True,
            "schema": wire,
        },
        reasoning={"effort": "none"},
        extra_body={
            "temperature": policy["temperature"],
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout_s=policy["timeout_seconds"],
        total_timeout_s=policy["timeout_seconds"],
    )
    result = {
        "output": None,
        "raw_response": asdict(turn),
        "usage": turn.usage or {},
        "seconds": monotonic() - started,
        "error": None,
    }
    if turn.response_status != "completed":
        result["error"] = "harness_model_" + turn.response_status
        return result
    messages = [
        part.get("text", "")
        for item in turn.output_items
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    try:
        output = json.loads("".join(messages), object_pairs_hook=_unique_object)
        if not isinstance(output, dict):
            raise ValueError("object required")
        result["output"] = output
    except (ValueError, TypeError):
        result["error"] = "harness_model_invalid_json"
    return result
