"""Deterministic ``{{占位符}}`` substitution — shared, LLM-free.

The risk-assessment matrix (QS-A-020F05) and the LLM narrative generator both fill
``{{label}}`` tokens with resolved fact values. This module owns one character scanner plus
a single-pass substitution so neither module reaches into the other's private
helper and both stay byte-identical. No LLM, no network — safe for the
deterministic, auditable matrix (FR-009 forbids the LLM from altering it).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

# A ``{{ 药物名称/代号 }}`` token; the inner group is the trimmed label. Imported by
# ``narrative_generator`` so the two report consumers share one scanner / one behavior.
def placeholder_spans(text):
    position = 0
    while position < len(text):
        start = text.find("{{", position)
        if start < 0:
            break
        end = text.find("}}", start + 2)
        if end < 0:
            break
        label = text[start + 2:end]
        if label and "{" not in label and "}" not in label:
            yield start, end + 2, label.strip()
            position = end + 2
        else:
            position = start + 1


class PlaceholderScanner:
    def findall(self, text):
        return [label for _, _, label in placeholder_spans(text)]


# Historical import name only; there is no regex execution behind this adapter.
PLACEHOLDER_RE = PlaceholderScanner()


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

    result, position = [], 0
    for start, end, label in placeholder_spans(text):
        result.append(text[position:start])
        value = mapping.get(label)
        result.append(missing(label) if value is None or value == "" else str(value))
        position = end
    result.append(text[position:])
    return "".join(result)
