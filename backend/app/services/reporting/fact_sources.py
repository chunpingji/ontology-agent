"""016+: fact-source providers for semantic slots.

A semantic slot's *associated ontology* (关联本体) is not only the source-document
relationship graph (``ontology_relation`` coverage). It can also draw on FACT SOURCES
(事实源): the source document's extracted facts, structured-mapped A-Box facts
(organization / personnel / permissions), external structured data, and declarative-rule
(E11/E12/E13) inference results — the *deterministic fact source*, injected READ-ONLY
(FR-009).

This module realizes :class:`~app.services.reporting.ast_template.FactSourceBinding`
(previously a placeholder) as a provider registry keyed on ``binding.source``. A provider
returns ``[{label, value, source_ref}]`` rows — read-only projections of artifacts already
computed by the generator (edges / Facts / RiskRow[]). Providers NEVER run a new extraction
pass and NEVER influence deterministic evaluation.

Registered now: ``extraction`` (source-doc facts) and ``rule_results`` (deterministic
declarative-rule results). Deferred (stub → no rows, so the validator keeps emitting its
non-counting MANUAL coverage position): ``abox.*`` (org/personnel/permissions) and
``external.*``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.reporting.ast_template import _short

logger = logging.getLogger(__name__)

# A provider maps (binding, context) → read-only fact rows.
FactSourceProvider = Callable[[Any, "FactContext"], list[dict]]


@dataclass
class FactContext:
    """Read-only artifacts a fact-source provider may project. All are already computed
    by ``generate_with_coverage``; providers reuse them (no re-extraction)."""

    edges: list[dict] = field(default_factory=list)
    facts: Any = None
    assessment_rows: list = field(default_factory=list)
    engine: Any | None = None


_REGISTRY: dict[str, FactSourceProvider] = {}


def register_fact_source(source: str) -> Callable[[FactSourceProvider], FactSourceProvider]:
    """Register a provider for a ``FactSourceBinding.source`` value. A ``"ns.*"`` key
    matches any ``"ns.<x>"`` source not claimed by an exact registration."""

    def _deco(fn: FactSourceProvider) -> FactSourceProvider:
        _REGISTRY[source] = fn
        return fn

    return _deco


def resolve_fact_source(binding: Any, ctx: FactContext) -> list[dict]:
    """Resolve one ``FactSourceBinding`` to read-only ``[{label, value, source_ref}]`` rows.

    Unknown / not-yet-implemented sources return ``[]`` (today's non-counting behaviour —
    no regression). Provider errors degrade to ``[]``."""
    source = (getattr(binding, "source", "") or "").strip()
    provider = _REGISTRY.get(source)
    if provider is None and "." in source:
        provider = _REGISTRY.get(f"{source.split('.', 1)[0]}.*")
    if provider is None:
        return []
    try:
        return list(provider(binding, ctx) or [])
    except Exception:  # pragma: no cover - defensive; a bad source must never crash a report
        logger.warning("fact-source provider %r failed", source, exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# Providers implemented now
# --------------------------------------------------------------------------- #


@register_fact_source("extraction")
def _extraction_facts(binding: Any, ctx: FactContext) -> list[dict]:
    """Source-document extracted facts. Optional ``selector`` narrows by an
    ``object_class_iri`` substring (e.g. ``"DrugProduct"``)."""
    selector = getattr(binding, "selector", None)
    rows: list[dict] = []
    for e in ctx.edges:
        obj_class = e.get("object_class_iri", "") or ""
        if selector and selector not in obj_class:
            continue
        source_ref = e.get("source_ref")
        text = e.get("object_text") or ""
        if text:
            rows.append({"label": _short(obj_class), "value": str(text), "source_ref": source_ref})
        for dp in e.get("object_data_properties") or []:
            label, value = dp.get("label"), dp.get("value")
            if label and value not in (None, ""):
                rows.append({"label": label, "value": str(value), "source_ref": source_ref})
    return rows


@register_fact_source("rule_results")
def _rule_result_facts(binding: Any, ctx: FactContext) -> list[dict]:
    """Deterministic declarative-rule (E12/E13) results — the *deterministic fact source*.
    READ-ONLY: quoted by the LLM, never recomputed here (FR-009)."""
    rows: list[dict] = []
    for row in ctx.assessment_rows or []:
        hazid = getattr(row, "hazid", "") or ""
        pre = getattr(row, "pre_control_level", "")
        post = getattr(row, "post_control_level", "")
        status = getattr(row, "status", "")
        rows.append({
            "label": hazid or getattr(row, "contributing_factors", ""),
            "value": f"初始风险 {pre} → 残余风险 {post}（{status}）",
            "source_ref": "rule_results",
        })
    return rows


# --------------------------------------------------------------------------- #
# Deferred providers (stubs — no rows, so the validator's non-counting MANUAL
# coverage position stands; upgraded when the underlying read API settles)
# --------------------------------------------------------------------------- #


@register_fact_source("abox.*")
def _abox_stub(binding: Any, ctx: FactContext) -> list[dict]:
    """结构化映射 A-Box 事实（组织/人员/权限）— deferred until the A-Box individual
    read API settles."""
    logger.debug("A-Box fact source %r not yet implemented", getattr(binding, "source", ""))
    return []


@register_fact_source("external.*")
def _external_stub(binding: Any, ctx: FactContext) -> list[dict]:
    """外部结构化数据源 — deferred (intranet sources are in-posture per Principle VI)."""
    logger.debug("external fact source %r not yet implemented", getattr(binding, "source", ""))
    return []
