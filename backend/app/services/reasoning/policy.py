"""Conflict-resolution policy interpreter (E13 → generic aggregation).

Externalises the strategy constants (priority lattice, override direction) that
were previously hard-coded in `conflict_resolver.py` if-branches. Populated in
User Story 3 (T033): the engine passes the published `ConflictPolicy` for each
dimension; absence falls back to the legacy default so behaviour is byte-for-byte
preserved (golden-master parity, FR-012).

Both resolvers are *pure* and accept either a `defaults.ConflictPolicy` dataclass
or any object exposing `.strategy` / `.override_direction` / `.priority_lattice`
(e.g. an ORM row adapted by the store), or `None` for the legacy default.
"""

from __future__ import annotations

from typing import Any

from app.services.reasoning.defaults import RISK_PRIORITY_LATTICE


def resolve_dedication_conflict(conclusions: list[dict], policy: Any | None = None) -> bool:
    """Aggregate equipment-dedication conclusions under an E13 `dedication` policy.

    `safety_override` + `restrictive_wins` (the default, CFDI §13.4): any rule
    asserting ``requires_dedication=True`` wins → True; otherwise False. The
    `permissive_wins` direction inverts this (an explicit False vetoes).
    """
    direction = getattr(policy, "override_direction", None) or "restrictive_wins"
    if direction == "permissive_wins":
        if any(c.get("requires_dedication") is False for c in conclusions):
            return False
        return any(c.get("requires_dedication") is True for c in conclusions)
    # restrictive_wins (safety-first): most-restrictive conclusion prevails.
    return any(c.get("requires_dedication") is True for c in conclusions)


def resolve_risk_level(levels: list[str], policy: Any | None = None) -> str:
    """Aggregate contamination risk levels under an E13 `risk_level` policy.

    `max_severity`: the highest level on the `priority_lattice` wins. When no
    level fired, the lowest-severity lattice key is returned (default LowRisk).
    """
    lattice = getattr(policy, "priority_lattice", None) or RISK_PRIORITY_LATTICE
    if not levels:
        return min(lattice, key=lambda k: lattice[k])
    return max(levels, key=lambda level: lattice.get(level, 0))
