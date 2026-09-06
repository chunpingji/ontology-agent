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
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.template_structure_builder import TemplateOrigin
from app.services.fact_selector import PredicateStep

logger = logging.getLogger(__name__)

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


class LLMExtractionSource(BaseModel):
    """Bind a slot to a value extracted by local LLM gap filling."""

    kind: Literal["llm_extraction"] = "llm_extraction"
    object_class_iri: str
    data_property_iri: str
    label: str


class SemanticSource(BaseModel):
    """SEMANTIC SLOT (语义化插槽, 016+).

    At report time the local LLM *synthesizes* this slot's text by fusing
    (1) a writing ``prompt`` and (2) the parent Section's *associated ontology* —
    the source-document ontology relationship graph (``Section.coverage``, 016) plus
    fact-source facts. It stores **no binding data of its own**: it is a PROJECTION of
    ``Section.prompt`` (015) + ``Section.coverage`` (016), not a copy.

    ``prompt`` ``None`` ⇒ inherit the parent ``Section.prompt``.
    ``coverage_refs`` are :func:`coverage_key` values selecting *which* of the section's
    ``coverage`` bindings feed this slot; ``[]`` ⇒ project the whole section's coverage.

    FR-009 invariant: a semantic slot is a downstream, read-only consumer — it never
    influences the deterministic risk evaluation. The legacy typed sources
    (``extraction``/``rule``/``manual``/``constant``/``llm_extraction``) remain valid
    union members for backward compatibility; new templates author semantic slots.
    """

    kind: Literal["semantic"] = "semantic"
    prompt: str | None = None
    coverage_refs: list[str] = Field(default_factory=list)


class ReportMetadataSource(BaseModel):
    kind: Literal["report_metadata"] = "report_metadata"
    field: Literal["source_filename", "input_class_iri", "snapshot_id", "template_version"]


class SnapshotSource(BaseModel):
    """Exact per-instance projection; labels/prompts never select facts."""

    kind: Literal["snapshot"] = "snapshot"
    root_class_iri: str
    predicate_path: list[PredicateStep] = Field(min_length=1, max_length=16)
    range_class_iri: str
    data_property_iris: list[str] = Field(default_factory=list)
    text: bool = False

    @model_validator(mode="after")
    def has_projection(self):
        if not self.text and not self.data_property_iris:
            raise ValueError("snapshot source requires text or explicit property IRIs")
        return self


