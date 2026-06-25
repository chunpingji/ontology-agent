"""DEPRECATED compatibility shim — conflict resolution externalized (spec 006, T043).

The hard-coded safety-first / max-severity ``if``-branches that lived here are now
the declarative **E13 conflict policy** layer:

  • generic interpreter ........ ``app/services/reasoning/policy.py``
                                  (``resolve_dedication_conflict`` / ``resolve_risk_level``
                                  consume a ``defaults.ConflictPolicy``)
  • in-code single source ...... ``app/services/reasoning/defaults.py``
                                  (``DEFAULT_CONFLICT_POLICIES``)
  • DB seed .................... ``app/services/reasoning/seed_declarative.py``
  • editable artifacts ......... E13 冲突策略 (``ontology_conflict_policy``),
                                  维护入口 ``/api/ontology/conflict-policies`` + 工作台「声明式规则」

``engine.py`` no longer imports this module — it drives ``policy.py`` over the
active/published policies (T034). These two functions are retained **only** as
backward-compatible wrappers; they delegate to ``policy`` with the default policy,
so any legacy caller observes byte-for-byte identical behaviour (golden-master
parity, FR-012). Prefer ``policy.resolve_*`` for new code.
"""

from __future__ import annotations

from app.services.reasoning import policy


def resolve_dedication_conflict(conclusions: list[dict]) -> bool:
    """DEPRECATED: use ``policy.resolve_dedication_conflict`` with an E13 policy."""
    return policy.resolve_dedication_conflict(conclusions, None)


def resolve_risk_level(levels: list[str]) -> str:
    """DEPRECATED: use ``policy.resolve_risk_level`` with an E13 policy."""
    return policy.resolve_risk_level(levels, None)
