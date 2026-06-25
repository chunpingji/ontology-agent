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
    RULE_GROUPS,
    STATUS_DRAFT,
    STATUS_IN_REVIEW,
    STATUS_PUBLISHED,
    AppUser,
    OntologyAction,
    OntologyChangeLog,
    OntologyClass,
    OntologyClassificationCriterion,
    OntologyClassMapping,
    OntologyConflictPolicy,
    OntologyDataProperty,
    OntologyDecisionRule,
    OntologyLinkType,
    OntologyRelease,
    OntologyRestriction,
)
from app.models.reasoning import AuditLog
from app.services import ttl_merge
from app.services.reasoning import interpreter
from app.services.reasoning.defaults import (
    VERIFIED_EXTERNAL_ALIGNMENTS,
    ClassificationCriterion as CriterionSpec,
    ConflictPolicy as ConflictPolicySpec,
    DecisionRule as DecisionRuleSpec,
)
from app.services.reasoning.seed_declarative import (
    CONFLICT_POLICY_PREFIX,
    DECISION_RULE_PREFIX,
)

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


def _classification_pattern_refs(node: dict) -> tuple[set[str], set[str]]:
    """Collect (property names, class names) a classification pattern references,
    for the release consistency gate (T018). Recurses through and/or operands.
    `external_alignment` references an external IRI (not a managed local name),
    so its alignment is checked by the US2 `alignment_verified` gate, not here."""
    props: set[str] = set()
    classes: set[str] = set()
    op = node.get("op")
    if op in ("and", "or"):
        for child in node.get("operands", []):
            p, c = _classification_pattern_refs(child)
            props |= p
            classes |= c
    elif op == "some_values_from":
        props.add(node["property"])
        classes.add(node["filler_class"])
    elif op == "class_membership":
        props.add(node["property"])
        classes.update(node.get("classes", []))
    elif op in ("datatype_facet", "boolean_has_value"):
        props.add(node["property"])
    return props, classes