SlotSource = Annotated[
    Union[
        ExtractionSource,
        RuleSource,
        ManualSource,
        ConstantSource,
        LLMExtractionSource,
        SemanticSource,
        SnapshotSource,
        ReportMetadataSource,
    ],
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------- #
# Coverage bindings — section-level ontology coverage (016, AST-1)
# --------------------------------------------------------------------------- #
#
# Raises the authoring unit from *individual slots* to *section-level coverage
# declarations* expressed in the ontology's own vocabulary. One
# ``OntologyRelationBinding`` == one edge of ``get_relation_schema(doc_class_iri)``
# (document entity type ─predicate→ target type). It is *physically incapable* of
# naming a concrete individual — all three IRIs are class/predicate types (FR-003).
# The validator expands each declared relationship into the target type's property
# checklist at validate-time; nothing here compiles to a persisted ``ExtractionSource``
# (D2). Coverage nests inside the existing ``schema_json`` JSON column — no migration.


class OntologyRelationBinding(BaseModel):
    """A section's declared coverage of one ontology relationship (FR-001).

    The no-omission contract at *relationship granularity*: a ``required`` relationship
    entirely absent from a source graph is a true omission (FR-005); blank properties
    under a *present* relationship are informational unless promoted via
    ``required_properties`` (FR-006/FR-007).
    """

    kind: Literal["ontology_relation"] = "ontology_relation"
    doc_class_iri: str  # document entity TYPE (domain) — class IRI only (FR-003)
    predicate_iri: str  # the relationship — a real edge of get_relation_schema
    range_class_iri: str  # target entity TYPE (range) — class IRI only (FR-003)
    required: bool = True  # FR-005a / clarification Q1 — required by default
    required_properties: list[str] = Field(default_factory=list)  # FR-007 per-section promotion
    label: str | None = None  # narrative display only
    predicate_path: list[PredicateStep] = Field(default_factory=list)
    quantifier: Literal["exists", "all"] = "exists"
    min_count: int = Field(default=1, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    subject_instance_iris: list[str] = Field(default_factory=list)
    subject_root_class_iri: str | None = None
    subject_path: list[PredicateStep] = Field(default_factory=list, max_length=16)
    object_instance_iris: list[str] | None = None
    applicable_at: str | None = None

    @model_validator(mode="after")
    def valid_cardinality(self):
        if bool(self.subject_root_class_iri) != bool(self.subject_path):
            raise ValueError("subject root class and path must be declared together")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("max_count cannot be less than min_count")
        if len(self.predicate_path) > 16:
            raise ValueError("predicate path exceeds traversal budget")
        return self


class FactSourceBinding(BaseModel):
    """Placeholder for a future non-graph fact-source binding (FR-014, D11).

    Modeled now to prove the discriminated-union shape extends to a second ``kind``;
    the fact-source data provider is out of scope (positions remain human-filled until
    it lands — the validator emits a single non-counting ``MANUAL`` placeholder).
    """

    kind: Literal["fact_source"] = "fact_source"
    source: str
    selector: str | None = None
    label: str | None = None


CoverageBinding = Annotated[
    Union[OntologyRelationBinding, FactSourceBinding],
    Field(discriminator="kind"),
]


def _short(iri: str) -> str:
    """Local-name of an IRI (after the last ``#`` or ``/``). Matches the
    identical helper in ``coverage_validator`` — kept here so ``coverage_key``
    has no import cycle (the validator imports *from* this module)."""
    for sep in ("#", "/"):
        idx = iri.rfind(sep)
        if idx >= 0:
            return iri[idx + 1 :]
    return iri


def coverage_key(binding: "CoverageBinding") -> str:
    """The synthetic slot-id the coverage validator emits for a ``section.coverage``
    binding. The single source of truth for that string so a semantic slot's
    ``coverage_refs`` (which project a section's coverage) key on the exact same
    value the manifest carries. Byte-identical to the strings previously inlined in
    ``coverage_validator._resolve_coverage_binding``."""
    if getattr(binding, "kind", None) == "fact_source":
        source = getattr(binding, "source", "") or ""
        return f"coverage.fact_source.{_short(source)}"
    return f"coverage.{_short(binding.predicate_iri)}__{_short(binding.range_class_iri)}"


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


class Slot(BaseModel):
    slot_id: str
    origin: TemplateOrigin | None = None
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
    origin: TemplateOrigin | None = None
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
    origin: TemplateOrigin | None = None
    title: str
    groups: list[Group]
    # 015: per-section 行文 Prompt. When set, the generation engine calls the local
    # LLM with this prompt (fusing the section's slot values) to produce the
    # section's narrative prose. Persisted inside schema_json; additive/optional.
    prompt: str | None = None
    # 016: per-section ontology coverage declarations (FR-001/FR-009). Mirrors the
    # additive-optional ``prompt`` field above — a DECLARED field (the codebase default
    # is ``extra='ignore'``, which would silently drop an un-declared key on
    # model_validate). Defaults to ``[]`` so legacy templates round-trip identically
    # (C1/C2). Global omission de-dup is a validate-time concern (D1/D3), so no
    # cross-section uniqueness validator is added here (C7 / FR-011a).
    coverage: list[CoverageBinding] = Field(default_factory=list)


class ReportTemplate(BaseModel):
    template_id: str
    doc_no: str = "QS-A-020F05"
    revision: str = "00"
    sections: list[Section]
    diagnostics: list[str] = Field(default_factory=list)

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


# --------------------------------------------------------------------------- #
# DB-aware template resolution (012)
# --------------------------------------------------------------------------- #


def _latest_version_key(row) -> tuple:
    """Sort key selecting the LATEST authored version within a template family.

    A template family shares one ``iri_pattern`` (e.g. 风险评估文档 v1…v4); report
    generation must use the version the author is currently editing — the newest —
    not an arbitrary row. ``version`` is a free string (e.g. ``"v4"``); we parse its
    trailing integer (``"v10" > "v9"``, unlike a lexical compare), then break ties by
    ``created_at`` so the result is deterministic even for equal/blank versions."""
    digits, current = [], ""
    for char in getattr(row, "version", "") or "":
        if char.isdecimal():
            current += char
        elif current:
            digits.append(current)
            current = ""
    if current:
        digits.append(current)
    num = int(digits[-1]) if digits else 0
    return (num, row.created_at)


def resolve_template(
    doc_class_iri: str | None,
    db: "Session",
) -> tuple[ReportTemplate, str, uuid.UUID | None]:
    """Three-tier fallback template resolution.

    Returns ``(template, match_source, template_db_id)`` where *match_source*
    is one of ``"iri_pattern"``, ``"default"``, or ``"fallback"``.

    1. **iri_pattern** — a non-archived ``AstTemplate`` whose ``iri_pattern``
       appears in *doc_class_iri*; the **longest** (most specific) pattern wins, and
       within that most-specific family the **latest authored version** wins (the
       version the frontend is editing). This is the functional resolution key
       (015; replaces DocumentTypeMapping).
    2. **default** — the ``AstTemplate`` row with ``is_default=True``.
    3. **fallback** — the filesystem JSON via ``load_default_template()``.
    """
    from app.models.extraction import AstTemplate

    # Tier 1: per-template iri_pattern substring match. Most-specific pattern
    # (longest) first, then latest version within that family. ``order_by`` makes
    # the candidate stream deterministic (defense in depth); the explicit version
    # key is what actually decides ties — a bare ``max`` over equal-length patterns
    # would otherwise return whichever row the engine happened to yield first.
    if doc_class_iri:
        candidates = (
            db.query(AstTemplate)
            .filter(AstTemplate.iri_pattern.isnot(None), AstTemplate.status != "archived")
            .order_by(AstTemplate.created_at.desc())
            .all()
        )
        matches = [t for t in candidates if t.iri_pattern and t.iri_pattern in doc_class_iri]
        if matches:
            longest = max(len(t.iri_pattern or "") for t in matches)
            specific = [t for t in matches if len(t.iri_pattern or "") == longest]
            best = max(specific, key=_latest_version_key)
            tpl = ReportTemplate.model_validate(best.schema_json)
            logger.debug(
                "resolve_template: iri_pattern %s → %s (%s)",
                best.iri_pattern, best.name, getattr(best, "version", "?"),
            )
            return tpl, "iri_pattern", best.id

    # Tier 2: DB default template
    default_row = db.query(AstTemplate).filter(AstTemplate.is_default.is_(True)).first()
    if default_row is not None:
        tpl = ReportTemplate.model_validate(default_row.schema_json)
        logger.debug("resolve_template: default → %s", default_row.name)
        return tpl, "default", default_row.id

    # Tier 3: filesystem fallback
    logger.debug("resolve_template: fallback → filesystem")
    return load_default_template(), "fallback", None
