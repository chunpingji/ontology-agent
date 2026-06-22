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
