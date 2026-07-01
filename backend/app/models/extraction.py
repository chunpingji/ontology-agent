import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.types import GUID


def _uuid():
    return uuid.uuid4()


def _now():
    return datetime.now(timezone.utc)


class ExtractionConfig(Base):
    __tablename__ = "extraction_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_class_iri: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    column_mapping: Mapped[dict | None] = mapped_column(JSON)
    # 008 US3：声明为自由文本的列白名单，其原文经本地 NER 富化本行属性（仅补空缺、
    # 结构化权威）。与 column_mapping 同为可空 JSON、互不重叠（data-model §3.2，FR-008）。
    ner_columns: Mapped[list | None] = mapped_column(JSON)
    llm_prompt_template: Mapped[str | None] = mapped_column(Text)
    few_shot_examples: Mapped[dict | None] = mapped_column(JSON)
    property_constraints: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(500))
    source_config: Mapped[dict | None] = mapped_column(JSON)
    document_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total_candidates: Mapped[int] = mapped_column(Integer, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidates: Mapped[list["ExtractionCandidate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ExtractionCandidate(Base):
    __tablename__ = "extraction_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="CASCADE"), nullable=False
    )
    target_class_iri: Mapped[str] = mapped_column(String(500), nullable=False)
    extracted_properties: Mapped[dict] = mapped_column(JSON, nullable=False)
    alignment_result: Mapped[str | None] = mapped_column(String(20))
    aligned_iri: Mapped[str | None] = mapped_column(String(500))
    match_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_iri: Mapped[str | None] = mapped_column(String(500))

    # 002-extraction-realtime-reasoning（data-model §1.1）：跨源归组 / 规范实例 /
    # 多类型候选（实例·类·关系·Action）/ LLM 回退降级 / 合并目标。
    candidate_kind: Mapped[str] = mapped_column(String(20), default="instance", nullable=False)
    group_key: Mapped[str | None] = mapped_column(String(500), index=True)
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    degraded_reason: Mapped[str | None] = mapped_column(String(200))
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("extraction_candidates.id")
    )
    action_conditions: Mapped[dict | None] = mapped_column(JSON)

    job: Mapped[ExtractionJob] = relationship(back_populates="candidates")


class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    report_type: Mapped[str] = mapped_column(String(50), nullable=False, default="risk_assessment")
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    rules_fired_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rules_summary: Mapped[dict | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[ExtractionJob] = relationship()
