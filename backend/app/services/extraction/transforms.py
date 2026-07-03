"""Declarative per-value transforms for property bindings (014, R6/FR-003/FR-009).

Four declared transform types, applied uniformly by both source adapters
(``db_reader``/``api_reader``) and re-used at fact-building (R10/FR-015):

- ``none``            — pass-through.
- ``controlled_vocab`` — map a raw source value → ontology term. Config is the
  vocab source: an explicit ``{"map": {raw: term}}`` (contract shape), a
  ``{"vocab": "oeb"}`` key into the shared :data:`CONTROLLED_VOCAB`, or an E3
  ``OntologyDataProperty.controlled_vocab`` dict passed straight through.
- ``pattern``          — regex *validation* (config ``{"pattern": "..."}``): the
  value is kept either way; a non-match only attaches an issue note.
- ``cast``             — coerce to a declared datatype (config ``{"to": "integer"}``;
  aligns with ``OntologyDataProperty.datatype``).

Transform failures are **per-value and non-fatal** (spec Edge Cases): the
offending value is returned unchanged with an issue ``note``; sibling values on
the same record still succeed and the row is never discarded. Callers collect
the notes onto the candidate for reviewer visibility.

No new normalization engine (Principle V): ``controlled_vocab`` reuses the
existing :func:`normalize_vocab`/:data:`CONTROLLED_VOCAB` seam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.services.extraction.vocabulary import CONTROLLED_VOCAB, normalize_vocab

# Cast targets aligned with OntologyDataProperty.datatype (E3).
CAST_TYPES = ("string", "integer", "decimal", "boolean", "date", "dateTime", "anyURI")

_TRUE_TOKENS = {"true", "1", "yes", "y", "t", "是", "真", "有"}
_FALSE_TOKENS = {"false", "0", "no", "n", "f", "否", "假", "无"}


@dataclass(frozen=True)
class TransformOutcome:
    """Result of one per-value transform: the (possibly) converted ``value`` and
    an optional non-blocking ``note`` when the transform could not be applied."""

    value: Any
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.note is None


def _vocab_map(config: dict | None) -> dict[str, str]:
    """Extract an explicit raw→term mapping from a ``controlled_vocab`` config.

    Accepts ``{"map": {...}}`` (contract shape) or a bare ``{raw: term}`` dict
    (an E3 ``controlled_vocab`` passed straight through). ``{"vocab": key}`` is
    handled separately via the shared vocabulary and yields no explicit map.
    """
    if not isinstance(config, dict):
        return {}
    inner = config.get("map")
    if isinstance(inner, dict):
        return {str(k): v for k, v in inner.items()}
    # A bare dict with no reserved keys is treated as the map itself.
    if "vocab" not in config and "map" not in config:
        return {str(k): v for k, v in config.items() if isinstance(v, (str, int, float))}
    return {}


def _apply_controlled_vocab(value: Any, config: dict | None) -> TransformOutcome:
    key = str(value).strip()
    mapping = _vocab_map(config)
    if key in mapping:
        return TransformOutcome(mapping[key])
    # A "vocab" hint scopes to one shared list; otherwise scan all shared vocabs.
    if isinstance(config, dict) and config.get("vocab") in CONTROLLED_VOCAB:
        terms = CONTROLLED_VOCAB[config["vocab"]]
        norm = key.upper().replace(" ", "")
        for term in terms:
            if term.upper().replace(" ", "") == norm:
                return TransformOutcome(term)
    else:
        canon = normalize_vocab(key)
        if canon is not None:
            return TransformOutcome(canon)
    return TransformOutcome(value, note=f"controlled_vocab: 无匹配取值 '{value}'")


def _pattern_of(config: dict | None) -> str | None:
    if isinstance(config, dict):
        pat = config.get("pattern")
        return pat if isinstance(pat, str) else None
    if isinstance(config, str):
        return config
    return None


def _apply_pattern(value: Any, config: dict | None) -> TransformOutcome:
    pattern = _pattern_of(config)
    if not pattern:
        return TransformOutcome(value, note="pattern: 缺少 pattern 配置")
    try:
        matched = re.search(pattern, str(value)) is not None
    except re.error as exc:
        return TransformOutcome(value, note=f"pattern: 非法正则 {exc}")
    if matched:
        return TransformOutcome(value)
    return TransformOutcome(value, note=f"pattern: 值 '{value}' 不匹配 /{pattern}/")


def _cast_target(config: dict | None) -> str | None:
    if isinstance(config, dict):
        for key in ("to", "type", "target", "datatype"):
            tgt = config.get(key)
            if isinstance(tgt, str):
                return tgt
        return None
    if isinstance(config, str):
        return config
    return None


def _apply_cast(value: Any, config: dict | None) -> TransformOutcome:
    target = _cast_target(config)
    if not target:
        return TransformOutcome(value, note="cast: 缺少目标类型配置")
    raw = str(value).strip()
    try:
        if target == "integer":
            return TransformOutcome(int(raw))
        if target == "decimal":
            return TransformOutcome(float(raw))
        if target == "boolean":
            low = raw.lower()
            if low in _TRUE_TOKENS:
                return TransformOutcome(True)
            if low in _FALSE_TOKENS:
                return TransformOutcome(False)
            raise ValueError(f"无法解析布尔值 '{value}'")
        if target == "date":
            return TransformOutcome(date.fromisoformat(raw).isoformat())
        if target == "dateTime":
            return TransformOutcome(datetime.fromisoformat(raw).isoformat())
        # string / anyURI / unknown → stringify.
        return TransformOutcome(str(value))
    except (ValueError, TypeError) as exc:
        return TransformOutcome(value, note=f"cast→{target}: {exc}")


def apply_transform(transform_type: str | None, config: dict | None, value: Any) -> TransformOutcome:
    """Apply one declared transform to a single source value (R6).

    Returns a :class:`TransformOutcome`; a failure never raises — the original
    value is returned with a ``note`` so the caller can keep the row and surface
    the issue for review. ``None`` values pass through untouched.
    """
    if value is None:
        return TransformOutcome(None)
    ttype = transform_type or "none"
    if ttype == "none":
        return TransformOutcome(value)
    if ttype == "controlled_vocab":
        return _apply_controlled_vocab(value, config)
    if ttype == "pattern":
        return _apply_pattern(value, config)
    if ttype == "cast":
        return _apply_cast(value, config)
    return TransformOutcome(value, note=f"未知 transform_type '{transform_type}'")


def validate_transform_config(transform_type: str | None, config: dict | None) -> str | None:
    """Static well-formedness check for a transform config (V5, FR-005).

    Returns an error message when the config cannot possibly drive the declared
    transform, else ``None``. Used by binding validation *before* any extraction.
    """
    ttype = transform_type or "none"
    if ttype == "none":
        return None
    if ttype == "controlled_vocab":
        if config is None:
            return "controlled_vocab 需要 map/vocab 配置"
        if not isinstance(config, dict):
            return "controlled_vocab 配置须为对象"
        if not _vocab_map(config) and config.get("vocab") not in CONTROLLED_VOCAB:
            return "controlled_vocab 配置缺少有效的 map 或已知 vocab"
        return None
    if ttype == "pattern":
        pattern = _pattern_of(config)
        if not pattern:
            return "pattern 需要 pattern 配置"
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"pattern 非法正则：{exc}"
        return None
    if ttype == "cast":
        target = _cast_target(config)
        if not target:
            return "cast 需要目标类型配置"
        if target not in CAST_TYPES:
            return f"cast 目标类型 '{target}' 不受支持"
        return None
    return f"未知 transform_type '{transform_type}'"
