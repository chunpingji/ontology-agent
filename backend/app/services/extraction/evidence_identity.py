"""Canonical dependency identities; no process IDs or executable matching rules."""

from __future__ import annotations

import dataclasses
import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel


def canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical object keys must be strings")
        return {key: canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (set, frozenset)):
        return sorted((canonical_value(item) for item in value), key=canonical_json)
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def evidence_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(namespace: str, value: Any) -> str:
    return evidence_hash({"namespace": namespace, "value": value})
