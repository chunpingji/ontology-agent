"""KGStore: bridges Owlready2 ontology store with PostgreSQL shadow tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.entity_shadow import EntityShadow
from app.services.ontology_engine import IndividualInfo, OntologyEngine


class KGStore:
    def __init__(self, db: Session, onto_engine: OntologyEngine):
        self.db = db
        self.onto = onto_engine

    def sync_individual_to_shadow(self, info: IndividualInfo) -> EntityShadow:
        module = self._detect_module(info.class_iris)
        shadow = self.db.query(EntityShadow).filter(EntityShadow.iri == info.iri).first()
        if shadow is None:
            shadow = EntityShadow(
                iri=info.iri,
                class_iri=info.class_iris[0] if info.class_iris else "",
                label_zh=info.label_zh,
                label_en=info.label_en,
                module=module,
                properties_json=info.properties,
            )
            self.db.add(shadow)
        else:
            shadow.class_iri = info.class_iris[0] if info.class_iris else shadow.class_iri
            shadow.label_zh = info.label_zh
            shadow.label_en = info.label_en
            shadow.module = module
            shadow.properties_json = info.properties
        self.db.commit()
        self.db.refresh(shadow)
        return shadow

    def sync_all_individuals(self) -> int:
        all_individuals = self.onto.get_all_individuals()
        count = 0
        for info in all_individuals:
            self.sync_individual_to_shadow(info)
            count += 1
        return count

    def get_shadow(self, iri: str) -> EntityShadow | None:
        return self.db.query(EntityShadow).filter(EntityShadow.iri == iri).first()

    def delete_shadow(self, iri: str) -> None:
        self.db.query(EntityShadow).filter(EntityShadow.iri == iri).delete()
        self.db.commit()

    def search_entities(
        self,
        query: str | None = None,
        module: str | None = None,
        class_iri: str | None = None,
        development_phase: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EntityShadow], int]:
        q = self.db.query(EntityShadow)

        if module:
            q = q.filter(EntityShadow.module == module)
        if class_iri:
            q = q.filter(EntityShadow.class_iri == class_iri)
        # 按研发阶段检索（007 US3，FR-005/SC-008）：过滤 properties_json.hasDevelopmentPhase
        # ——复用既有影子表与查询，不新增检索框架。文档与派生实体同维过滤（C2.1/C2.2）。
        if development_phase:
            q = q.filter(
                EntityShadow.properties_json["hasDevelopmentPhase"].as_string()
                == development_phase
            )
        if query:
            pattern = f"%{query}%"
            q = q.filter(or_(
                EntityShadow.label_zh.ilike(pattern),
                EntityShadow.label_en.ilike(pattern),
                EntityShadow.iri.ilike(pattern),
            ))

        total = q.count()
        items = q.order_by(EntityShadow.id).offset((page - 1) * page_size).limit(page_size).all()
        return items, total

    def get_stats(self) -> dict[str, Any]:
        total = self.db.query(func.count(EntityShadow.id)).scalar() or 0
        by_module = (
            self.db.query(EntityShadow.module, func.count(EntityShadow.id))
            .group_by(EntityShadow.module)
            .all()
        )
        by_class = (
            self.db.query(EntityShadow.class_iri, func.count(EntityShadow.id))
            .group_by(EntityShadow.class_iri)
            .all()
        )
        return {
            "total_entities": total,
            "by_module": {mod: cnt for mod, cnt in by_module},
            "by_class": {cls: cnt for cls, cnt in by_class},
        }

    def _detect_module(self, class_iris: list[str]) -> str:
        module_prefixes = {
            "drug": "/slpra/drug/",
            "equipment": "/slpra/equipment/",
            "contamination": "/slpra/contamination/",
            "risk": "/slpra/risk/",
            "cleaning": "/slpra/cleaning/",
            "facility": "/slpra/facility/",
            "document": "/slpra/document/",
        }
        for cls_iri in class_iris:
            for mod, prefix in module_prefixes.items():
                if prefix in cls_iri:
                    return mod
        return "integration"
