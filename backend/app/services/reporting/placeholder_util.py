"""Deterministic ``{{占位符}}`` substitution — shared, LLM-free.

The risk-assessment matrix (QS-A-020F05) and the LLM narrative generator both fill
``{{label}}`` tokens with resolved fact values. This module owns the ONE regex plus
a single-pass substitution so neither module reaches into the other's private
helper and both stay byte-identical. No LLM, no network — safe for the
deterministic, auditable matrix (FR-009 forbids the LLM from altering it).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

# A ``{{ 药物名称/代号 }}`` token; the inner group is the trimmed label. Imported by
# ``narrative_generator`` so the two report consumers share one regex / one behavior.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def substitute(
    text: str,
    mapping: Mapping[str, str | None],
    *,
    missing: Callable[[str], str],
) -> str:
    """Replace every ``{{label}}`` in ``text`` with ``mapping[label]``.

    **Single pass**: substituted values are NOT re-scanned for placeholders, so a
    fact value that itself contains ``{{...}}`` is emitted literally (no recursive
    expansion, no injection). A label absent from ``mapping`` or mapped to
    ``None``/``""`` is rendered via ``missing(label)`` (e.g. an explicit
    ``【待补充：label】`` sentinel) rather than silently blanked — missing data must
    stay visible on a controlled matrix, never hidden behind a vague generic word.
    """
    if not text or "{{" not in text:
        return text

    def _repl(m: re.Match) -> str:
        label = m.group(1).strip()
        value = mapping.get(label)
        if value is None or value == "":
            return missing(label)
        return str(value)

    return PLACEHOLDER_RE.sub(_repl, text)
