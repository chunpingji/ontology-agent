"""Append-only 审计哈希链单写路径（FR-028/029, data-model §1.3, VR-5）。

全链路操作（抽取/对齐/物化/推理/动作/签名）均经 ``append()`` 写入一条
``audit_log``，链式哈希：``entry_hash = SHA-256(prev_hash ‖ 规范化记录)``。
``verify()`` 顺序重算并定位首个断裂 ``seq``。这是唯一写路径——禁止 UPDATE/DELETE。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reasoning import AuditLog

GENESIS_HASH = "0" * 64


def _canonical(seq: int, action: str, actor: str | None, entity_iri: str | None,
               details: dict | None) -> str:
    """稳定、可重算的记录规范化串（字段顺序与序列化固定）。"""
    payload = {
        "seq": seq,
        "action": action,
        "actor": actor,
        "entity_iri": entity_iri,
        "details": details,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_entry_hash(prev_hash: str, seq: int, action: str, actor: str | None,
                       entity_iri: str | None, details: dict | None) -> str:
    record = _canonical(seq, action, actor, entity_iri, details)
    return hashlib.sha256((prev_hash + record).encode("utf-8")).hexdigest()


def _head(db: Session) -> AuditLog | None:
    return db.execute(
        select(AuditLog).where(AuditLog.seq.is_not(None)).order_by(AuditLog.seq.desc()).limit(1)
    ).scalars().first()


def append(
    db: Session,
    action: str,
    actor: str | None = None,
    entity_iri: str | None = None,
    details: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> AuditLog:
    """追加一条哈希链审计记录并返回它。``commit=False`` 时由调用方统一提交。"""
    head = _head(db)
    prev_hash = head.entry_hash if head and head.entry_hash else GENESIS_HASH
    seq = (head.seq + 1) if head and head.seq is not None else 1

    entry_hash = compute_entry_hash(prev_hash, seq, action, actor, entity_iri, details)
    entry = AuditLog(
        action=action,
        actor=actor,
        entity_iri=entity_iri,
        details=details,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        seq=seq,
    )
    db.add(entry)
    db.flush()
    if commit:
        db.commit()
    return entry


def verify(db: Session) -> dict[str, Any]:
    """按 ``seq`` 顺序重算整链，定位首个断裂记录（FR-029, SC-008）。"""
    rows = db.execute(
        select(AuditLog).where(AuditLog.seq.is_not(None)).order_by(AuditLog.seq.asc())
    ).scalars().all()

    prev_hash = GENESIS_HASH
    expected_seq = 1
    verified = 0
    for row in rows:
        # 序号必须连续单调。
        if row.seq != expected_seq:
            return {
                "ok": False,
                "broken_at_seq": row.seq,
                "expected_hash": None,
                "actual_hash": row.entry_hash,
                "reason": f"seq 不连续：期望 {expected_seq} 实得 {row.seq}",
            }
        expected = compute_entry_hash(
            prev_hash, row.seq, row.action, row.actor, row.entity_iri, row.details
        )
        if row.prev_hash != prev_hash or row.entry_hash != expected:
            return {
                "ok": False,
                "broken_at_seq": row.seq,
                "expected_hash": expected,
                "actual_hash": row.entry_hash,
            }
        prev_hash = row.entry_hash
        expected_seq += 1
        verified += 1

    return {"ok": True, "verified_count": verified, "head_seq": verified}
