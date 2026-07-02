"""Material coverage validator for risk assessment reports (010, AST-2).

Traverses a declarative :class:`ReportTemplate`, resolves each slot against the
actual extraction edges and decision-rule evaluations, and produces a
:class:`CoverageManifest`. This is the proof of "无遗漏" (no-omission): every
required slot in the template is accounted for as either filled / inferred or
**explicitly flagged missing** — there is no silent third state.

Coverage status per slot
-------------------------
- ``filled``           : a concrete value was found (extraction / constant), or a
                         rule's antecedent is determinately FALSE (low risk).
- ``inferred``         : a rule's antecedent evaluated TRUE — its row is meaningful.
- ``manual``           : a human-filled slot (team / review / approvals).
- ``blank_optional``   : an optional slot with no value — left blank, no flag.
- ``missing_required`` : a REQUIRED slot whose value/evaluation is absent
                         (extraction miss, or rule antecedent UNKNOWN). This is the
                         G1 signal: distinguishes "确认低风险" from "无数据".

Rule slots intentionally expand per-rule (one HazID dimension each), because a
missing dimension is a real omission. Equipment columns are evaluated per-column
across all equipment rows (homogeneous rows — a missing cell is not structural).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.reasoning.interpreter import FALSE, TRUE, evaluate

from app.services.reporting.ast_template import (
    ExtractionSource,
    Group,
    ReportTemplate,
    Slot,
)

# Status constants
FILLED = "filled"
INFERRED = "inferred"
MANUAL = "manual"
BLANK_OPTIONAL = "blank_optional"
MISSING_REQUIRED = "missing_required"
DISMISSED = "dismissed"


def _short(iri: str) -> str:
    for sep in ("#", "/"):
        idx = iri.rfind(sep)
        if idx >= 0:
            return iri[idx + 1 :]
    return iri


@dataclass
class SlotCoverage:
    slot_id: str  # may be an instance id, e.g. "assessment.pre_control_level[R-RA2]"
    label: str
    status: str
    source_kind: str  # extraction | rule | manual | constant
    value: str | None = None
    source_ref: str | None = None
    rule_key: str | None = None
    hazid: str | None = None
    note: str | None = None
    source_span: str | None = None
    is_llm_sourced: bool = False


@dataclass
class CoverageManifest:
    template_id: str
    slots: list[SlotCoverage] = field(default_factory=list)

    @property
    def total_slots(self) -> int:
        return len(self.slots)

    @property
    def _counts(self) -> Counter:
        return Counter(s.status for s in self.slots)

    @property
    def filled(self) -> int:
        return self._counts[FILLED]

    @property
    def inferred(self) -> int:
        return self._counts[INFERRED]

    @property
    def manual(self) -> int:
        return self._counts[MANUAL]

    @property
    def blank_optional(self) -> int:
        return self._counts[BLANK_OPTIONAL]

    @property
    def missing_required(self) -> int:
        return self._counts[MISSING_REQUIRED]

    @property
    def dismissed(self) -> int:
        return self._counts[DISMISSED]

    @property
    def missing_required_slots(self) -> list[SlotCoverage]:
        return [s for s in self.slots if s.status == MISSING_REQUIRED]

    @property
    def dismissed_slots(self) -> list[SlotCoverage]:
        return [s for s in self.slots if s.status == DISMISSED]

    @property
    def has_omissions(self) -> bool:
        return self.missing_required > 0

    def summary(self) -> dict[str, Any]:
        """Compact summary for audit ``details`` (FR-012)."""
        return {
            "template_id": self.template_id,
            "total_slots": self.total_slots,
            "filled": self.filled,
            "inferred": self.inferred,
            "manual": self.manual,
            "blank_optional": self.blank_optional,
            "missing_required": self.missing_required,
            "dismissed": self.dismissed,
            "missing_slot_ids": [s.slot_id for s in self.missing_required_slots],
        }

    def to_dict(self) -> dict[str, Any]:
        """Full manifest for persistence in ``GeneratedReport.rules_summary``."""
        return {**self.summary(), "slots": [asdict(s) for s in self.slots]}


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #


def _matching_edges(edges: Sequence[dict], class_contains: str) -> list[dict]:
    return [e for e in edges if class_contains in e.get("object_class_iri", "")]


def _resolve_extraction(slot: Slot, edges: Sequence[dict]) -> SlotCoverage:
    src: ExtractionSource = slot.source  # type: ignore[assignment]
    value: str | None = None
    source_ref: str | None = None

    if src.relation:
        for e in edges:
            if (
                _short(e.get("predicate_iri", "")) == src.relation
                and src.object_class_iri_contains in e.get("object_class_iri", "")
            ):
                value = "存在"
                source_ref = e.get("source_ref")
                break
    else:
        matching = _matching_edges(edges, src.object_class_iri_contains)
        if src.text:
            for e in matching:
                t = e.get("object_text")
                if t:
                    value, source_ref = str(t), e.get("source_ref")
                    break
        else:  # data_property or label
            for e in matching:
                for dp in e.get("object_data_properties") or []:
                    val = dp.get("value")
                    if val in (None, ""):
                        continue
                    hit = (
                        src.data_property
                        and dp.get("iri")
                        and _short(dp["iri"]) == src.data_property
                    ) or (src.label and dp.get("label") == src.label)
                    if hit:
                        value, source_ref = str(val), e.get("source_ref")
                        break
                if value is not None:
                    break

    if value is not None:
        status = FILLED
    else:
        status = MISSING_REQUIRED if slot.required else BLANK_OPTIONAL
    return SlotCoverage(
        slot_id=slot.slot_id,
        label=slot.label,
        status=status,
        source_kind="extraction",
        value=value,
        source_ref=source_ref,
    )


def _resolve_value_slot(slot: Slot, edges: Sequence[dict]) -> SlotCoverage:
    """Resolve a singular (non-table) slot by its source kind."""
    kind = slot.source.kind
    if kind == "extraction":
        return _resolve_extraction(slot, edges)
    if kind == "constant":
        return SlotCoverage(
            slot_id=slot.slot_id, label=slot.label, status=FILLED,
            source_kind="constant", value=slot.source.value,  # type: ignore[attr-defined]
        )
    # manual
    return SlotCoverage(
        slot_id=slot.slot_id, label=slot.label, status=MANUAL, source_kind="manual",
    )


def _resolve_equipment(group: Group, edges: Sequence[dict]) -> list[SlotCoverage]:
    records: list[SlotCoverage] = []
    for slot in group.slots:
        src: ExtractionSource = slot.source  # type: ignore[assignment]
        matching = _matching_edges(edges, src.object_class_iri_contains)
        if src.text:  # the id column → "does any equipment exist?"
            count = sum(1 for e in matching if e.get("object_text"))
            if count:
                status, value = FILLED, f"{count} 台设备"
            else:
                status, value = (
                    (MISSING_REQUIRED if slot.required else BLANK_OPTIONAL),
                    None,
                )
        else:  # an optional column → filled if any row populates it
            has = any(
                dp.get("label") == src.label and dp.get("value") not in (None, "")
                for e in matching
                for dp in (e.get("object_data_properties") or [])
            )
            if has:
                status, value = FILLED, None
            else:
                status, value = (
                    (MISSING_REQUIRED if slot.required else BLANK_OPTIONAL),
                    None,
                )
        records.append(SlotCoverage(
            slot_id=slot.slot_id, label=slot.label, status=status,
            source_kind="extraction", value=value,
        ))
    return records


def _resolve_assessment(
    group: Group, rules: Sequence[Any], facts: Any
) -> list[SlotCoverage]:
    if not rules:
        return [SlotCoverage(
            slot_id=f"{group.group_id}.__no_rules__",
            label="风险评估维度",
            status=MISSING_REQUIRED,
            source_kind="rule",
            note="未加载任何 risk_assessment 规则，全部风险维度缺失",
        )]

    records: list[SlotCoverage] = []
    for rule in rules:
        result = evaluate(rule.antecedent, facts)
        consequent = rule.consequent or {}
        hazid = consequent.get("category", "")
        rule_key = getattr(rule, "rule_key", None)
        for slot in group.slots:
            if result is TRUE:
                status = INFERRED
            elif result is FALSE:
                status = FILLED
            else:  # UNKNOWN — dimension can't be evaluated (G1)
                status = MISSING_REQUIRED if slot.required else BLANK_OPTIONAL
            records.append(SlotCoverage(
                slot_id=f"{slot.slot_id}[{rule_key}]" if rule_key else slot.slot_id,
                label=f"{hazid}·{slot.label}" if hazid else slot.label,
                status=status,
                source_kind="rule",
                rule_key=rule_key,
                hazid=hazid,
            ))
    return records


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def validate_coverage(
    template: ReportTemplate,
    edges: Sequence[dict],
    rules: Sequence[Any],
    facts: Any | None = None,
    dismissed_slot_ids: set[str] | None = None,
) -> CoverageManifest:
    """Produce a :class:`CoverageManifest` for ``edges`` + ``rules`` against ``template``.

    ``facts`` may be supplied to reuse an already-built Facts view; otherwise it is
    derived from ``edges``.

    ``dismissed_slot_ids``, when provided, flips ``missing_required`` slots whose
    base slot_id is in the set to ``dismissed`` status (011 FR-API-006).
    """
    if facts is None:
        from app.services.reasoning.fact_bridge import edges_to_facts

        facts = edges_to_facts(list(edges))

    manifest = CoverageManifest(template_id=template.template_id)
    for section in template.sections:
        for group in section.groups:
            if group.kind == "equipment_table":
                manifest.slots.extend(_resolve_equipment(group, edges))
            elif group.kind == "assessment_table":
                manifest.slots.extend(_resolve_assessment(group, rules, facts))
            else:  # fields | manual
                for slot in group.slots:
                    manifest.slots.append(_resolve_value_slot(slot, edges))

    if dismissed_slot_ids:
        for sc in manifest.slots:
            if sc.status == MISSING_REQUIRED:
                base_id = sc.slot_id.split("[")[0]
                if base_id in dismissed_slot_ids or sc.slot_id in dismissed_slot_ids:
                    sc.status = DISMISSED

    return manifest
