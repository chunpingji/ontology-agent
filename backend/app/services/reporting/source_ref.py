"""Render an edge/candidate ``source_ref`` to a human-readable string.

An extraction edge's ``source_ref`` is legitimately **dual-shaped** (mirrors the
frontend ``RelationSourceRef = string | StructuredRelationSourceRef``):

- **Legacy string** — the hand-written ``relation_extractor`` strategies emit a
  plain locator string (``"§ 简介"``, ``"表 设备需求"``).
- **Structured dict** — the 017 ontology-guided Document Profile emits a
  structured locator (``{"kind": "table_cell", "table": 3, "row": 1,
  "column": 2, "header": "设备编号"}`` / ``{"kind": "section", "section": …}``)
  so the annotation UI can anchor the highlight back to the exact cell/section.

The dict shape is intentional and consumed by the frontend (see
``frontend/src/lib/relation-source-ref.ts``), so it must NOT be flattened at the
edge level. But every **report-time** consumer that treats ``source_ref`` as a
string (``re.search`` for workshop codes, ``f"…〔{src}〕"`` fact lines, coverage
``source_ref`` display) breaks or renders garbage when handed the dict —
concretely, ``re.search(dict)`` raises ``expected string or bytes-like object,
got 'dict'`` and fails the whole report. This helper is the single coercion seam
those consumers call; its output mirrors the frontend label byte-for-byte
(1-based table/row/column, ``§`` section, ``·`` join) so web and DOCX agree.
"""

from __future__ import annotations

import json
from typing import Any


def _numeric_part(ref: dict, key: str, label: str) -> str | None:
    """0-based ``table``/``row``/``column`` → 1-based ``"表 4"`` (mirrors the UI)."""
    value = ref.get(key)
    if isinstance(value, bool):  # bool is an int subclass; never a positional index
        return None
    if isinstance(value, int):
        return f"{label} {value + 1}"
    return None


def format_source_ref(ref: Any) -> str:
    """Human-readable locator for a legacy string or structured dict ``source_ref``.

    Empty/``None`` → ``""``. Mirrors ``formatRelationSourceRef`` so the DOCX report
    and the web annotation panel show identical source labels.
    """
    if ref is None:
        return ""
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict):
        return str(ref)
    if not ref:
        return ""  # empty dict carries no locator → blank, not the literal "{}"

    parts: list[str] = []
    section = ref.get("section")
    if isinstance(section, str) and section.strip():
        parts.append(f"§ {section.strip()}")

    for key, label in (("table", "表"), ("row", "行"), ("column", "列")):
        part = _numeric_part(ref, key, label)
        if part:
            parts.append(part)

    # Truthy fall-through (``or``): an empty ``parameter`` yields to a meaningful
    # ``key``/``header`` rather than rendering blank. The frontend formatter uses
    # the same ``||`` precedence (see relation-source-ref.ts) so web and DOCX agree.
    detail = ref.get("parameter") or ref.get("key") or ref.get("header")
    if isinstance(detail, str) and detail.strip():
        parts.append(detail.strip())

    # External-system locator (mock A-Box source, e.g. {"system","entity","record"}):
    # the fallback BASE only when no structural locator was found — mirrors the
    # frontend's parts-empty branch.
    if not parts:
        parts.extend(
            str(ref[k]).strip()
            for k in ("system", "entity", "record")
            if isinstance(ref.get(k), str) and ref[k].strip()
        )

    # Equipment enrichment writes its archive/workshop provenance onto the dict
    # under ``enrichment`` (see equipment_source._apply_equipment_fact) so the
    # structured anchor survives. It AUGMENTS the base label (appended last), never
    # preempts it — so an external-system ref keeps system/entity/record too.
    enrichment = ref.get("enrichment")
    if isinstance(enrichment, str) and enrichment.strip():
        parts.append(enrichment.strip())

    if parts:
        return " · ".join(parts)

    try:
        return json.dumps(ref, ensure_ascii=False)
    except (TypeError, ValueError):
        return "来源位置"
