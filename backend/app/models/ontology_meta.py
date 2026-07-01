"""Editable T-Box metadata tables (E1–E10) — see specs data-model.md.

The metadata tables are the *editing source of truth* (draft state, R2); the
Owlready2 World and authoritative TTL are publish-time materialisations. Every
editable entity carries `version` (optimistic concurrency, R4) and a lifecycle
`status` (draft/in_review/published).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.db import Base
from app.models.types import GUID

# --- shared vocabularies (validated in schema/service layers) ---------------
STATUS_DRAFT = "draft"
STATUS_IN_REVIEW = "in_review"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"
EDITABLE_STATUSES = (STATUS_DRAFT, STATUS_IN_REVIEW, STATUS_PUBLISHED)

DATATYPES = ("string", "integer", "decimal", "boolean", "date", "dateTime", "anyURI")
RESTRICTION_KINDS = ("some", "only", "exactly", "min", "max", "disjoint", "equivalent")
PROPERTY_KINDS = ("object", "data")
MAPPING_TYPES = ("slpra_iri", "bfo", "source_field")
HEALTH_STATES = ("ok", "unmapped", "drift", "orphan")
CHANGE_KINDS = ("create", "update", "delete", "disable")
ROLE_NAMES = ("senior_analyst", "operator", "qa")

# --- declarative rule layer vocabularies (spec 006, data-model.md §A) --------
LOGIC_ROLES = ("defined", "production")
RULE_GROUPS = ("equipment_dedication", "scenario_identification", "contamination_risk", "risk_assessment")
CONFLICT_DIMENSIONS = ("dedication", "risk_level")
CONFLICT_STRATEGIES = ("safety_override", "max_severity")
OVERRIDE_DIRECTIONS = ("restrictive_wins", "permissive_wins")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(GUID(), ForeignKey("app_user.id"), nullable=True)

    @declared_attr
    def updated_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(GUID(), ForeignKey("app_user.id"), nullable=True)


class VersionMixin:
    """Optimistic-concurrency version + lifecycle status (R4)."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)


class NamedEntityMixin(VersionMixin, TimestampMixin):
    """Common columns for IRI-bearing editable entities (E1–E4)."""

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    slpra_iri: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    bfo_category: Mapped[str | None] = mapped_column(String(100))
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)


# --- E10 app_role / E9 app_user --------------------------------------------
class AppRole(Base):
    __tablename__ = "app_role"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("app_role.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    role: Mapped[AppRole | None] = relationship("AppRole", lazy="joined")


# --- E1 ontology_class ------------------------------------------------------
class OntologyClass(NamedEntityMixin, Base):
    __tablename__ = "ontology_class"

    parent_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    module: Mapped[str | None] = mapped_column(String(50), index=True)
    field_schema: Mapped[dict | None] = mapped_column(JSON)


# --- E2 ontology_link_type (object property / relation) ---------------------
class OntologyLinkType(NamedEntityMixin, Base):
    __tablename__ = "ontology_link_type"

    domain_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    range_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    inverse_link_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_link_type.id")
    )
    min_cardinality: Mapped[int | None] = mapped_column(Integer)
    max_cardinality: Mapped[int | None] = mapped_column(Integer)
    is_functional: Mapped[bool] = mapped_column(Boolean, default=False)
    is_symmetric: Mapped[bool] = mapped_column(Boolean, default=False)
    is_transitive: Mapped[bool] = mapped_column(Boolean, default=False)


# --- E3 ontology_data_property ----------------------------------------------
class OntologyDataProperty(NamedEntityMixin, Base):
    __tablename__ = "ontology_data_property"

    domain_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    datatype: Mapped[str] = mapped_column(String(20), nullable=False, default="string")
    unit: Mapped[str | None] = mapped_column(String(50))
    controlled_vocab: Mapped[dict | None] = mapped_column(JSON)


# --- E4 ontology_action (definition only, R10) ------------------------------
class OntologyAction(NamedEntityMixin, Base):
    __tablename__ = "ontology_action"

    actor_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    target_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    precondition: Mapped[dict | None] = mapped_column(JSON)
    postcondition: Mapped[dict | None] = mapped_column(JSON)
    params: Mapped[dict | None] = mapped_column(JSON)


