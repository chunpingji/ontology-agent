"""Mock 外部事实源数据的数据库模型 —— 支持运行时编辑与持久化。

真实内网 API 接入前，系统使用 canned mock 数据。本模块提供数据库表存储这些 mock 数据，
支持通过管理界面编辑、新增、删除，修改立即生效且重启后保留。

设计原则：
- 每个事实源类型一张表（departments, roles, equipment, production_areas, team_members）
- data_properties 存为 JSON（灵活扩展属性，无需 ALTER TABLE）
- 代码/ID 字段加 UNIQUE 约束（防重）
- 提供 created_at/updated_at 审计字段
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid():
    return uuid.uuid4()


def _now():
    return datetime.now(timezone.utc)


class MockDepartment(Base):
    __tablename__ = "mock_departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    iri: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_properties: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MockRole(Base):
    __tablename__ = "mock_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    iri: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    role_class_iri: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_properties: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MockEquipment(Base):
    __tablename__ = "mock_equipment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    equipment_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    iri: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    equipment_class_iri: Mapped[str] = mapped_column(String(500), nullable=False)
    workshop_code: Mapped[str] = mapped_column(String(50), nullable=False)
    data_properties: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MockProductionArea(Base):
    __tablename__ = "mock_production_areas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    iri: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_properties: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MockTeamMember(Base):
    """评估小组与审批小组成员共用一张表，通过 team_type 区分。"""
    __tablename__ = "mock_team_members"
    __table_args__ = (
        UniqueConstraint("team_type", "role_code", name="uq_team_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "assessment" or "approver"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_label: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(200), nullable=False)
    role_class_iri: Mapped[str] = mapped_column(String(500), nullable=False)
    role_code: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
