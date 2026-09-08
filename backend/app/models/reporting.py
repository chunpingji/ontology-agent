"""V2 report records. Immutable evidence objects are separate from mutable heads."""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import GUID


def now():
    return datetime.now(timezone.utc)


class FrozenPayload:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OntologySchemaSnapshot(FrozenPayload, Base):
    """Exact extraction/compilation schema; capturing content is not approval."""

    __tablename__ = "ontology_schema_snapshots"


class ContractRevision(FrozenPayload, Base):
    __tablename__ = "report_contract_revisions"
    __table_args__ = (
        UniqueConstraint("family_id", "revision_no", name="uq_report_contract_revision"),
    )
    family_id: Mapped[str] = mapped_column(String(500), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # Lifecycle is proven by append-only ContractDecision, not a mutable payload flag.


class ContractDecision(FrozenPayload, Base):
    __tablename__ = "report_contract_decisions"
    contract_id: Mapped[str] = mapped_column(
        ForeignKey("report_contract_revisions.id"),
        nullable=False,
        index=True,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("contract_id", "revision_no", name="uq_contract_decision"),)


class ContractRecord(FrozenPayload, Base):
    __tablename__ = "report_contract_records"
    contract_id: Mapped[str] = mapped_column(
        ForeignKey("report_contract_revisions.id"), nullable=False
    )
    record_key: Mapped[str] = mapped_column(String(500), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "record_key",
            "revision_no",
            name="uq_contract_record_revision",
        ),
    )


class OntologyDecisionRuleRevision(Base):
    """Rule identity points to exactly one immutable registered definition."""

    __tablename__ = "ontology_decision_rule_revisions"
    id: Mapped[str] = mapped_column(
        ForeignKey("report_contract_revisions.id"),
        primary_key=True,
    )
    rule_id: Mapped[object] = mapped_column(GUID(), ForeignKey("ontology_decision_rule.id"))
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("rule_id", "revision_no", name="uq_rule_revision"),)


class ContractRecordReview(FrozenPayload, Base):
    __tablename__ = "report_contract_record_reviews"
    record_id: Mapped[str] = mapped_column(ForeignKey("report_contract_records.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)


class ConditionDefinition(Base):
    __tablename__ = "condition_definitions"
    id: Mapped[str] = mapped_column(ForeignKey("report_contract_revisions.id"), primary_key=True)


class ConditionalAssertionBinding(Base):
    __tablename__ = "conditional_assertion_bindings"
    id: Mapped[str] = mapped_column(ForeignKey("report_contract_revisions.id"), primary_key=True)
    condition_id: Mapped[str] = mapped_column(
        ForeignKey("condition_definitions.id"), nullable=False
    )
    assertion_id: Mapped[str] = mapped_column(ForeignKey("evidence_assertions.id"), nullable=False)


class TemplateCompilation(FrozenPayload, Base):
    __tablename__ = "template_compilations"
    template_id: Mapped[object | None] = mapped_column(GUID(), ForeignKey("ast_templates.id"))
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ReportRun(Base):
    __tablename__ = "report_runs"
    __table_args__ = (UniqueConstraint("actor", "idempotency_key", name="uq_report_run_request"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[object | None] = mapped_column(GUID(), ForeignKey("ast_templates.id"))
    compilation_id: Mapped[str] = mapped_column(
        ForeignKey("template_compilations.id"), nullable=False
    )
    source_bundle: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    phase: Mapped[str] = mapped_column(String(30), default="frozen")
    execution_status: Mapped[str] = mapped_column(String(20), default="pending")
    material_status: Mapped[str] = mapped_column(String(20), default="incomplete")
    review_status: Mapped[str] = mapped_column(String(20), default="draft")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[dict | None] = mapped_column(JSON)
    worker_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportInputSnapshot(FrozenPayload, Base):
    __tablename__ = "report_input_snapshots"
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ReportOutputResult(FrozenPayload, Base):
    __tablename__ = "report_output_results"
    run_id: Mapped[str] = mapped_column(ForeignKey("report_runs.id"), nullable=False, index=True)
    output_id: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "output_id",
            "execution_scope_id",
            "attempt",
            name="uq_report_output_attempt",
        ),
    )


class ReportBody(FrozenPayload, Base):
    __tablename__ = "report_bodies"
    run_id: Mapped[str] = mapped_column(ForeignKey("report_runs.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "attempt", name="uq_report_body_attempt"),)


class ReportContentVersion(FrozenPayload, Base):
    __tablename__ = "report_content_versions"
    run_id: Mapped[str] = mapped_column(ForeignKey("report_runs.id"), nullable=False)
    body_id: Mapped[str] = mapped_column(ForeignKey("report_bodies.id"), nullable=False)
    input_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("report_input_snapshots.id"),
        nullable=False,
    )


class ReportContentReview(FrozenPayload, Base):
    __tablename__ = "report_content_reviews"
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("report_content_versions.id"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)


class ReportSigningSession(Base):
    __tablename__ = "report_signing_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("report_content_versions.id"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("report_signing_sessions.id"))
    frozen_context: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open")
    revision_no: Mapped[int] = mapped_column(Integer, default=0)
    frozen_signature_id: Mapped[str | None] = mapped_column(String(64))
    envelope_request: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReportSignature(FrozenPayload, Base):
    __tablename__ = "report_signatures"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("report_signing_sessions.id"), nullable=False
    )
    signature_slot_id: Mapped[str] = mapped_column(String(500), nullable=False)
    signature_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("session_id", "signature_slot_id", name="uq_signature_slot"),
    )


class ReportSignatureEvent(FrozenPayload, Base):
    __tablename__ = "report_signature_events"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("report_signing_sessions.id"), nullable=False
    )
    signature_id: Mapped[str] = mapped_column(ForeignKey("report_signatures.id"), nullable=False)
    signature_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("session_id", "signature_revision", name="uq_signature_event"),
    )