# --- E5 ontology_restriction ------------------------------------------------
class OntologyRestriction(VersionMixin, TimestampMixin, Base):
    __tablename__ = "ontology_restriction"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    owner_class_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("ontology_class.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    on_property_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    property_kind: Mapped[str | None] = mapped_column(String(10))
    filler_class_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ontology_class.id")
    )
    cardinality: Mapped[int | None] = mapped_column(Integer)


# --- E6 ontology_class_mapping ----------------------------------------------
class OntologyClassMapping(VersionMixin, TimestampMixin, Base):
    __tablename__ = "ontology_class_mapping"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    class_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("ontology_class.id"), nullable=False
    )
    mapping_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(50))
    health: Mapped[str] = mapped_column(String(20), default="ok")


# --- E7 ontology_release ----------------------------------------------------
class OntologyRelease(Base):
    __tablename__ = "ontology_release"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    release_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)
    ttl_commit_sha: Mapped[str | None] = mapped_column(String(64))
    ttl_diff: Mapped[str | None] = mapped_column(Text)
    validation_report: Mapped[dict | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("app_user.id"))
    published_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    change_logs: Mapped[list["OntologyChangeLog"]] = relationship(
        "OntologyChangeLog", back_populates="release", cascade="all, delete-orphan"
    )


# --- E8 ontology_change_log -------------------------------------------------
class OntologyChangeLog(Base):
    __tablename__ = "ontology_change_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    release_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("ontology_release.id"), nullable=False
    )
    entity_table: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    change_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    release: Mapped[OntologyRelease] = relationship(
        "OntologyRelease", back_populates="change_logs"
    )

    __table_args__ = (
        UniqueConstraint("release_id", "entity_table", "entity_id", name="uq_changelog_entity"),
    )


# --- E11 ontology_classification_criterion (declarative defined criterion) ---
class OntologyClassificationCriterion(VersionMixin, TimestampMixin, Base):
    """Declarative "底层属性条件 → 风险分类" unit (spec 006, data-model.md §A/E11).

    Not IRI-bearing: a criterion is a class expression *hung off* its target
    class (projected as `target_class owl:equivalentClass _:c<id>`), so it
    reuses `VersionMixin`+`TimestampMixin` rather than `NamedEntityMixin`.
    E11 only ever stores `logic_role='defined'` (necessary-and-sufficient);
    production rules live in E12.
    """

    __tablename__ = "ontology_classification_criterion"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    criterion_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    target_class_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("ontology_class.id"), nullable=False
    )
    logic_role: Mapped[str] = mapped_column(String(20), nullable=False, default="defined")
    pattern: Mapped[dict] = mapped_column(JSON, nullable=False)
    regulation_ref: Mapped[str | None] = mapped_column(String(200))
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)


# --- E12 ontology_decision_rule (production rule R-ED/R-SC/R-CP) -------------
class OntologyDecisionRule(NamedEntityMixin, Base):
    """Production rule beyond DL-definable expressivity (data-model.md §A/E12).

    IRI-bearing managed subject `slpra:DecisionRule_<rule_key>`; the engine
    assembles by `rule_group`. `consequent` mirrors `RuleResult.conclusion`
    so the external `AssessmentResult` shape is preserved.
    """

    __tablename__ = "ontology_decision_rule"

    rule_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    rule_group: Mapped[str] = mapped_column(String(40), nullable=False)
    antecedent: Mapped[dict] = mapped_column(JSON, nullable=False)
    consequent: Mapped[dict] = mapped_column(JSON, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    regulation_ref: Mapped[str | None] = mapped_column(String(200))


# --- E13 ontology_conflict_policy (conflict resolution strategy) -------------
class OntologyConflictPolicy(NamedEntityMixin, Base):
    """Declarative conflict-resolution strategy (data-model.md §A/E13).

    Externalises `resolve_dedication_conflict` / `resolve_risk_level` as
    `slpra:ConflictPolicy_<dimension>` named resources: the priority lattice
    and override direction are data, not Python `if` branches.
    """

    __tablename__ = "ontology_conflict_policy"

    dimension: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    priority_lattice: Mapped[dict | None] = mapped_column(JSON)
    override_direction: Mapped[str | None] = mapped_column(String(20))
    regulation_ref: Mapped[str | None] = mapped_column(String(200))
