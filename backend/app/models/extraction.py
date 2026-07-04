import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
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
    # 013: async report generation status tracking
    report_status: Mapped[str | None] = mapped_column(String(20))
    report_error: Mapped[str | None] = mapped_column(Text)
    # 015: persisted per-section 行文 narrative for web display (transient DOCX prose
    # also surfaced in the report reading pane). Shape:
    #   {subject_description?, conclusion?, sections: [{section_id, title, text}]}
    narratives: Mapped[dict | None] = mapped_column(JSON)

    job: Mapped[ExtractionJob] = relationship()


class AstTemplate(Base):
    __tablename__ = "ast_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_template_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    doc_no: Mapped[str | None] = mapped_column(String(50))
    schema_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    sample_text: Mapped[str | None] = mapped_column(Text)
    # 013: 忠于原文结构的 tiptap 样例（供 AI 插槽建议 drawer 忠实预览与结构锚点联动）。
    sample_content_json: Mapped[dict | None] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # 015: lifecycle status (draft|published|archived) + per-template doc-class IRI
    # pattern. iri_pattern is the functional template-resolution key (replaces the
    # retired DocumentTypeMapping); archived templates are excluded from resolution.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    iri_pattern: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(100))
    # 015 基本信息：责任人（业务负责人，区别于 created_by 创建者/审计执行者）与默认源文件
    # （固化输出格式的参照原件，存 data/uploads，仅存路径/原名）。
    owner: Mapped[str | None] = mapped_column(String(100))
    default_source_path: Mapped[str | None] = mapped_column(String(500))
    default_source_filename: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)

    training_pairs: Mapped[list["AstTemplateTrainingPair"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class AstTemplateTrainingPair(Base):
    """015 训练数据：源文档 → 评估报告 成对样例，用于学习深层次评估语义。

    文件存 data/uploads（复用抽取管线的落盘约定），本表仅存路径与原始文件名。
    评估报告可缺省（先入源文档、后补报告）。
    """

    __tablename__ = "ast_template_training_pairs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ast_templates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    report_filename: Mapped[str | None] = mapped_column(String(500))
    report_path: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    template: Mapped["AstTemplate"] = relationship(back_populates="training_pairs")


class SlotDismissal(Base):
    __tablename__ = "slot_dismissals"
    __table_args__ = (
        UniqueConstraint("job_id", "slot_id", name="uq_slot_dismissal_job_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    slot_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dismissed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