def _classification_alignment_refs(node: dict) -> set[str]:
    """Collect the external alignment IRIs an `external_alignment` pattern names,
    for the US2 release gate (T026 / FR-014). Recurses through and/or operands.
    Unlike managed local names, an alignment IRI must have been byte-verified
    against its authoritative source (research.md R3) before the criterion may
    project an axiom onto a managed class — the gate blocks any IRI absent from
    `defaults.VERIFIED_EXTERNAL_ALIGNMENTS`."""
    out: set[str] = set()
    op = node.get("op")
    if op in ("and", "or"):
        for child in node.get("operands", []):
            out |= _classification_alignment_refs(child)
    elif op == "external_alignment":
        align = node.get("alignment")
        if align:
            out.add(align)
    return out


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
    def list_link_types(
        self, domain_iri: str | None = None, include_inherited: bool = False
    ) -> list[dict]:
        """列出（未停用的）对象属性 / 关系；给定 domain_iri 时仅返回 domain 挂接该类的关系。
        include_inherited=True 时沿父类链合并继承自祖先类的关系（标注 inherited_from_*）。"""
        q = self.db.query(OntologyLinkType).filter_by(is_disabled=False)
        if not domain_iri:
            return [self.link_type_detail(lt) for lt in q.all()]
        cls = self._class_by_iri(domain_iri)
        if cls is None:
            return []
        if not include_inherited:
            rows = q.filter_by(domain_class_id=cls.id).all()
            return [self.link_type_detail(lt) for lt in rows]
        chain = self._ancestor_chain(cls)
        rows = q.filter(OntologyLinkType.domain_class_id.in_(list(chain))).all()
        return [
            self._annotate_inherited(self.link_type_detail(lt), lt.domain_class_id, cls.id, chain)
            for lt in rows
        ]

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
    def list_data_properties(
        self, domain_iri: str | None = None, include_inherited: bool = False
    ) -> list[dict]:
        """列出（未停用的）数据属性；给定 domain_iri 时仅返回挂接该类的属性。
        include_inherited=True 时沿父类链合并继承自祖先类的属性（标注 inherited_from_*）。"""
        q = self.db.query(OntologyDataProperty).filter_by(is_disabled=False)
        if not domain_iri:
            return [self.data_property_detail(dp) for dp in q.all()]
        cls = self._class_by_iri(domain_iri)
        if cls is None:
            return []
        if not include_inherited:
            rows = q.filter_by(domain_class_id=cls.id).all()
            return [self.data_property_detail(dp) for dp in rows]
        chain = self._ancestor_chain(cls)
        rows = q.filter(OntologyDataProperty.domain_class_id.in_(list(chain))).all()
        return [
            self._annotate_inherited(self.data_property_detail(dp), dp.domain_class_id, cls.id, chain)
            for dp in rows
        ]

    def _ancestor_chain(self, cls: OntologyClass) -> dict:
        """{class_id: OntologyClass} for cls and all ancestors via parent_class_id
        (cycle-safe)."""
        chain: dict = {}
        cur: OntologyClass | None = cls
        while cur is not None and cur.id not in chain:
            chain[cur.id] = cur
            cur = self.db.get(OntologyClass, cur.parent_class_id) if cur.parent_class_id else None
        return chain

    @staticmethod
    def _annotate_inherited(detail: dict, owner_id, self_id, chain: dict) -> dict:
        """Tag a property detail with the ancestor it is inherited from (None when
        declared directly on the queried class)."""
        if owner_id != self_id:
            owner = chain.get(owner_id)
            detail["inherited_from_iri"] = owner.slpra_iri if owner else None
            detail["inherited_from_label"] = owner.label if owner else None
        return detail

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
    # 声明式规则层 (能力六 / spec 006) — E11/E12/E13 可版本化规则数据
    #
    # The metadata rows are the draft source of truth: edits CAS-bump `version`
    # (→409 on conflict, R4) and reset `status=draft` so they (a) are read
    # immediately by the engine via the `active_*` loaders below (US3/FR-016:
    # change a threshold or add a rule = pure data, no source change) and (b)
    # land in the next release batch (T040). `publish_release` flips them back
    # to published; the surgical merge (T032) projects them into the TTL.
    # =================================================================== #

    # --- E11 classification-criterion (defined criteria) -------------------
    def classification_criterion_detail(self, c: OntologyClassificationCriterion) -> dict:
        target = self.db.get(OntologyClass, c.target_class_id) if c.target_class_id else None
        return {
            "id": str(c.id),
            "criterion_key": c.criterion_key,
            "target_class_iri": target.slpra_iri if target else None,
            "target_class_label": target.label if target else None,
            "pattern": c.pattern,
            "regulation_ref": c.regulation_ref,
            "logic_role": c.logic_role,
            "status": c.status,
            "version": c.version,
            "is_disabled": c.is_disabled,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }

    def _require_criterion(self, criterion_key: str) -> OntologyClassificationCriterion:
        c = (
            self.db.query(OntologyClassificationCriterion)
            .filter_by(criterion_key=criterion_key)
            .first()
        )
        if not c:
            raise HTTPException(status_code=404, detail=f"分类判据不存在：{criterion_key}")
        return c

    def list_classification_criteria(self) -> list[dict]:
        return [
            self.classification_criterion_detail(c)
            for c in self.db.query(OntologyClassificationCriterion)
            .order_by(OntologyClassificationCriterion.criterion_key)
            .all()
        ]

    def create_classification_criterion(self, payload, actor: str) -> dict:
        if (
            self.db.query(OntologyClassificationCriterion)
            .filter_by(criterion_key=payload.criterion_key)
            .first()
        ):
            raise HTTPException(status_code=400, detail="criterion_key 已存在")
        target = self._require_class(payload.target_class_iri)
        uid = self._user_id(actor)
        c = OntologyClassificationCriterion(
            criterion_key=payload.criterion_key,
            target_class_id=target.id,
            logic_role=payload.logic_role,
            pattern=payload.pattern,
            regulation_ref=payload.regulation_ref,
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(c)
        self.db.commit()
        self.db.refresh(c)
        self.audit(
            "classification_criterion.create",
            payload.criterion_key,
            actor,
            details={"target_class_iri": target.slpra_iri},
        )
        return self.classification_criterion_detail(c)

    def update_classification_criterion(self, criterion_key: str, payload, actor: str) -> dict:
        c = self._require_criterion(criterion_key)
        changes: dict = {"updated_by": self._user_id(actor), "status": STATUS_DRAFT}
        if payload.target_class_iri is not None:
            changes["target_class_id"] = self._require_class(payload.target_class_iri).id
        for f in ("pattern", "regulation_ref", "logic_role", "is_disabled"):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        c = self._cas_update(
            OntologyClassificationCriterion, c.id, payload.expected_version, changes
        )
        self.audit("classification_criterion.update", criterion_key, actor)
        return self.classification_criterion_detail(c)

    def delete_classification_criterion(
        self, criterion_key: str, expected_version: int, actor: str
    ) -> None:
        c = self._require_criterion(criterion_key)
        self._cas_update(
            OntologyClassificationCriterion,
            c.id,
            expected_version,
            {"is_disabled": True, "status": STATUS_DRAFT},
        )
        self.audit("classification_criterion.delete", criterion_key, actor)

    # --- E12 decision-rule (production rules R-ED / R-SC / R-CP) -----------
    def decision_rule_detail(self, r: OntologyDecisionRule) -> dict:
        return {
            "id": str(r.id),
            "slpra_iri": r.slpra_iri,
            "rule_key": r.rule_key,
            "rule_group": r.rule_group,
            "antecedent": r.antecedent,
            "consequent": r.consequent,
            "priority": r.priority,
            "regulation_ref": r.regulation_ref,
            "label": r.label,
            "comment": r.comment,
            "status": r.status,
            "version": r.version,
            "is_disabled": r.is_disabled,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }

    def _require_decision_rule(self, rule_key: str) -> OntologyDecisionRule:
        r = self.db.query(OntologyDecisionRule).filter_by(rule_key=rule_key).first()
        if not r:
            raise HTTPException(status_code=404, detail=f"决策规则不存在：{rule_key}")
        return r

    def list_decision_rules(self, rule_group: str | None = None) -> list[dict]:
        q = self.db.query(OntologyDecisionRule)
        if rule_group:
            q = q.filter_by(rule_group=rule_group)
        return [
            self.decision_rule_detail(r)
            for r in q.order_by(
                OntologyDecisionRule.rule_group,
                OntologyDecisionRule.priority,
                OntologyDecisionRule.rule_key,
            ).all()
        ]

    def create_decision_rule(self, payload, actor: str) -> dict:
        if payload.rule_group not in RULE_GROUPS:
            raise HTTPException(
                status_code=400,
                detail=f"rule_group 必须是 {', '.join(RULE_GROUPS)} 之一",
            )
        if self.db.query(OntologyDecisionRule).filter_by(rule_key=payload.rule_key).first():
            raise HTTPException(status_code=400, detail="rule_key 已存在")
        slpra_iri = DECISION_RULE_PREFIX + payload.rule_key
        uid = self._user_id(actor)
        r = OntologyDecisionRule(
            slpra_iri=slpra_iri,
            label=payload.label or payload.rule_key,
            comment=payload.comment,
            rule_key=payload.rule_key,
            rule_group=payload.rule_group,
            antecedent=payload.antecedent,
            consequent=payload.consequent,
            priority=payload.priority,
            regulation_ref=payload.regulation_ref,
            created_by=uid,
            updated_by=uid,
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        self.audit("decision_rule.create", slpra_iri, actor, details={"rule_key": r.rule_key})
        return self.decision_rule_detail(r)

    def update_decision_rule(self, rule_key: str, payload, actor: str) -> dict:
        r = self._require_decision_rule(rule_key)
        if payload.rule_group is not None and payload.rule_group not in RULE_GROUPS:
            raise HTTPException(
                status_code=400,
                detail=f"rule_group 必须是 {', '.join(RULE_GROUPS)} 之一",
            )
        changes: dict = {"updated_by": self._user_id(actor), "status": STATUS_DRAFT}
        for f in (
            "rule_group",
            "antecedent",
            "consequent",
            "priority",
            "regulation_ref",
            "label",
            "comment",
            "is_disabled",
        ):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        r = self._cas_update(OntologyDecisionRule, r.id, payload.expected_version, changes)
        self.audit("decision_rule.update", r.slpra_iri, actor)
        return self.decision_rule_detail(r)

    def delete_decision_rule(self, rule_key: str, expected_version: int, actor: str) -> None:
        r = self._require_decision_rule(rule_key)
        self._cas_update(
            OntologyDecisionRule,
            r.id,
            expected_version,
            {"is_disabled": True, "status": STATUS_DRAFT},
        )
        self.audit("decision_rule.delete", r.slpra_iri, actor)

    # --- E13 conflict-policy (fixed dimension set; GET / PUT only) ----------
    def conflict_policy_detail(self, p: OntologyConflictPolicy) -> dict:
        return {
            "id": str(p.id),
            "slpra_iri": p.slpra_iri,
            "dimension": p.dimension,
            "strategy": p.strategy,
            "priority_lattice": p.priority_lattice,
            "override_direction": p.override_direction,
            "regulation_ref": p.regulation_ref,
            "label": p.label,
            "comment": p.comment,
            "status": p.status,
            "version": p.version,
            "is_disabled": p.is_disabled,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }

    def _require_conflict_policy(self, dimension: str) -> OntologyConflictPolicy:
        p = self.db.query(OntologyConflictPolicy).filter_by(dimension=dimension).first()
        if not p:
            raise HTTPException(status_code=404, detail=f"冲突消解策略不存在：{dimension}")
        return p

    def list_conflict_policies(self) -> list[dict]:
        return [
            self.conflict_policy_detail(p)
            for p in self.db.query(OntologyConflictPolicy)
            .order_by(OntologyConflictPolicy.dimension)
            .all()
        ]

    def get_conflict_policy(self, dimension: str) -> dict:
        return self.conflict_policy_detail(self._require_conflict_policy(dimension))

    def update_conflict_policy(self, dimension: str, payload, actor: str) -> dict:
        p = self._require_conflict_policy(dimension)
        changes: dict = {"updated_by": self._user_id(actor), "status": STATUS_DRAFT}
        for f in (
            "strategy",
            "priority_lattice",
            "override_direction",
            "regulation_ref",
            "comment",
            "is_disabled",
        ):
            v = getattr(payload, f, None)
            if v is not None:
                changes[f] = v
        p = self._cas_update(OntologyConflictPolicy, p.id, payload.expected_version, changes)
        self.audit("conflict_policy.update", p.slpra_iri, actor)
        return self.conflict_policy_detail(p)

    # --- active-state loaders (engine consumes these at assessment time) ----
    # ORM rows → the in-code `defaults.*` dataclasses the engine already speaks,
    # so the assessment path is data-driven while `run_assessment`'s fallback to
    # the in-code defaults keeps golden-master parity (FR-012 / SC-004). Only
    # `is_disabled=False` rows participate (draft *and* published), mirroring the
    # surgical-merge projection (`build_managed_graph`).
    def active_classification_criteria(self) -> list[CriterionSpec]:
        specs: list[CriterionSpec] = []
        for c in (
            self.db.query(OntologyClassificationCriterion)
            .filter_by(is_disabled=False)
            .order_by(OntologyClassificationCriterion.criterion_key)
            .all()
        ):
            target_iri = self._iri_of_class(c.target_class_id)
            if not target_iri:
                continue  # dangling target (defensive) → skip
            specs.append(
                CriterionSpec(
                    key=c.criterion_key,
                    target_class=target_iri.rsplit("/", 1)[-1],
                    pattern=c.pattern,
                    regulation_ref=c.regulation_ref or "",
                    description=c.criterion_key,
                    logic_role=c.logic_role,
                )
            )
        return specs

    def active_decision_rules(self) -> list[DecisionRuleSpec]:
        return [
            DecisionRuleSpec(
                key=r.rule_key,
                rule_group=r.rule_group,
                antecedent=r.antecedent,
                consequent=r.consequent,
                regulation_ref=r.regulation_ref or "",
                description=r.comment or r.rule_key,
                priority=r.priority,
            )
            for r in self.db.query(OntologyDecisionRule).filter_by(is_disabled=False).all()
        ]

    def active_conflict_policies(self) -> list[ConflictPolicySpec]:
        return [
            ConflictPolicySpec(
                dimension=p.dimension,
                strategy=p.strategy,
                regulation_ref=p.regulation_ref or "",
                description=p.comment or p.dimension,
                priority_lattice=p.priority_lattice,
                override_direction=p.override_direction,
            )
            for p in self.db.query(OntologyConflictPolicy).filter_by(is_disabled=False).all()
        ]

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

        # E11 classification criteria (spec 006, T018 / FR-014): a defined
        # criterion projects an owl:equivalentClass axiom — block the release if
        # its target/pattern/referenced property/filler cannot be resolved, so an
        # unresolvable axiom never reaches the authoritative TTL.
        class_names = {
            c.slpra_iri.rsplit("/", 1)[-1] for c in classes
        }
        prop_names = {
            lt.slpra_iri.rsplit("/", 1)[-1]
            for lt in self.db.query(OntologyLinkType).filter_by(is_disabled=False).all()
        } | {
            dp.slpra_iri.rsplit("/", 1)[-1]
            for dp in self.db.query(OntologyDataProperty).filter_by(is_disabled=False).all()
        }
        for crit in (
            self.db.query(OntologyClassificationCriterion).filter_by(is_disabled=False).all()
        ):
            ckey = crit.criterion_key
            entity = f"criterion:{ckey}"
            target = self.db.get(OntologyClass, crit.target_class_id)
            if target is None or target.is_disabled:
                blocking.append({
                    "code": "criterion_target_unresolved",
                    "message": f"判据目标类不可解析/已停用：{ckey}",
                    "entity_iri": entity,
                })
            try:
                interpreter.validate_pattern(crit.pattern)
            except interpreter.PatternError as exc:
                blocking.append({
                    "code": "criterion_pattern_invalid",
                    "message": f"判据模式非法：{ckey}：{exc}",
                    "entity_iri": entity,
                })
                continue  # refs are unreliable on a malformed pattern
            ref_props, ref_classes = _classification_pattern_refs(crit.pattern)
            for p in sorted(ref_props - prop_names):
                blocking.append({
                    "code": "criterion_property_unresolved",
                    "message": f"判据引用属性不可解析：{ckey} → {p}",
                    "entity_iri": entity,
                })
            for c in sorted(ref_classes - class_names):
                blocking.append({
                    "code": "criterion_filler_unresolved",
                    "message": f"判据引用类不可解析：{ckey} → {c}",
                    "entity_iri": entity,
                })
            # US2 (T026 / FR-014, 宪章 II NON-NEGOTIABLE): an external_alignment
            # criterion projects an existential onto a managed class with an
            # external IRI filler — block release unless that IRI was byte-verified
            # (research.md R3); an unverified term must never reach authoritative TTL.
            for align in sorted(
                _classification_alignment_refs(crit.pattern) - VERIFIED_EXTERNAL_ALIGNMENTS
            ):
                blocking.append({
                    "code": "criterion_alignment_unverified",
                    "message": f"判据外部对齐未经字节级核实：{ckey} → {align}",
                    "entity_iri": entity,
                })

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
        # aggregate current draft editable entities into the change log. The
        # declarative rule layer (E12/E13, spec 006) is IRI-bearing like the
        # E1–E4 entities, so a draft rule/policy edit lands in the batch (T040).
        for model, table in (
            (OntologyClass, "ontology_class"),
            (OntologyLinkType, "ontology_link_type"),
            (OntologyDataProperty, "ontology_data_property"),
            (OntologyAction, "ontology_action"),
            (OntologyDecisionRule, "ontology_decision_rule"),
            (OntologyConflictPolicy, "ontology_conflict_policy"),
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
        # E11 criteria are not IRI-bearing (class expressions hung off a target
        # class); key the change log by `criterion_key` + target IRI instead.
        for c in (
            self.db.query(OntologyClassificationCriterion)
            .filter_by(status=STATUS_DRAFT)
            .all()
        ):
            kind = "disable" if c.is_disabled else ("create" if c.version == 1 else "update")
            self.db.add(
                OntologyChangeLog(
                    release_id=r.id,
                    entity_table="ontology_classification_criterion",
                    entity_id=c.id,
                    change_kind=kind,
                    after={
                        "criterion_key": c.criterion_key,
                        "target_class_iri": self._iri_of_class(c.target_class_id),
                    },
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
            ("ontology_decision_rule", OntologyDecisionRule),
            ("ontology_conflict_policy", OntologyConflictPolicy),
            ("ontology_classification_criterion", OntologyClassificationCriterion),
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
        """Idempotent upsert of authoritative TTL axioms into the metadata tables
        (seed) so the existing classes, object properties (relations) and data
        properties are editable in the workbench. Returns the total rows seeded."""
        try:
            base = ttl_merge.load_base_graph(Path(settings.ontology_dir))
        except Exception as exc:  # pragma: no cover
            logger.warning("project_from_ttl: could not load TTL: %s", exc)
            return 0

        classes = self._seed_classes(base)
        # Classes must be queryable (by IRI) before parents / link / data
        # properties resolve their references; flush the pending inserts first.
        self.db.flush()
        parents = self._link_class_parents(base)
        links = self._seed_link_types(base)
        data = self._seed_data_properties(base)

        total = classes + links + data
        if total or parents:
            self.db.commit()
        logger.info(
            "project_from_ttl seeded %d rows (%d classes, %d relations, %d data props), "
            "linked %d parents",
            total, classes, links, data, parents,
        )
        return total

    @staticmethod
    def _pick_label(graph, subject) -> str | None:
        """Prefer the Chinese label, then English, then any rdfs:label."""
        labels = list(graph.objects(subject, RDFS.label))
        if not labels:
            return None
        for lang in ("zh", "en"):
            for lit in labels:
                if getattr(lit, "language", None) == lang:
                    return str(lit)
        return str(labels[0])

    def _seed_classes(self, base) -> int:
        seeded = 0
        for s in set(base.subjects(RDF.type, OWL.Class)):
            if not isinstance(s, URIRef):
                continue
            iri = str(s)
            if not iri.startswith(MANAGED_PREFIX):
                continue
            if self._class_by_iri(iri):
                continue
            comment = next(iter(base.objects(s, RDFS.comment)), None)
            self.db.add(
                OntologyClass(
                    slpra_iri=iri,
                    label=self._pick_label(base, s) or iri.rsplit("/", 1)[-1],
                    comment=str(comment) if comment else None,
                    status=STATUS_PUBLISHED,
                )
            )
            seeded += 1
        return seeded

    def _link_class_parents(self, base) -> int:
        """Backfill OntologyClass.parent_class_id from rdfs:subClassOf so the
        editable metadata mirrors the TTL hierarchy (and subclasses can inherit
        ancestor properties). Only managed *named* superclasses are linked —
        anonymous owl:Restriction nodes and external (BFO) parents are skipped.
        Idempotent: only fills rows whose parent is still unset."""
        linked = 0
        unset = (
            self.db.query(OntologyClass)
            .filter(OntologyClass.parent_class_id.is_(None))
            .all()
        )
        for c in unset:
            for sup in base.objects(URIRef(c.slpra_iri), RDFS.subClassOf):
                if not isinstance(sup, URIRef):
                    continue  # anonymous restriction / equivalent-class axiom
                sup_iri = str(sup)
                if not sup_iri.startswith(MANAGED_PREFIX):
                    continue  # external upper ontology (e.g. BFO)
                parent = self._class_by_iri(sup_iri)
                if parent and parent.id != c.id:
                    c.parent_class_id = parent.id
                    linked += 1
                    break  # single-parent model: first managed superclass wins
        return linked

    def _seed_link_types(self, base) -> int:
        """Project owl:ObjectProperty → OntologyLinkType (relations)."""
        seeded = 0
        # First pass: create link types; collect owl:inverseOf for a second pass
        # once all link types exist (an inverse may be defined before its target).
        inverses: list[tuple[str, str]] = []
        for s in set(base.subjects(RDF.type, OWL.ObjectProperty)):
            if not isinstance(s, URIRef):
                continue
            iri = str(s)
            if not iri.startswith(MANAGED_PREFIX):
                continue
            if self.db.query(OntologyLinkType).filter_by(slpra_iri=iri).first():
                continue
            domain = next(iter(base.objects(s, RDFS.domain)), None)
            rng = next(iter(base.objects(s, RDFS.range)), None)
            comment = next(iter(base.objects(s, RDFS.comment)), None)
            self.db.add(
                OntologyLinkType(
                    slpra_iri=iri,
                    label=self._pick_label(base, s) or iri.rsplit("/", 1)[-1],
                    comment=str(comment) if comment else None,
                    domain_class_id=self._class_id_or_none(domain),
                    range_class_id=self._class_id_or_none(rng),
                    is_functional=(s, RDF.type, OWL.FunctionalProperty) in base,
                    is_symmetric=(s, RDF.type, OWL.SymmetricProperty) in base,
                    is_transitive=(s, RDF.type, OWL.TransitiveProperty) in base,
                    status=STATUS_PUBLISHED,
                )
            )
            seeded += 1
            inv = next(iter(base.objects(s, OWL.inverseOf)), None)
            if isinstance(inv, URIRef) and str(inv).startswith(MANAGED_PREFIX):
                inverses.append((iri, str(inv)))

        if seeded:
            self.db.flush()  # make new link types resolvable for inverse linkage
        for iri, inv_iri in inverses:
            lt = self.db.query(OntologyLinkType).filter_by(slpra_iri=iri).first()
            inv = self.db.query(OntologyLinkType).filter_by(slpra_iri=inv_iri).first()
            if lt and inv:
                lt.inverse_link_id = inv.id
        return seeded

    def _seed_data_properties(self, base) -> int:
        """Project owl:DatatypeProperty → OntologyDataProperty."""
        seeded = 0
        for s in set(base.subjects(RDF.type, OWL.DatatypeProperty)):
            if not isinstance(s, URIRef):
                continue
            iri = str(s)
            if not iri.startswith(MANAGED_PREFIX):
                continue
            if self.db.query(OntologyDataProperty).filter_by(slpra_iri=iri).first():
                continue
            domain = next(iter(base.objects(s, RDFS.domain)), None)
            rng = next(iter(base.objects(s, RDFS.range)), None)
            comment = next(iter(base.objects(s, RDFS.comment)), None)
            self.db.add(
                OntologyDataProperty(
                    slpra_iri=iri,
                    label=self._pick_label(base, s) or iri.rsplit("/", 1)[-1],
                    comment=str(comment) if comment else None,
                    domain_class_id=self._class_id_or_none(domain),
                    datatype=self._xsd_to_datatype(rng),
                    status=STATUS_PUBLISHED,
                )
            )
            seeded += 1
        return seeded

    def _class_id_or_none(self, iri) -> uuid.UUID | None:
        """Resolve a domain/range IRI to a seeded class id, or None if unmanaged
        / not present (seeding must never fail on a dangling reference)."""
        if not isinstance(iri, URIRef):
            return None
        c = self._class_by_iri(str(iri))
        return c.id if c else None

    @staticmethod
    def _xsd_to_datatype(rng) -> str:
        """Map an XSD range IRI to the workbench datatype vocabulary (DATATYPES)."""
        if not isinstance(rng, URIRef):
            return "string"
        local = str(rng).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        mapping = {
            "string": "string", "normalizedString": "string", "token": "string",
            "integer": "integer", "int": "integer", "long": "integer",
            "short": "integer", "nonNegativeInteger": "integer",
            "positiveInteger": "integer",
            "decimal": "decimal", "float": "decimal", "double": "decimal",
            "boolean": "boolean", "date": "date", "dateTime": "dateTime",
            "anyURI": "anyURI",
        }
        return mapping.get(local, "string")


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
