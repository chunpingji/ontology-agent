"""Declarative AST template for risk assessment reports (010, AST-1).

Defines the report's *slot inventory*: every fillable position, its binding to an
extraction edge or a decision rule, and its required/missing policy. The coverage
validator (AST-2) and the report generator (AST-3) traverse this template instead
of hardcoding report structure — so "可控无遗漏" (controllable & no-omission)
becomes a declarative, verifiable contract rather than implicit code behaviour.

Source kinds
------------
- ``extraction``: value comes from extraction edges (resolved via ``edges_to_facts``).
  Exactly one selector: ``text`` (edge.object_text), ``data_property``
  (Facts.data_values short-name), ``label`` (Facts.scalars label), or ``relation``
  (Facts.relations predicate presence — used to surface missing prerequisites).
- ``rule``: a field of a ``risk_assessment`` DecisionRule's evaluation (RiskRow field).
- ``manual``: filled by a human downstream (team / review / approvals).
- ``constant``: a literal value baked into the template.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- #
# Slot sources (discriminated union on ``kind``)
# --------------------------------------------------------------------------- #


class ExtractionSource(BaseModel):
    """Bind a slot to a value found in extraction edges."""

    kind: Literal["extraction"] = "extraction"
    object_class_iri_contains: str  # e.g. "DrugProduct", "Equipment"
    # exactly one selector below:
    text: bool = False  # edge.object_text of a matching edge
    data_property: str | None = None  # Facts.data_values[short_name]
    label: str | None = None  # Facts.scalars[label]
    relation: str | None = None  # Facts.relations[predicate] presence (prerequisite)

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> "ExtractionSource":
        chosen = [s for s in (self.text, self.data_property, self.label, self.relation) if s]
        if len(chosen) != 1:
            raise ValueError(
                "extraction source must set exactly one of "
                f"text/data_property/label/relation (got {len(chosen)})"
            )
        return self


class RuleSource(BaseModel):
    """Bind a slot to a field of a risk_assessment rule's evaluation (RiskRow)."""

    kind: Literal["rule"] = "rule"
    field: Literal[
        "hazid",
        "contributing_factors",
        "pre_control_level",
        "post_control_level",
        "control_measures",
        "traceability",
        "status",
    ]


class ManualSource(BaseModel):
    """Filled by a human downstream — coverage status ``manual``."""

    kind: Literal["manual"] = "manual"


class ConstantSource(BaseModel):
    """A literal value baked into the template."""

    kind: Literal["constant"] = "constant"
    value: str


SlotSource = Annotated[
    Union[ExtractionSource, RuleSource, ManualSource, ConstantSource],
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


class Slot(BaseModel):
    slot_id: str
    label: str
    source: SlotSource
    required: bool = False
    on_missing: Literal["annotate", "leave_blank"] = "annotate"
    missing_placeholder: str = "⚠ 待评估（数据缺失）"


class Repeat(BaseModel):
    """Marks a group whose slots are a per-instance template (expanded at runtime)."""

    by: Literal["workshop", "rule"]
    rule_group: str | None = None  # required when by == "rule"

    @model_validator(mode="after")
    def _rule_group_when_by_rule(self) -> "Repeat":
        if self.by == "rule" and not self.rule_group:
            raise ValueError("repeat by=rule requires rule_group")
        return self


GroupKind = Literal["fields", "equipment_table", "assessment_table", "manual"]


class Group(BaseModel):
    group_id: str
    title: str
    kind: GroupKind
    repeat: Repeat | None = None
    slots: list[Slot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _repeat_required_for_tables(self) -> "Group":
        if self.kind in ("equipment_table", "assessment_table") and self.repeat is None:
            raise ValueError(f"group {self.group_id!r} kind={self.kind} requires repeat")
        return self


class Section(BaseModel):
    section_id: str
    title: str
    groups: list[Group]


class ReportTemplate(BaseModel):
    template_id: str
    doc_no: str = "QS-A-020F05"
    revision: str = "00"
    sections: list[Section]

    def iter_slots(self) -> Iterator[tuple[Section, Group, Slot]]:
        """Yield every declared slot in document order with its parent context."""
        for section in self.sections:
            for group in section.groups:
                for slot in group.slots:
                    yield section, group, slot

    def required_slots(self) -> list[Slot]:
        """The minimal complete material set — every slot that must be filled or flagged."""
        return [slot for _, _, slot in self.iter_slots() if slot.required]

    @model_validator(mode="after")
    def _unique_slot_ids(self) -> "ReportTemplate":
        seen: set[str] = set()
        for _, _, slot in self.iter_slots():
            if slot.slot_id in seen:
                raise ValueError(f"duplicate slot_id: {slot.slot_id!r}")
            seen.add(slot.slot_id)
        return self


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE_ID = "QS-A-020F05@v1"
_TEMPLATE_FILES = {DEFAULT_TEMPLATE_ID: "qs_a_020f05.json"}


def load_template_file(path: str | Path) -> ReportTemplate:
    """Load and validate a template from an explicit JSON path."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReportTemplate.model_validate(data)


def load_template(template_id: str = DEFAULT_TEMPLATE_ID) -> ReportTemplate:
    """Load a registered template by id (raises KeyError for unknown ids)."""
    filename = _TEMPLATE_FILES.get(template_id)
    if filename is None:
        raise KeyError(f"unknown report template_id: {template_id!r}")
    return load_template_file(TEMPLATES_DIR / filename)


def load_default_template() -> ReportTemplate:
    """Load the default QS-A-020F05 risk assessment template."""
    return load_template(DEFAULT_TEMPLATE_ID)
