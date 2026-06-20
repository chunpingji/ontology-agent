"""Conflict resolution: safety-first meta-rule per CFDI §13.4.

When multiple rules produce contradictory conclusions about equipment dedication,
the most restrictive conclusion wins (requires_dedication=true overrides false).
"""

from __future__ import annotations


def resolve_dedication_conflict(conclusions: list[dict]) -> bool:
    """Any rule requiring dedication -> final answer is True (safety-first)."""
    for c in conclusions:
        if c.get("requires_dedication") is True:
            return True
    return any(c.get("requires_dedication") is False for c in conclusions) and False


def resolve_risk_level(levels: list[str]) -> str:
    """Return the highest risk level seen."""
    priority = {"HighRisk": 3, "MediumRisk": 2, "LowRisk": 1}
    if not levels:
        return "LowRisk"
    return max(levels, key=lambda l: priority.get(l, 0))
