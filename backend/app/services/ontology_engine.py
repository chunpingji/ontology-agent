"""OntologyEngine: loads SLPRA OWL modules via Owlready2, provides class/individual operations."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import owlready2

from app.config import settings

logger = logging.getLogger(__name__)

MODULE_NAMES = {
    "drug": "https://ontology.pharma-gmp.cn/slpra/drug/",
    "equipment": "https://ontology.pharma-gmp.cn/slpra/equipment/",
    "contamination": "https://ontology.pharma-gmp.cn/slpra/contamination/",
    "risk": "https://ontology.pharma-gmp.cn/slpra/risk/",
    "cleaning": "https://ontology.pharma-gmp.cn/slpra/cleaning/",
    "facility": "https://ontology.pharma-gmp.cn/slpra/facility/",
    "integration": "https://ontology.pharma-gmp.cn/slpra/integration/",
}


@dataclass
class ClassInfo:
    iri: str
    name: str
    label_zh: str | None = None
    label_en: str | None = None
    comment: str | None = None
    parent_iris: list[str] = field(default_factory=list)
    children_iris: list[str] = field(default_factory=list)
    module: str | None = None
    individual_count: int = 0
    object_properties: list[dict] = field(default_factory=list)
    data_properties: list[dict] = field(default_factory=list)
    restrictions: list[dict] = field(default_factory=list)


@dataclass
class IndividualInfo:
    iri: str
    name: str
    class_iris: list[str] = field(default_factory=list)
    label_zh: str | None = None
    label_en: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModuleInfo:
    key: str
    iri: str
    label: str | None = None
    class_count: int = 0
    individual_count: int = 0


@dataclass
class TreeNode:
    iri: str
    name: str
    label: str | None = None
    children: list[TreeNode] = field(default_factory=list)
    individual_count: int = 0


class OntologyEngine:
    def __init__(self, ontology_dir: Path | None = None, store_path: Path | None = None):
        self._ontology_dir = ontology_dir or settings.ontology_dir
        self._store_path = store_path or settings.owl_store_path
        self._lock = threading.Lock()
        self._world: owlready2.World | None = None
        self._ontologies: dict[str, owlready2.Ontology] = {}
        self.is_loaded = False

    @property
    def modules(self) -> dict[str, owlready2.Ontology]:
        return self._ontologies

    def load(self) -> None:
        with self._lock:
            if self.is_loaded:
                return
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._world = owlready2.World(filename=str(self._store_path))
            owlready2.onto_path.insert(0, str(self._ontology_dir))

            integration_iri = MODULE_NAMES["integration"]
            try:
                onto = self._world.get_ontology(integration_iri).load()
                self._ontologies["integration"] = onto
            except Exception:
                logger.warning("Could not load integration module, loading modules individually")

            for key, iri in MODULE_NAMES.items():
                if key in self._ontologies:
                    continue
                try:
                    onto = self._world.get_ontology(iri).load()
                    self._ontologies[key] = onto
                except Exception:
                    logger.warning("Could not load module %s (%s)", key, iri)

            self.is_loaded = True
            total = sum(len(list(o.classes())) for o in self._ontologies.values())
            logger.info("Loaded %d modules with %d classes total", len(self._ontologies), total)

    def close(self) -> None:
        with self._lock:
            if self._world:
                self._world.close()
                self._world = None
            self._ontologies.clear()
            self.is_loaded = False

    def get_modules(self) -> list[ModuleInfo]:
        with self._lock:
            result = []
            for key, iri in MODULE_NAMES.items():
                onto = self._ontologies.get(key)
                if not onto:
                    continue
                classes = list(onto.classes())
                individuals = list(onto.individuals())
                label = None
                for lbl in onto.label:
                    if hasattr(lbl, "lang") and lbl.lang == "zh":
                        label = str(lbl)
                        break
                if not label:
                    label = str(onto.label[0]) if onto.label else key
                result.append(ModuleInfo(
                    key=key, iri=iri, label=label,
                    class_count=len(classes), individual_count=len(individuals),
                ))
            return result

    def get_class_hierarchy(self, module_key: str) -> list[TreeNode]:
        with self._lock:
            onto = self._ontologies.get(module_key)
            if not onto:
                return []
            classes = list(onto.classes())
            roots = []
            for cls in classes:
                parents_in_module = [
                    p for p in cls.is_a
                    if isinstance(p, owlready2.ThingClass) and p in classes
                ]
                if not parents_in_module:
                    roots.append(cls)
            return [self._build_tree(cls, classes) for cls in roots]

    def _build_tree(self, cls: owlready2.ThingClass, module_classes: list) -> TreeNode:
        children_in_module = [
            c for c in cls.subclasses()
            if c in module_classes
        ]
        label = self._get_label(cls)
        individuals = list(cls.instances())
        return TreeNode(
            iri=cls.iri,
            name=cls.name,
            label=label,
            individual_count=len(individuals),
            children=[self._build_tree(c, module_classes) for c in children_in_module],
        )

    def get_class_detail(self, class_iri: str) -> ClassInfo | None:
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                return None

            label_zh, label_en = self._get_bilingual_labels(cls)
            comment = str(cls.comment[0]) if cls.comment else None
            module = self._find_module_for_class(cls)

            parent_iris = [
                p.iri for p in cls.is_a if isinstance(p, owlready2.ThingClass)
            ]
            children_iris = [c.iri for c in cls.subclasses()]

            obj_props = []
            data_props = []
            for prop in cls.get_class_properties():
                prop_info = {
                    "iri": prop.iri,
                    "name": prop.name,
                    "label": self._get_label(prop),
                }
                if isinstance(prop, owlready2.ObjectPropertyClass):
                    ranges = [r.iri for r in prop.range if hasattr(r, "iri")]
                    prop_info["range"] = ranges
                    obj_props.append(prop_info)
                elif isinstance(prop, owlready2.DataPropertyClass):
                    ranges = [str(r) for r in prop.range]
                    prop_info["range"] = ranges
                    data_props.append(prop_info)

            restrictions = []
            for restriction in cls.is_a:
                if isinstance(restriction, owlready2.Restriction):
                    r_info = {"property": restriction.property.name}
                    if restriction.type == owlready2.SOME:
                        r_info["type"] = "someValuesFrom"
                        if hasattr(restriction.value, "iri"):
                            r_info["value"] = restriction.value.iri
                    elif restriction.type == owlready2.ONLY:
                        r_info["type"] = "allValuesFrom"
                        if hasattr(restriction.value, "iri"):
                            r_info["value"] = restriction.value.iri
                    elif restriction.type == owlready2.EXACTLY:
                        r_info["type"] = "exactCardinality"
                        r_info["cardinality"] = restriction.cardinality
                    restrictions.append(r_info)

            return ClassInfo(
                iri=class_iri,
                name=cls.name,
                label_zh=label_zh,
                label_en=label_en,
                comment=comment,
                parent_iris=parent_iris,
                children_iris=children_iris,
                module=module,
                individual_count=len(list(cls.instances())),
                object_properties=obj_props,
                data_properties=data_props,
                restrictions=restrictions,
            )

    def get_individuals(self, class_iri: str) -> list[IndividualInfo]:
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                return []
            return [self._individual_to_info(ind) for ind in cls.instances()]

    def get_individual(self, iri: str) -> IndividualInfo | None:
        with self._lock:
            ind = self._world.search_one(iri=iri)
            if ind is None or isinstance(ind, owlready2.ThingClass):
                return None
            return self._individual_to_info(ind)

    def create_individual(
        self, class_iri: str, name: str, properties: dict[str, Any]
    ) -> IndividualInfo:
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                raise ValueError(f"Class not found: {class_iri}")

            onto = cls.namespace.ontology
            with onto:
                ind = cls(name)
                self._set_properties(ind, properties)
                self._world.save()

            return self._individual_to_info(ind)

    def update_individual(self, iri: str, properties: dict[str, Any]) -> IndividualInfo:
        with self._lock:
            ind = self._world.search_one(iri=iri)
            if ind is None or isinstance(ind, owlready2.ThingClass):
                raise ValueError(f"Individual not found: {iri}")

            onto = ind.namespace.ontology
            with onto:
                self._set_properties(ind, properties)
                self._world.save()

            return self._individual_to_info(ind)

    def delete_individual(self, iri: str) -> None:
        with self._lock:
            ind = self._world.search_one(iri=iri)
            if ind is None or isinstance(ind, owlready2.ThingClass):
                raise ValueError(f"Individual not found: {iri}")
            owlready2.destroy_entity(ind)
            self._world.save()

    def sparql_query(self, query: str) -> list[dict]:
        with self._lock:
            try:
                results = list(self._world.sparql(query))
            except Exception as e:
                raise ValueError(f"SPARQL error: {e}") from e

            if not results:
                return []

            columns = [f"col_{i}" for i in range(len(results[0]))]
            return [
                {col: self._serialize_value(val) for col, val in zip(columns, row)}
                for row in results
            ]

    def get_all_individuals(self) -> list[IndividualInfo]:
        with self._lock:
            individuals = []
            for onto in self._ontologies.values():
                for ind in onto.individuals():
                    individuals.append(self._individual_to_info(ind))
            return individuals

    def _individual_to_info(self, ind) -> IndividualInfo:
        label_zh, label_en = self._get_bilingual_labels(ind)
        class_iris = [cls.iri for cls in ind.is_a if isinstance(cls, owlready2.ThingClass)]
        props = {}
        for prop in ind.get_properties():
            values = getattr(ind, prop.python_name, [])
            if not isinstance(values, list):
                values = [values]
            serialized = [self._serialize_value(v) for v in values]
            props[prop.iri] = serialized[0] if len(serialized) == 1 else serialized

        return IndividualInfo(
            iri=ind.iri, name=ind.name,
            class_iris=class_iris,
            label_zh=label_zh, label_en=label_en,
            properties=props,
        )

    def _set_properties(self, ind, properties: dict[str, Any]) -> None:
        for prop_iri, value in properties.items():
            prop = self._world.search_one(iri=prop_iri)
            if prop is None:
                prop_name = prop_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                try:
                    setattr(ind, prop_name, value if isinstance(value, list) else [value])
                except Exception:
                    logger.warning("Could not set property %s", prop_iri)
                continue
            prop_name = prop.python_name
            if isinstance(value, list):
                setattr(ind, prop_name, value)
            else:
                setattr(ind, prop_name, [value])

    def _get_label(self, entity) -> str | None:
        if not hasattr(entity, "label") or not entity.label:
            return None
        for lbl in entity.label:
            if hasattr(lbl, "lang") and lbl.lang == "zh":
                return str(lbl)
        return str(entity.label[0])

    def _get_bilingual_labels(self, entity) -> tuple[str | None, str | None]:
        label_zh = label_en = None
        if hasattr(entity, "label"):
            for lbl in entity.label:
                lang = getattr(lbl, "lang", None)
                if lang == "zh":
                    label_zh = str(lbl)
                elif lang == "en":
                    label_en = str(lbl)
                elif label_en is None:
                    label_en = str(lbl)
        return label_zh, label_en

    def _find_module_for_class(self, cls) -> str | None:
        cls_ns = str(cls.namespace.base_iri) if cls.namespace else ""
        for key, iri in MODULE_NAMES.items():
            if cls_ns.startswith(iri) or iri.startswith(cls_ns):
                return key
        return None

    def _serialize_value(self, val) -> Any:
        if hasattr(val, "iri"):
            return {"iri": val.iri, "name": getattr(val, "name", str(val))}
        return str(val) if not isinstance(val, (int, float, bool, str)) else val


ontology_engine = OntologyEngine()
