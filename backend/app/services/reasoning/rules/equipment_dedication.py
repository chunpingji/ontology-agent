"""DEPRECATED compatibility shim — equipment-dedication rule knowledge externalized
(spec 006, T043; rule group ``equipment_dedication``, R-ED1~6).

The hard-coded ``ALL_RULES`` production functions that lived here are superseded by
the declarative rule layer:

  • in-code single source ...... ``app/services/reasoning/defaults.py``
                                  (``DEFAULT_DECISION_RULES`` → R-ED1~6)
  • generic interpreter ........ ``app/services/reasoning/interpreter.py``
                                  (evaluates each rule's restricted antecedent AST)
  • DB seed .................... ``app/services/reasoning/seed_declarative.py``
  • editable artifacts ......... E12 决策规则 (``ontology_decision_rule``),
                                  维护入口 ``/api/ontology/decision-rules`` + 工作台「声明式规则」

``engine.py`` no longer imports this module (T034); ``/api/reasoning/rules`` now
lists from ``defaults`` (T043). Retained only as a backward-compatible stub —
``ALL_RULES`` is intentionally empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RuleResult:  # retained for legacy imports only; no longer produced at runtime
    rule_id: str
    fired: bool
    description: str
    inputs: dict[str, Any]
    conclusion: dict[str, Any]
    regulation_ref: str | None = None


ALL_RULES: list = []  # migrated to defaults.DEFAULT_DECISION_RULES (equipment_dedication)
