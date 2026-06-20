import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EntityShadow(Base):
    __tablename__ = "entity_shadows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iri: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)
    class_iri: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    label_zh: Mapped[str | None] = mapped_column(String(500))
    label_en: Mapped[str | None] = mapped_column(String(500))
    module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    properties_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_entity_shadows_module_class", "module", "class_iri"),
    )