class ReportSignatureSnapshot(FrozenPayload, Base):
    __tablename__ = "report_signature_snapshots"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("report_signing_sessions.id"), nullable=False
    )
    signature_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "signature_revision",
            name="uq_signature_snapshot",
        ),
    )


class ReportSigningEnvelope(FrozenPayload, Base):
    __tablename__ = "report_signing_envelopes"
    session_id: Mapped[str] = mapped_column(
        ForeignKey("report_signing_sessions.id"),
        nullable=False,
        unique=True,
    )
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("report_content_versions.id"),
        nullable=False,
    )
    signature_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("report_signature_snapshots.id"),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("report_signing_envelopes.id"))


class ReportArtifact(FrozenPayload, Base):
    __tablename__ = "report_artifacts"
    run_id: Mapped[str] = mapped_column(ForeignKey("report_runs.id"), nullable=False, index=True)
    body_id: Mapped[str | None] = mapped_column(ForeignKey("report_bodies.id"))
    envelope_id: Mapped[str | None] = mapped_column(ForeignKey("report_signing_envelopes.id"))
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)


class TemplateMigrationPlan(FrozenPayload, Base):
    __tablename__ = "template_migration_plans"
    template_id: Mapped[object] = mapped_column(GUID(), ForeignKey("ast_templates.id"))


class RuleMigrationPlan(FrozenPayload, Base):
    __tablename__ = "rule_migration_plans"
    rule_id: Mapped[object] = mapped_column(GUID(), ForeignKey("ontology_decision_rule.id"))


class ReportRequest(FrozenPayload, Base):
    __tablename__ = "report_requests"
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "actor",
            "operation",
            "idempotency_key",
            name="uq_report_request_key",
        ),
    )


class CalculationDecision(FrozenPayload, Base):
    """Append-only decisions pinned to an exact entity calculation, never a job-wide switch."""

    __tablename__ = "calculation_decisions"
    job_id: Mapped[object] = mapped_column(GUID(), ForeignKey("extraction_jobs.id"), index=True)
    subject_candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "job_id", "subject_candidate_id", "revision_no", name="uq_calculation_decision_revision"
        ),
    )


IMMUTABLE_MODELS = (
    OntologySchemaSnapshot,
    CalculationDecision,
    ContractRevision,
    ContractDecision,
    ContractRecord,
    ContractRecordReview,
    OntologyDecisionRuleRevision,
    ConditionDefinition,
    ConditionalAssertionBinding,
    TemplateCompilation,
    ReportInputSnapshot,
    ReportOutputResult,
    ReportBody,
    ReportContentVersion,
    ReportContentReview,
    ReportSignature,
    ReportSignatureEvent,
    ReportSignatureSnapshot,
    ReportSigningEnvelope,
    ReportArtifact,
    TemplateMigrationPlan,
    RuleMigrationPlan,
    ReportRequest,
)


def _immutable(mapper, connection, target):
    raise ValueError(f"{target.__tablename__} is immutable; append a revision or event")


for _model in IMMUTABLE_MODELS:
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)

for _model in (ReportRun, ReportSigningSession):
    event.listen(_model, "before_delete", _immutable)


@event.listens_for(ReportRun, "before_update")
def _run_frozen(mapper, connection, target):
    for key in ("source_bundle", "source_hash", "request_hash", "compilation_id", "template_id"):
        if sa_inspect(target).attrs[key].history.has_changes():
            raise ValueError("report source bundle is immutable")


@event.listens_for(ReportSigningSession, "before_update")
def _session_frozen(mapper, connection, target):
    for key in ("content_version_id", "content_hash", "frozen_context", "parent_id"):
        if sa_inspect(target).attrs[key].history.has_changes():
            raise ValueError("signing session context is immutable")
