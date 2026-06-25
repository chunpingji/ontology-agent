"""DEPRECATED compatibility shim — drug-classification rule knowledge externalized
(spec 006, T043; rule group ``drug_classification``, R-DC1~4 + US2 gap-closers).

The hard-coded ``ALL_RULES`` production functions that lived here are superseded by
the declarative rule layer:

  • in-code single source ...... ``app/services/reasoning/defaults.py``
                                  (``DEFAULT_CLASSIFICATION_CRITERIA`` → R-DC1~4,
                                  ``US2_CLASSIFICATION_CRITERIA`` → the inferable upgrades)
  • generic interpreter ........ ``app/services/reasoning/interpreter.py``
                                  (evaluates each criterion's restricted pattern AST)
  • DB seed .................... ``app/services/reasoning/seed_declarative.py``
  • editable artifacts ......... E11 分类判据 (``ontology_classification_criterion``),
                                  维护入口 ``/api/ontology/classification-criteria`` + 工作台「声明式规则」

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


ALL_RULES: list = []  # migrated to defaults.DEFAULT_CLASSIFICATION_CRITERIA / US2_*
