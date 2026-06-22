"""OntologyMetaStore — editable T-Box metadata CRUD with optimistic concurrency,
dual-store synchronisation, change-log capture, validation, batched release and
audit (R2/R3/R4/R5/R9, FR-008/008a/009/009a/011a/032/035).

The metadata tables are the *draft* source of truth. Writes apply a compare-and
-swap on ``version`` (``WHERE id=? AND version=?``) → ``409`` on mismatch. The
Owlready2 World and authoritative TTL are materialised only at publish time, via
``app.services.ttl_merge`` (surgical merge preserving un-modelled triples) and a
single Git commit.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from rdflib import RDF, RDFS, OWL, URIRef
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ontology_meta import (
    DATATYPES,
    MAPPING_TYPES,
    PROPERTY_KINDS,
    RESTRICTION_KINDS,
    STATUS_DRAFT,
    STATUS_IN_REVIEW,
    STATUS_PUBLISHED,
    AppUser,
    OntologyAction,
    OntologyChangeLog,
    OntologyClass,
    OntologyClassMapping,
    OntologyDataProperty,
    OntologyLinkType,
    OntologyRelease,
    OntologyRestriction,
)
from app.models.reasoning import AuditLog
from app.services import ttl_merge

logger = logging.getLogger(__name__)

MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/"

# Controlled vocabularies for the risk-attribute wizard (§4b, FR-010).
RISK_VOCABULARIES = {
    "OEB": {
        "label": "职业暴露等级 (Occupational Exposure Band)",
        "values": ["OEB1", "OEB2", "OEB3", "OEB4", "OEB5"],
    },
    "PDE": {
        "label": "每日允许暴露量 (Permitted Daily Exposure)",
        "values": ["<10µg/day", "10-100µg/day", "100-1000µg/day", ">1000µg/day"],
    },
    "sensitizer": {
        "label": "致敏性 (Sensitizer)",
        "values": ["呼吸致敏", "皮肤致敏", "非致敏"],
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologyMetaStore:
    def __init__(self, db: Session, engine) -> None:
        self.db = db
        self.engine = engine

    # =================================================================== #
    # Identity / audit helpers
    # =================================================================== #
    def _user_id(self, username: str | None) -> uuid.UUID | None:
        if not username:
            return None
        u = self.db.query(AppUser).filter_by(username=username).first()
        return u.id if u else None

    def audit(
        self,
        action: str,
        entity_iri: str | None,
        actor: str | None,
        release_id: uuid.UUID | None = None,
        details: dict | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                action=action,
                entity_iri=entity_iri,
                actor=actor,
                release_id=release_id,
                details=details,
            )
        )
        self.db.commit()

    def list_audit(
        self,
        entity_iri: str | None = None,
        release_id: str | None = None,
        actor: str | None = None,
    ) -> list[dict]:
        q = self.db.query(AuditLog)
        if entity_iri:
            q = q.filter(AuditLog.entity_iri == entity_iri)
        if actor:
            q = q.filter(AuditLog.actor == actor)
        if release_id:
            q = q.filter(AuditLog.release_id == uuid.UUID(release_id))
        rows = q.order_by(AuditLog.created_at.desc()).limit(500).all()
        return [
            {
                "id": r.id,
                "action": r.action,
                "entity_iri": r.entity_iri,
                "actor": r.actor,
                "release_id": str(r.release_id) if r.release_id else None,
                "details": r.details,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    # =================================================================== #
    # Optimistic-concurrency CAS
    # =================================================================== #
    def _cas_update(self, model, obj_id: uuid.UUID, expected_version: int, changes: dict):
        """Compare-and-swap on version. 0 rows affected → 409 (R4)."""
        stmt = (
            update(model)
            .where(model.id == obj_id, model.version == expected_version)
            .values(version=model.version + 1, updated_at=_now(), **changes)
        )
        res = self.db.execute(stmt)
        if res.rowcount == 0:
            self.db.rollback()
            current = self.db.get(model, obj_id)
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "版本冲突：他人已更新该实体",
                    "current_version": current.version if current else None,
                },
            )
        self.db.commit()
        return self.db.get(model, obj_id)

    def _validate_iri(self, iri: str) -> None:
        if not iri or not iri.startswith(MANAGED_PREFIX):
            raise HTTPException(
                status_code=400, detail=f"IRI 不属于受管命名空间 {MANAGED_PREFIX}"
            )

    # =================================================================== #
    # Lookups / DTO builders
    # =================================================================== #
    def _class_by_iri(self, iri: str) -> OntologyClass | None:
        return self.db.query(OntologyClass).filter_by(slpra_iri=iri).first()

    def _require_class(self, iri: str) -> OntologyClass:
        c = self._class_by_iri(iri)
        if not c:
            raise HTTPException(status_code=404, detail=f"类不存在：{iri}")
        return c

    def _iri_of_class(self, class_id: uuid.UUID | None) -> str | None:
        if not class_id:
            return None
        c = self.db.get(OntologyClass, class_id)
        return c.slpra_iri if c else None

    def _property_iri(self, prop_id: uuid.UUID | None) -> str | None:
        if not prop_id:
            return None
        lt = self.db.get(OntologyLinkType, prop_id)
        if lt:
            return lt.slpra_iri
        dp = self.db.get(OntologyDataProperty, prop_id)
        return dp.slpra_iri if dp else None

    def _restriction_summary(self, r: OntologyRestriction) -> dict:
        return {
            "id": str(r.id),
            "kind": r.kind,
            "property_iri": self._property_iri(r.on_property_id),
            "property_kind": r.property_kind,
            "filler_iri": self._iri_of_class(r.filler_class_id),
            "cardinality": r.cardinality,
            "version": r.version,
            "status": r.status,
        }

    def _mapping_dto(self, m: OntologyClassMapping) -> dict:
        return {
            "id": str(m.id),
            "class_iri": self._iri_of_class(m.class_id),
            "mapping_type": m.mapping_type,
            "target": m.target,
            "source_system": m.source_system,
            "health": m.health,
            "version": m.version,
            "status": m.status,
        }

    def class_detail(self, c: OntologyClass) -> dict:
        restrictions = (
            self.db.query(OntologyRestriction).filter_by(owner_class_id=c.id).all()
        )
        mappings = self.db.query(OntologyClassMapping).filter_by(class_id=c.id).all()
        return {
            "id": str(c.id),
            "slpra_iri": c.slpra_iri,
            "label": c.label,
            "comment": c.comment,
            "module": c.module,
            "parent_iri": self._iri_of_class(c.parent_class_id),
            "bfo_category": c.bfo_category,
            "field_schema": c.field_schema,
            "status": c.status,
            "version": c.version,
            "is_reviewed": c.is_reviewed,
            "is_disabled": c.is_disabled,
            "confidence": c.confidence,
            "restrictions": [self._restriction_summary(r) for r in restrictions],
            "mappings": [self._mapping_dto(m) for m in mappings],
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }

    def link_type_detail(self, lt: OntologyLinkType) -> dict:
        inv = self.db.get(OntologyLinkType, lt.inverse_link_id) if lt.inverse_link_id else None
        return {
            "id": str(lt.id),
            "slpra_iri": lt.slpra_iri,
            "label": lt.label,
            "comment": lt.comment,
            "domain_iri": self._iri_of_class(lt.domain_class_id),
            "range_iri": self._iri_of_class(lt.range_class_id),
            "inverse_iri": inv.slpra_iri if inv else None,
            "min_cardinality": lt.min_cardinality,
            "max_cardinality": lt.max_cardinality,
            "is_functional": lt.is_functional,
            "is_symmetric": lt.is_symmetric,
            "is_transitive": lt.is_transitive,
            "status": lt.status,
            "version": lt.version,
            "is_disabled": lt.is_disabled,
        }

    def data_property_detail(self, dp: OntologyDataProperty) -> dict:
        return {
            "id": str(dp.id),
            "slpra_iri": dp.slpra_iri,
            "label": dp.label,
            "comment": dp.comment,
            "domain_iri": self._iri_of_class(dp.domain_class_id),
            "datatype": dp.datatype,
            "unit": dp.unit,
            "controlled_vocab": dp.controlled_vocab,
            "status": dp.status,
            "version": dp.version,
            "is_disabled": dp.is_disabled,
        }

    def action_detail(self, a: OntologyAction) -> dict:
        return {
            "id": str(a.id),
            "slpra_iri": a.slpra_iri,
            "label": a.label,
            "comment": a.comment,
            "actor_iri": self._iri_of_class(a.actor_class_id),
            "target_iri": self._iri_of_class(a.target_class_id),
            "precondition": a.precondition,
            "postcondition": a.postcondition,
            "params": a.params,
            "status": a.status,
            "version": a.version,
            "is_disabled": a.is_disabled,
        }

    # =================================================================== #
    # E1 Class CRUD
    # =================================================================== #
    def create_class(self, payload, actor: str) -> dict:
        self._validate_iri(payload.slpra_iri)
        if self._class_by_iri(payload.slpra_iri):
            raise HTTPException(status_code=400, detail="IRI 已存在")
        parent_id = None
        if payload.parent_iri:
            parent_id = self._require_class(payload.parent_iri).id
        uid = self._user_id(actor)
        c = OntologyClass(
            slpra_iri=payload.slpra_iri,
            label=payload.label,
            comment=payload.comment,
            module=payload.module,
            parent_class_id=parent_id,
            bfo_category=payload.bfo_category,
            field_schema=payload.field_schema,
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(c)
        self.db.commit()
        self.db.refresh(c)
        self.audit("class.create", c.slpra_iri, actor, details={"label": c.label})
        return self.class_detail(c)

    def update_class(self, iri: str, payload, actor: str) -> dict:
        c = self._require_class(iri)
        changes: dict = {"updated_by": self._user_id(actor)}
        for f in ("label", "comment", "module", "bfo_category", "field_schema"):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        if payload.parent_iri is not None:
            parent = self._require_class(payload.parent_iri)
            if parent.id == c.id:
                raise HTTPException(status_code=400, detail="父类不得指向自身")
            changes["parent_class_id"] = parent.id
        c = self._cas_update(OntologyClass, c.id, payload.expected_version, changes)
        self.audit("class.update", iri, actor)
        return self.class_detail(c)

    def delete_class(self, iri: str, expected_version: int, actor: str) -> None:
        c = self._require_class(iri)
        self._cas_update(
            OntologyClass, c.id, expected_version, {"is_disabled": True, "status": STATUS_DRAFT}
        )
        self.audit("class.delete", iri, actor)

    def set_class_flag(self, iri: str, expected_version: int, actor: str, **flag) -> dict:
        c = self._require_class(iri)
        c = self._cas_update(OntologyClass, c.id, expected_version, flag)
        self.audit(f"class.{'disable' if flag.get('is_disabled') else 'review'}", iri, actor)
        return self.class_detail(c)

    # =================================================================== #
    # E2 Link type CRUD
    # =================================================================== #
    def create_link_type(self, payload, actor: str) -> dict:
        self._validate_iri(payload.slpra_iri)
        if self.db.query(OntologyLinkType).filter_by(slpra_iri=payload.slpra_iri).first():
            raise HTTPException(status_code=400, detail="IRI 已存在")
        domain_id = self._domain_range_id(payload.domain_iri)
        range_id = self._domain_range_id(payload.range_iri)
        self._check_cardinality(payload.min_cardinality, payload.max_cardinality)
        inv_id = None
        if payload.inverse_iri:
            inv = self.db.query(OntologyLinkType).filter_by(slpra_iri=payload.inverse_iri).first()
            if not inv:
                raise HTTPException(status_code=400, detail="逆属性不存在")
            inv_id = inv.id
        uid = self._user_id(actor)
        lt = OntologyLinkType(
            slpra_iri=payload.slpra_iri,
            label=payload.label,
            comment=payload.comment,
            domain_class_id=domain_id,
            range_class_id=range_id,
            inverse_link_id=inv_id,
            min_cardinality=payload.min_cardinality,
            max_cardinality=payload.max_cardinality,
            is_functional=payload.is_functional,
            is_symmetric=payload.is_symmetric,
            is_transitive=payload.is_transitive,
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(lt)
        self.db.commit()
        self.db.refresh(lt)
        self.audit("link_type.create", lt.slpra_iri, actor)
        return self.link_type_detail(lt)

    def update_link_type(self, iri: str, payload, actor: str) -> dict:
        lt = self.db.query(OntologyLinkType).filter_by(slpra_iri=iri).first()
        if not lt:
            raise HTTPException(status_code=404, detail=f"关系不存在：{iri}")
        self._check_cardinality(payload.min_cardinality, payload.max_cardinality)
        changes: dict = {"updated_by": self._user_id(actor)}
        for f in (
            "label", "comment", "min_cardinality", "max_cardinality",
            "is_functional", "is_symmetric", "is_transitive",
        ):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        if payload.domain_iri:
            changes["domain_class_id"] = self._domain_range_id(payload.domain_iri)
        if payload.range_iri:
            changes["range_class_id"] = self._domain_range_id(payload.range_iri)
        lt = self._cas_update(OntologyLinkType, lt.id, payload.expected_version, changes)
        self.audit("link_type.update", iri, actor)
        return self.link_type_detail(lt)

    def delete_link_type(self, iri: str, expected_version: int, actor: str) -> None:
        lt = self.db.query(OntologyLinkType).filter_by(slpra_iri=iri).first()
        if not lt:
            raise HTTPException(status_code=404, detail=f"关系不存在：{iri}")
        self._cas_update(OntologyLinkType, lt.id, expected_version, {"is_disabled": True})
        self.audit("link_type.delete", iri, actor)

    def _domain_range_id(self, iri: str | None) -> uuid.UUID | None:
        if not iri:
            return None
        c = self._require_class(iri)
        if c.is_disabled:
            raise HTTPException(status_code=400, detail=f"domain/range 指向已停用类：{iri}")
        return c.id

    def _check_cardinality(self, lo: int | None, hi: int | None) -> None:
        if lo is not None and hi is not None and lo > hi:
            raise HTTPException(status_code=400, detail="min_cardinality 不得大于 max_cardinality")

    # =================================================================== #
    # E3 Data property CRUD (+ risk wizard)
    # =================================================================== #
    def create_data_property(self, payload, actor: str) -> dict:
        self._validate_iri(payload.slpra_iri)
        if payload.datatype not in DATATYPES:
            raise HTTPException(status_code=400, detail=f"非法 datatype：{payload.datatype}")
        if self.db.query(OntologyDataProperty).filter_by(slpra_iri=payload.slpra_iri).first():
            raise HTTPException(status_code=400, detail="IRI 已存在")
        domain_id = self._domain_range_id(payload.domain_iri)
        uid = self._user_id(actor)
        dp = OntologyDataProperty(
            slpra_iri=payload.slpra_iri,
            label=payload.label,
            comment=getattr(payload, "comment", None),
            domain_class_id=domain_id,
            datatype=payload.datatype,
            unit=getattr(payload, "unit", None),
            controlled_vocab=getattr(payload, "controlled_vocab", None),
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(dp)
        self.db.commit()
        self.db.refresh(dp)
        self.audit("data_property.create", dp.slpra_iri, actor)
        return self.data_property_detail(dp)

    def create_risk_data_property(self, payload, actor: str) -> dict:
        vocab = RISK_VOCABULARIES.get(payload.vocab)
        if not vocab:
            raise HTTPException(status_code=400, detail=f"未知受控词表：{payload.vocab}")

        class _P:  # adapt to create_data_property's expected attrs
            slpra_iri = payload.slpra_iri
            label = payload.label
            comment = vocab["label"]
            domain_iri = payload.domain_iri
            datatype = payload.datatype
            unit = None
            controlled_vocab = {"vocab": payload.vocab, "values": vocab["values"]}

        return self.create_data_property(_P(), actor)

    def update_data_property(self, iri: str, payload, actor: str) -> dict:
        dp = self.db.query(OntologyDataProperty).filter_by(slpra_iri=iri).first()
        if not dp:
            raise HTTPException(status_code=404, detail=f"数据属性不存在：{iri}")
        if payload.datatype is not None and payload.datatype not in DATATYPES:
            raise HTTPException(status_code=400, detail=f"非法 datatype：{payload.datatype}")
        changes: dict = {"updated_by": self._user_id(actor)}
        for f in ("label", "comment", "datatype", "unit", "controlled_vocab"):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        if payload.domain_iri:
            changes["domain_class_id"] = self._domain_range_id(payload.domain_iri)
        dp = self._cas_update(OntologyDataProperty, dp.id, payload.expected_version, changes)
        self.audit("data_property.update", iri, actor)
        return self.data_property_detail(dp)

    def delete_data_property(self, iri: str, expected_version: int, actor: str) -> None:
        dp = self.db.query(OntologyDataProperty).filter_by(slpra_iri=iri).first()
        if not dp:
            raise HTTPException(status_code=404, detail=f"数据属性不存在：{iri}")
        self._cas_update(OntologyDataProperty, dp.id, expected_version, {"is_disabled": True})
        self.audit("data_property.delete", iri, actor)

    def risk_vocabularies(self) -> list[dict]:
        return [
            {"key": k, "label": v["label"], "values": v["values"]}
            for k, v in RISK_VOCABULARIES.items()
        ]

    # =================================================================== #
    # E4 Action CRUD (definition only, R10)
    # =================================================================== #
    def list_actions(self) -> list[dict]:
        return [self.action_detail(a) for a in self.db.query(OntologyAction).all()]

    def create_action(self, payload, actor: str) -> dict:
        self._validate_iri(payload.slpra_iri)
        if self.db.query(OntologyAction).filter_by(slpra_iri=payload.slpra_iri).first():
            raise HTTPException(status_code=400, detail="IRI 已存在")
        uid = self._user_id(actor)
        a = OntologyAction(
            slpra_iri=payload.slpra_iri,
            label=payload.label,
            comment=payload.comment,
            actor_class_id=self._optional_class_id(payload.actor_iri),
            target_class_id=self._optional_class_id(payload.target_iri),
            precondition=payload.precondition,
            postcondition=payload.postcondition,
            params=payload.params,
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(a)
        self.db.commit()
        self.db.refresh(a)
        self.audit("action.create", a.slpra_iri, actor)
        return self.action_detail(a)

    def update_action(self, iri: str, payload, actor: str) -> dict:
        a = self.db.query(OntologyAction).filter_by(slpra_iri=iri).first()
        if not a:
            raise HTTPException(status_code=404, detail=f"Action 不存在：{iri}")
        changes: dict = {"updated_by": self._user_id(actor)}
        for f in ("label", "comment", "precondition", "postcondition", "params"):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        if payload.actor_iri:
            changes["actor_class_id"] = self._optional_class_id(payload.actor_iri)
        if payload.target_iri:
            changes["target_class_id"] = self._optional_class_id(payload.target_iri)
        a = self._cas_update(OntologyAction, a.id, payload.expected_version, changes)
        self.audit("action.update", iri, actor)
        return self.action_detail(a)

    def delete_action(self, iri: str, expected_version: int, actor: str) -> None:
        a = self.db.query(OntologyAction).filter_by(slpra_iri=iri).first()
        if not a:
            raise HTTPException(status_code=404, detail=f"Action 不存在：{iri}")
        self._cas_update(OntologyAction, a.id, expected_version, {"is_disabled": True})
        self.audit("action.delete", iri, actor)

    def _optional_class_id(self, iri: str | None) -> uuid.UUID | None:
        return self._require_class(iri).id if iri else None

    # =================================================================== #
    # E5 Restriction CRUD
    # =================================================================== #
    def create_restriction(self, owner_iri: str, payload, actor: str) -> dict:
        owner = self._require_class(owner_iri)
        if payload.kind not in RESTRICTION_KINDS:
            raise HTTPException(status_code=400, detail=f"非法约束类型：{payload.kind}")
        if payload.property_kind and payload.property_kind not in PROPERTY_KINDS:
            raise HTTPException(status_code=400, detail="非法 property_kind")
        self._validate_restriction(payload)
        prop_id = self._property_id(payload.property_iri) if payload.property_iri else None
        filler_id = self._optional_class_id(payload.filler_iri)
        r = OntologyRestriction(
            owner_class_id=owner.id,
            kind=payload.kind,
            on_property_id=prop_id,
            property_kind=payload.property_kind,
            filler_class_id=filler_id,
            cardinality=payload.cardinality,
            created_by=self._user_id(actor),
            updated_by=self._user_id(actor),
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        self.audit("restriction.create", owner_iri, actor, details={"kind": payload.kind})
        return self._restriction_summary(r)

    def update_restriction(self, rid: str, payload, actor: str) -> dict:
        r = self.db.get(OntologyRestriction, uuid.UUID(rid))
        if not r:
            raise HTTPException(status_code=404, detail="约束不存在")
        merged = _Merged(r, payload, self)
        self._validate_restriction(merged)
        changes: dict = {"updated_by": self._user_id(actor)}
        if payload.kind is not None:
            changes["kind"] = payload.kind
        if payload.property_kind is not None:
            changes["property_kind"] = payload.property_kind
        if payload.cardinality is not None:
            changes["cardinality"] = payload.cardinality
        if payload.property_iri is not None:
            changes["on_property_id"] = self._property_id(payload.property_iri)
        if payload.filler_iri is not None:
            changes["filler_class_id"] = self._optional_class_id(payload.filler_iri)
        r = self._cas_update(OntologyRestriction, r.id, payload.expected_version, changes)
        self.audit("restriction.update", self._iri_of_class(r.owner_class_id), actor)
        return self._restriction_summary(r)

    def delete_restriction(self, rid: str, expected_version: int, actor: str) -> None:
        r = self.db.get(OntologyRestriction, uuid.UUID(rid))
        if not r:
            raise HTTPException(status_code=404, detail="约束不存在")
        owner_iri = self._iri_of_class(r.owner_class_id)
        # CAS guard, then hard delete (restrictions are leaf axioms)
        stmt = (
            update(OntologyRestriction)
            .where(
                OntologyRestriction.id == r.id,
                OntologyRestriction.version == expected_version,
            )
            .values(version=OntologyRestriction.version + 1)
        )
        if self.db.execute(stmt).rowcount == 0:
            self.db.rollback()
            raise HTTPException(status_code=409, detail="版本冲突")
        self.db.delete(self.db.get(OntologyRestriction, r.id))
        self.db.commit()
        self.audit("restriction.delete", owner_iri, actor)

    def _property_id(self, iri: str) -> uuid.UUID:
        lt = self.db.query(OntologyLinkType).filter_by(slpra_iri=iri).first()
        if lt:
            return lt.id
        dp = self.db.query(OntologyDataProperty).filter_by(slpra_iri=iri).first()
        if dp:
            return dp.id
        raise HTTPException(status_code=400, detail=f"on_property 不存在：{iri}")

    def _validate_restriction(self, p) -> None:
        kind = p.kind
        if kind in ("some", "only") and not (p.property_iri and p.filler_iri):
            raise HTTPException(status_code=400, detail=f"{kind} 需 property_iri + filler_iri")
        if kind in ("exactly", "min", "max") and not (
            p.property_iri and p.cardinality is not None
        ):
            raise HTTPException(status_code=400, detail=f"{kind} 需 property_iri + cardinality")
        if kind in ("disjoint", "equivalent") and not p.filler_iri:
            raise HTTPException(status_code=400, detail=f"{kind} 需目标类 filler_iri")

    # =================================================================== #
    # E6 Mapping CRUD + health
    # =================================================================== #
    def list_mappings(self, class_iri: str) -> list[dict]:
        c = self._require_class(class_iri)
        return [
            self._mapping_dto(m)
            for m in self.db.query(OntologyClassMapping).filter_by(class_id=c.id).all()
        ]

    def create_mapping(self, class_iri: str, payload, actor: str) -> dict:
        c = self._require_class(class_iri)
        if payload.mapping_type not in MAPPING_TYPES:
            raise HTTPException(status_code=400, detail=f"非法 mapping_type：{payload.mapping_type}")
        m = OntologyClassMapping(
            class_id=c.id,
            mapping_type=payload.mapping_type,
            target=payload.target,
            source_system=payload.source_system,
            health="ok",
            created_by=self._user_id(actor),
            updated_by=self._user_id(actor),
        )
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        self.audit("mapping.create", class_iri, actor, details={"type": payload.mapping_type})
        return self._mapping_dto(m)

    def update_mapping(self, mid: str, payload, actor: str) -> dict:
        m = self.db.get(OntologyClassMapping, uuid.UUID(mid))
        if not m:
            raise HTTPException(status_code=404, detail="映射不存在")
        if payload.mapping_type not in MAPPING_TYPES:
            raise HTTPException(status_code=400, detail=f"非法 mapping_type：{payload.mapping_type}")
        changes = {
            "mapping_type": payload.mapping_type,
            "target": payload.target,
            "source_system": payload.source_system,
            "updated_by": self._user_id(actor),
        }
        m = self._cas_update(OntologyClassMapping, m.id, payload.expected_version, changes)
        self.audit("mapping.update", self._iri_of_class(m.class_id), actor)
        return self._mapping_dto(m)

    def delete_mapping(self, mid: str, expected_version: int, actor: str) -> None:
        m = self.db.get(OntologyClassMapping, uuid.UUID(mid))
        if not m:
            raise HTTPException(status_code=404, detail="映射不存在")
        class_iri = self._iri_of_class(m.class_id)
        stmt = (
            update(OntologyClassMapping)
            .where(
                OntologyClassMapping.id == m.id,
                OntologyClassMapping.version == expected_version,
            )
            .values(version=OntologyClassMapping.version + 1)
        )
        if self.db.execute(stmt).rowcount == 0:
            self.db.rollback()
            raise HTTPException(status_code=409, detail="版本冲突")
        self.db.delete(self.db.get(OntologyClassMapping, m.id))
        self.db.commit()
        self.audit("mapping.delete", class_iri, actor)

    def mappings_health(self) -> dict:
        ok, unmapped, drift, orphan = [], [], [], []
        for c in self.db.query(OntologyClass).filter_by(is_disabled=False).all():
            maps = self.db.query(OntologyClassMapping).filter_by(class_id=c.id).all()
            types = {m.mapping_type for m in maps}
            has_states = {m.health for m in maps}
            if not maps:
                unmapped.append(c.slpra_iri)
            elif "drift" in has_states:
                drift.append(c.slpra_iri)
            elif "orphan" in has_states:
                orphan.append(c.slpra_iri)
            elif "slpra_iri" in types and "bfo" in types:
                ok.append(c.slpra_iri)
            else:
                unmapped.append(c.slpra_iri)
        return {"ok": ok, "unmapped": unmapped, "drift": drift, "orphan": orphan}

    # =================================================================== #
    # Validation (R9, §8)
    # =================================================================== #
    def validate(self) -> dict:
        blocking, warnings = [], []
        classes = self.db.query(OntologyClass).filter_by(is_disabled=False).all()
        disabled_ids = {
            c.id for c in self.db.query(OntologyClass).filter_by(is_disabled=True).all()
        }

        for c in classes:
            maps = self.db.query(OntologyClassMapping).filter_by(class_id=c.id).all()
            types = {m.mapping_type for m in maps}
            if "slpra_iri" not in types or "bfo" not in types:
                blocking.append(
                    {
                        "code": "missing_mapping",
                        "message": f"类缺少 slpra_iri/bfo 映射：{c.label}",
                        "entity_iri": c.slpra_iri,
                    }
                )
            # orphan warning: no parent, no children
            has_child = (
                self.db.query(OntologyClass).filter_by(parent_class_id=c.id).first() is not None
            )
            if not c.parent_class_id and not has_child:
                warnings.append(
                    {"code": "orphan_class", "message": f"孤立类：{c.label}", "entity_iri": c.slpra_iri}
                )

        # disabled class still referenced as parent/domain/range → blocking
        if disabled_ids:
            referencing = (
                self.db.query(OntologyClass)
                .filter(
                    OntologyClass.is_disabled.is_(False),
                    OntologyClass.parent_class_id.in_(disabled_ids),
                )
                .all()
            )
            for c in referencing:
                blocking.append(
                    {
                        "code": "disabled_referenced",
                        "message": f"父类已停用：{c.label}",
                        "entity_iri": c.slpra_iri,
                    }
                )
            for lt in (
                self.db.query(OntologyLinkType)
                .filter(
                    OntologyLinkType.is_disabled.is_(False),
                    OntologyLinkType.domain_class_id.in_(disabled_ids)
                    | OntologyLinkType.range_class_id.in_(disabled_ids),
                )
                .all()
            ):
                blocking.append(
                    {
                        "code": "disabled_referenced",
                        "message": f"domain/range 指向已停用类：{lt.label}",
                        "entity_iri": lt.slpra_iri,
                    }
                )

        # cardinality contradictions on link types
        for lt in self.db.query(OntologyLinkType).filter_by(is_disabled=False).all():
            if (
                lt.min_cardinality is not None
                and lt.max_cardinality is not None
                and lt.min_cardinality > lt.max_cardinality
            ):
                blocking.append(
                    {
                        "code": "cardinality_conflict",
                        "message": f"基数矛盾 min>max：{lt.label}",
                        "entity_iri": lt.slpra_iri,
                    }
                )

        reasoner = {"ran": False, "consistent": None, "note": "无 JVM/HermiT，规则式校验已执行（优雅降级）"}
        return {"blocking": blocking, "warnings": warnings, "reasoner": reasoner}

    # =================================================================== #
    # Import / Export / Diff (R3, §9)
    # =================================================================== #
    def import_ttl(self, content: bytes | str, actor: str) -> dict:
        g = ttl_merge.parse_ttl(content)
        added = updated = 0
        conflicts: list[str] = []
        for s in set(g.subjects()):
            if not isinstance(s, URIRef):
                continue
            iri = str(s)
            if not iri.startswith(MANAGED_PREFIX):
                continue
            types = set(g.objects(s, RDF.type))
            label = next(iter(g.objects(s, RDFS.label)), None)
            comment = next(iter(g.objects(s, RDFS.comment)), None)
            if OWL.Class in types:
                existing = self._class_by_iri(iri)
                if existing:
                    existing.label = str(label) if label else existing.label
                    existing.comment = str(comment) if comment else existing.comment
                    updated += 1
                else:
                    self.db.add(
                        OntologyClass(
                            slpra_iri=iri,
                            label=str(label) if label else iri.rsplit("/", 1)[-1],
                            comment=str(comment) if comment else None,
                            status=STATUS_PUBLISHED,
                        )
                    )
                    added += 1
        self.db.commit()
        self.audit("import.ttl", None, actor, details={"added": added, "updated": updated})
        return {"added": added, "updated": updated, "conflicts": conflicts}

    def export_ttl(self, module: str | None = None) -> str:
        return ttl_merge.export_ttl(self.db, settings.ontology_dir)

    def export_diff(self, release_id: str | None = None) -> dict:
        preview, added, removed = ttl_merge.export_diff(self.db, settings.ontology_dir)
        return {
            "turtle_preview": preview,
            "triples_added": added,
            "triples_removed": removed,
        }

    # =================================================================== #
    # Releases (E7/E8, R5, §10)
    # =================================================================== #
    def list_releases(self) -> list[dict]:
        return [
            self._release_summary(r)
            for r in self.db.query(OntologyRelease)
            .order_by(OntologyRelease.created_at.desc())
            .all()
        ]

    def _release_summary(self, r: OntologyRelease) -> dict:
        return {
            "id": str(r.id),
            "release_no": r.release_no,
            "title": r.title,
            "status": r.status,
            "ttl_commit_sha": r.ttl_commit_sha,
            "published_at": r.published_at,
            "created_at": r.created_at,
        }

    def release_detail(self, rid: str) -> dict:
        r = self._require_release(rid)
        summary = self._release_summary(r)
        summary["ttl_diff"] = r.ttl_diff
        summary["validation_report"] = r.validation_report
        summary["change_log"] = [
            {
                "id": str(cl.id),
                "entity_table": cl.entity_table,
                "entity_id": str(cl.entity_id),
                "change_kind": cl.change_kind,
                "before": cl.before,
                "after": cl.after,
            }
            for cl in r.change_logs
        ]
        return summary

    def _require_release(self, rid: str) -> OntologyRelease:
        r = self.db.get(OntologyRelease, uuid.UUID(rid))
        if not r:
            raise HTTPException(status_code=404, detail="发布批次不存在")
        return r

    def create_release(self, title: str, actor: str) -> dict:
        now = _now()
        seq = (
            self.db.query(OntologyRelease)
            .filter(OntologyRelease.release_no.like(f"R{now:%Y.%m.%d}-%"))
            .count()
            + 1
        )
        release_no = f"R{now:%Y.%m.%d}-{seq:02d}"
        r = OntologyRelease(
            release_no=release_no,
            title=title,
            status=STATUS_DRAFT,
            created_by=self._user_id(actor),
        )
        self.db.add(r)
        self.db.flush()
        # aggregate current draft editable entities into the change log
        for model, table in (
            (OntologyClass, "ontology_class"),
            (OntologyLinkType, "ontology_link_type"),
            (OntologyDataProperty, "ontology_data_property"),
            (OntologyAction, "ontology_action"),
        ):
            for e in self.db.query(model).filter_by(status=STATUS_DRAFT).all():
                kind = "create" if e.version == 1 else "update"
                if getattr(e, "is_disabled", False):
                    kind = "disable"
                self.db.add(
                    OntologyChangeLog(
                        release_id=r.id,
                        entity_table=table,
                        entity_id=e.id,
                        change_kind=kind,
                        after={"slpra_iri": e.slpra_iri, "label": e.label},
                    )
                )
        self.db.commit()
        self.db.refresh(r)
        self.audit("release.create", None, actor, release_id=r.id, details={"no": release_no})
        return self.release_detail(str(r.id))

    def submit_release(self, rid: str, actor: str) -> dict:
        r = self._require_release(rid)
        if r.status != STATUS_DRAFT:
            raise HTTPException(status_code=409, detail="仅 draft 可提交审核")
        report = self.validate()
        r.validation_report = report
        if report["blocking"]:
            self.db.commit()
            raise HTTPException(status_code=409, detail="存在阻断校验项，无法提交")
        r.status = STATUS_IN_REVIEW
        self.db.commit()
        self.audit("release.submit", None, actor, release_id=r.id)
        return self.release_detail(rid)

    def publish_release(self, rid: str, actor: str) -> dict:
        r = self._require_release(rid)
        if r.status == STATUS_PUBLISHED:
            raise HTTPException(status_code=409, detail="批次已发布")
        if r.status != STATUS_IN_REVIEW:
            raise HTTPException(status_code=409, detail="仅 in_review 可发布")
        report = self.validate()
        if report["blocking"]:
            r.validation_report = report
            self.db.commit()
            raise HTTPException(status_code=409, detail="存在阻断校验项，无法发布")

        # 1) project to Owlready2 World (best effort)
        try:
            self.engine.project_entities(self._projection_payloads())
        except Exception as exc:  # pragma: no cover
            logger.warning("World projection failed: %s", exc)

        # 2) surgical TTL export + diff
        preview, added, removed = ttl_merge.export_diff(self.db, settings.ontology_dir)
        r.ttl_diff = preview

        # 3) write merged TTL + one Git commit (best effort)
        sha = self._write_and_commit(r.release_no, preview)
        r.ttl_commit_sha = sha

        # 4) finalise lifecycle
        r.validation_report = report
        r.published_at = _now()
        r.published_by = self._user_id(actor)
        r.status = STATUS_PUBLISHED
        for table, model in (
            ("ontology_class", OntologyClass),
            ("ontology_link_type", OntologyLinkType),
            ("ontology_data_property", OntologyDataProperty),
            ("ontology_action", OntologyAction),
        ):
            for e in self.db.query(model).filter_by(status=STATUS_DRAFT).all():
                e.status = STATUS_PUBLISHED
        self.db.commit()
        self.audit(
            "release.publish",
            None,
            actor,
            release_id=r.id,
            details={"sha": sha, "added": len(added), "removed": len(removed)},
        )
        return self.release_detail(rid)

    def rollback_release(self, rid: str, actor: str) -> dict:
        r = self._require_release(rid)
        if r.status != STATUS_IN_REVIEW:
            raise HTTPException(status_code=409, detail="仅 in_review 可退回 draft")
        r.status = STATUS_DRAFT
        self.db.commit()
        self.audit("release.rollback", None, actor, release_id=r.id)
        return self.release_detail(rid)

    def _projection_payloads(self) -> list[dict]:
        payloads: list[dict] = []
        for c in self.db.query(OntologyClass).filter_by(is_disabled=False).all():
            payloads.append(
                {
                    "kind": "class",
                    "iri": c.slpra_iri,
                    "label": c.label,
                    "comment": c.comment,
                    "parent_iri": self._iri_of_class(c.parent_class_id),
                    "module": c.module,
                }
            )
        for lt in self.db.query(OntologyLinkType).filter_by(is_disabled=False).all():
            payloads.append(
                {
                    "kind": "link_type",
                    "iri": lt.slpra_iri,
                    "label": lt.label,
                    "comment": lt.comment,
                    "domain_iri": self._iri_of_class(lt.domain_class_id),
                    "range_iri": self._iri_of_class(lt.range_class_id),
                }
            )
        for dp in self.db.query(OntologyDataProperty).filter_by(is_disabled=False).all():
            payloads.append(
                {
                    "kind": "data_property",
                    "iri": dp.slpra_iri,
                    "label": dp.label,
                    "comment": dp.comment,
                    "domain_iri": self._iri_of_class(dp.domain_class_id),
                }
            )
        return payloads

    def _write_and_commit(self, release_no: str, ttl: str) -> str | None:
        try:
            out_dir = Path(settings.ontology_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "slpra_managed.ttl"
            out_file.write_text(ttl, encoding="utf-8")
            subprocess.run(
                ["git", "add", str(out_file)],
                cwd=str(out_dir),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"release: {release_no} (T-Box 批次发布)"],
                cwd=str(out_dir),
                check=True,
                capture_output=True,
            )
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(out_dir),
                check=True,
                capture_output=True,
                text=True,
            )
            return res.stdout.strip()
        except Exception as exc:  # pragma: no cover - git optional in dev/test
            logger.warning("TTL Git commit skipped: %s", exc)
            return None

    # =================================================================== #
    # Seeding: project authoritative TTL into metadata (R6, T013)
    # =================================================================== #
    def project_from_ttl(self) -> int:
        """Idempotent upsert of authoritative TTL classes into the metadata
        tables (seed) so the existing axioms are editable in the workbench."""
        try:
            base = ttl_merge.load_base_graph(Path(settings.ontology_dir))
        except Exception as exc:  # pragma: no cover
            logger.warning("project_from_ttl: could not load TTL: %s", exc)
            return 0
        seeded = 0
        for s in set(base.subjects(RDF.type, OWL.Class)):
            if not isinstance(s, URIRef):
                continue
            iri = str(s)
            if not iri.startswith(MANAGED_PREFIX):
                continue
            if self._class_by_iri(iri):
                continue
            label = next(iter(base.objects(s, RDFS.label)), None)
            comment = next(iter(base.objects(s, RDFS.comment)), None)
            self.db.add(
                OntologyClass(
                    slpra_iri=iri,
                    label=str(label) if label else iri.rsplit("/", 1)[-1],
                    comment=str(comment) if comment else None,
                    status=STATUS_PUBLISHED,
                )
            )
            seeded += 1
        if seeded:
            self.db.commit()
        logger.info("project_from_ttl seeded %d classes", seeded)
        return seeded


class _Merged:
    """View over an existing restriction overlaid with a partial update payload,
    used to validate the *resulting* state before a CAS update."""

    def __init__(self, r: OntologyRestriction, payload, store: "OntologyMetaStore"):
        self.kind = payload.kind if payload.kind is not None else r.kind
        self.cardinality = (
            payload.cardinality if payload.cardinality is not None else r.cardinality
        )
        self.property_iri = (
            payload.property_iri
            if payload.property_iri is not None
            else store._property_iri(r.on_property_id)
        )
        self.filler_iri = (
            payload.filler_iri
            if payload.filler_iri is not None
            else store._iri_of_class(r.filler_class_id)
        )
