"""CMCReport「推导 vs 原文」PDE 冲突的人工决策记录（CAS 幂等）。

关系图谱在共线评估端点上暴露 PDE 冲突（推导 band 5 vs 原文 band 2）。用户对每份文档的该冲突
作一次人工裁决：采纳推导 / 采纳原文 / 待复核。以 ``(job_id, conflict_key)`` 唯一，``version``
供乐观并发（``expected_version`` CAS）。这是**人工决策**记录，区别于 E13 ConflictPolicy 的策略自动消解。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 决策取值：采纳推导侧 / 采纳原文侧 / 待复核（未决）。
DECISION_CHOICES = ("derived", "asserted", "pending")


def _uuid():
    return uuid.uuid4()


def _now():
    return datetime.now(timezone.utc)


class PdeConflictDecision(Base):
    __tablename__ = "pde_conflict_decisions"
    __table_args__ = (
        UniqueConstraint("job_id", "conflict_key", name="uq_pde_conflict_job_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # 抽取作业（松耦合，不设 FK；标注缓存亦按 job_id 索引）。
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    conflict_key: Mapped[str] = mapped_column(String(64), nullable=False, default="shared_line_pde")
    chosen: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
