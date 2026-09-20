"""Request-local short references at the model transport boundary.

Only structured identity fields are encoded. Source text, reasoning and provider
identifiers remain untouched; persistence and authorization use canonical IDs.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import replace

from app.services.extraction.evidence_identity import canonical_json
from app.services.llm.local_client import ResponseTurn, StructuredModelError

_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_PREFIX = "@r:"
_TEXT_FIELDS = frozenset({
    "text", "context_text", "reason", "description", "label", "labels", "title", "summary",
})
_REFERENCE_LISTS = frozenset({
    "required_relation_checks", "requested_units", "processed_units", "unprocessed_units",
})
_LOCAL_REFERENCE_FIELDS = frozenset({"subject_id", "object_id", "object_ids", "binding_ids"})
MODEL_REFERENCE_INSTRUCTIONS = (
    "@r:开头是本请求登记引用；引用字段照抄，也不授予事实权限。"
    "新候选local_id使用e1、p1等新编号，禁止使用@r:登记引用。\n"
)


def _reference_field(name):
    return name != "local_id" and (
        name == "id" or name in _REFERENCE_LISTS
        or name.endswith(("_id", "_ids", "_ref", "_refs", "_hash", "_hashes"))
    )


def _load_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def reject_constant(_value):
        raise ValueError("nonstandard JSON constant")

    return json.loads(value, object_pairs_hook=unique, parse_constant=reject_constant)


def _local_ids(value):
    """Candidate IDs are local to one answer; never borrow another batch member's."""
    if isinstance(value, list):
        return frozenset(identity for item in value for identity in _local_ids(item))
    if not isinstance(value, dict):
        return frozenset()
    identities = {value["local_id"]} if isinstance(value.get("local_id"), str) else set()
    for name, item in value.items():
        if name not in _TEXT_FIELDS and name != "members":
            identities.update(_local_ids(item))
    return frozenset(identities)


