"""系统配置 API：key-value JSON 配置（默认抽取目标、文件类型关键词等）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import ROLE_SENIOR_ANALYST, require_role
from app.models.system_config import SystemConfig

router = APIRouter()

_writer = require_role(ROLE_SENIOR_ANALYST)


class SystemConfigResponse(BaseModel):
    key: str
    value: Any
    updated_at: str | None = None


class SystemConfigUpdate(BaseModel):
    value: Any


@router.get("", response_model=list[SystemConfigResponse])
def list_configs(db: Session = Depends(get_db)):
    rows = db.query(SystemConfig).all()
    return [
        SystemConfigResponse(
            key=r.key,
            value=r.value,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in rows
    ]


@router.get("/{key}", response_model=SystemConfigResponse)
def get_config(key: str, db: Session = Depends(get_db)):
    row = db.get(SystemConfig, key)
    if not row:
        raise HTTPException(404, f"config key '{key}' not found")
    return SystemConfigResponse(
        key=row.key,
        value=row.value,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.put("/{key}", response_model=SystemConfigResponse)
def upsert_config(
    key: str,
    req: SystemConfigUpdate,
    db: Session = Depends(get_db),
    _identity=Depends(_writer),
):
    row = db.get(SystemConfig, key)
    if row:
        row.value = req.value
    else:
        row = SystemConfig(key=key, value=req.value)
        db.add(row)
    db.commit()
    db.refresh(row)
    return SystemConfigResponse(
        key=row.key,
        value=row.value,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