class ModelReferenceProjection:
    """Encode one request and decode only references visible to that request.

    Aliases derive from the ID, so cumulative tool/reasoning turns never renumber
    earlier references. A prefix collision is rejected before any request is sent.
    This map is not authority: member and evidence checks still run after decoding.
    """

    def __init__(self, request: dict):
        self._encoded: dict[str, str] = {}
        self._decoded: dict[str, str] = {}
        self._request(request, "collect")
        self.request = self._request(request, "encode")
        if self._encoded and not self.request.get("instructions", "").startswith(
            MODEL_REFERENCE_INSTRUCTIONS,
        ):
            self.request["instructions"] = (
                MODEL_REFERENCE_INSTRUCTIONS + self.request.get("instructions", "")
            )

    def _reference(self, value, mode):
        if not isinstance(value, str):
            return value
        if mode == "collect":
            if value.startswith(_PREFIX):
                raise StructuredModelError("unknown_model_reference")
            if _HASH.fullmatch(value):
                alias = _PREFIX + value[:12].lower()
                previous = self._decoded.get(alias)
                if previous is not None and previous != value:
                    raise StructuredModelError("model_reference_collision")
                self._encoded[value] = alias
                self._decoded[alias] = value
            return value
        if mode == "encode":
            return self._encoded.get(value, value)
        if value.startswith(_PREFIX):
            if value not in self._decoded:
                raise StructuredModelError("unknown_model_reference")
            return self._decoded[value]
        if _HASH.fullmatch(value) and value not in self._encoded:
            raise StructuredModelError("unknown_model_reference")
        return value

    def _payload(self, value, mode, field="", local_ids=frozenset(), local_reference=False):
        if field in _TEXT_FIELDS:
            return deepcopy(value)
        if field == "local_id":
            if isinstance(value, str) and value.startswith(_PREFIX):
                raise StructuredModelError("unknown_model_reference")
            return deepcopy(value)
        if isinstance(value, list):
            if field == "members":
                return [self._payload(item, mode, local_ids=_local_ids(item)) for item in value]
            return [self._payload(item, mode, field, local_ids, local_reference) for item in value]
        if isinstance(value, dict):
            result = {}
            for name, item in value.items():
                # Full hash keys occur in reference maps; never rewrite substrings
                # such as composite version keys, IRIs, or model-created local IDs.
                key = self._reference(name, mode) if (
                    name not in local_ids and (_HASH.fullmatch(name) or name.startswith(_PREFIX))
                ) else name
                if key in result:
                    raise StructuredModelError("model_reference_collision")
                can_reference_local = name in _LOCAL_REFERENCE_FIELDS or (
                    name in ("source_id", "target_id") and "binding_kind" in value
                )
                result[key] = self._payload(item, mode, name, local_ids, can_reference_local)
            return result
        if local_reference and value in local_ids:
            return value
        return self._reference(value, mode) if _reference_field(field) else value

    def encode_payload(self, value):
        """Encode with the fixed request map, without admitting new references."""
        return self._payload(value, "encode", local_ids=_local_ids(value))

    def decode_payload(self, value):
        return self._payload(value, "decode", local_ids=_local_ids(value))

    def _json(self, value, mode):
        if not isinstance(value, str):
            return deepcopy(value)
        try:
            parsed = _load_json(value)
        except (ValueError, TypeError):
            # Existing stage parsing owns malformed JSON and its correction path.
            return value
        transformed = self._payload(parsed, mode, local_ids=_local_ids(parsed))
        return value if transformed == parsed else canonical_json(transformed)

    def _item(self, item, mode):
        if not isinstance(item, dict):
            return deepcopy(item)
        result = deepcopy(item)
        kind = item.get("type")
        if kind == "function_call":
            result["arguments"] = self._json(item.get("arguments"), mode)
        elif kind == "function_call_output":
            result["output"] = self._json(item.get("output"), mode)
        elif kind == "message" or (kind is None and "role" in item):
            content = item.get("content")
            if isinstance(content, str):
                result["content"] = self._json(content, mode)
            elif isinstance(content, list):
                for part in result["content"]:
                    if isinstance(part, dict) and part.get("type") in (
                        "input_text", "output_text",
                    ) and "text" in part:
                        part["text"] = self._json(part["text"], mode)
        return result

    def _output_group(self, items, mode):
        """Stage parsing concatenates all output_text parts; preserve their envelopes."""
        result = deepcopy(items)
        parts = [
            part for item in result if item.get("type") == "message"
            and isinstance(item.get("content"), list)
            for part in item["content"] if isinstance(part, dict)
            and part.get("type") == "output_text" and isinstance(part.get("text"), str)
        ]
        joined = "".join(part["text"] for part in parts)
        transformed = self._json(joined, mode)
        if transformed != joined:
            offset = 0
            for part in parts[:-1]:
                end = offset + len(part["text"])
                part["text"] = transformed[offset:end]
                offset = end
            parts[-1]["text"] = transformed[offset:]
        return result

    def _items(self, items, mode):
        result, group = [], []
        for item in items:
            if isinstance(item, dict) and (
                item.get("type") == "reasoning"
                or item.get("type") == "message" and item.get("role") == "assistant"
            ):
                group.append(item)
            else:
                result.extend(self._output_group(group, mode))
                group = []
                result.append(self._item(item, mode))
        result.extend(self._output_group(group, mode))
        return result

    def _schema(self, value, mode, field="", *, answer=False):
        if isinstance(value, list):
            return [self._schema(item, mode, field, answer=answer) for item in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        result = {}
        for name, item in value.items():
            if name == "properties" and isinstance(item, dict):
                result[name] = {key: self._schema(val, mode, key, answer=answer)
                                for key, val in item.items()}
            elif name in ("enum", "const") and field not in _TEXT_FIELDS and field != "local_id":
                def map_value(candidate):
                    if _reference_field(field):
                        return self._reference(candidate, mode)
                    if mode == "encode" and isinstance(candidate, str):
                        return self._encoded.get(candidate, candidate)
                    return candidate

                result[name] = ([map_value(val) for val in item] if isinstance(item, list)
                                else map_value(item))
            else:
                result[name] = self._schema(item, mode, field, answer=answer)
        if (mode == "encode" and self._encoded and _reference_field(field)
                and value.get("minLength") == value.get("maxLength") == 64):
            result["minLength"] = result["maxLength"] = len(_PREFIX) + 12
        if mode == "encode" and answer and field == "local_id" and value.get("type") == "string":
            result["pattern"] = "^[A-Za-z0-9_-]+$"
            description = "新候选编号如e1、p1，禁止使用@r:登记引用。"
            result["description"] = (result["description"] + "\n" + description
                                     if result.get("description") else description)
        return result

    def _request(self, request, mode):
        result = deepcopy(request)
        if isinstance(request.get("input"), list):
            result["input"] = self._items(request["input"], mode)
        elif isinstance(request.get("input"), str):
            result["input"] = self._json(request["input"], mode)
        for tool in result.get("tools", []):
            if isinstance(tool, dict) and "parameters" in tool:
                tool["parameters"] = self._schema(tool["parameters"], mode)
        output_format = result.get("text", {}).get("format", {})
        if "schema" in output_format:
            output_format["schema"] = self._schema(output_format["schema"], mode, answer=True)
        instructions = request.get("instructions")
        if isinstance(instructions, str):
            prefix = (MODEL_REFERENCE_INSTRUCTIONS
                      if instructions.startswith(MODEL_REFERENCE_INSTRUCTIONS) else "")
            body = instructions.removeprefix(prefix) if prefix else instructions
            head, separator, tail = body.rpartition("\n")
            result["instructions"] = prefix + head + separator + self._json(tail, mode)
        return result

    def decode_response(self, turn: ResponseTurn) -> ResponseTurn:
        return replace(turn, output_items=self._items(turn.output_items, "decode"))


def project_reference_payload(value: dict) -> dict:
    """Use the same stable short view for standalone tool-result token accounting."""
    projection = ModelReferenceProjection({"input": canonical_json(value)})
    return projection.encode_payload(value)
