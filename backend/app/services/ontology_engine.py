"""OntologyEngine: loads SLPRA OWL modules via Owlready2, provides class/individual operations."""

from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import owlready2
import rdflib

from app.config import settings

logger = logging.getLogger(__name__)

MODULE_NAMES = {
    "drug": "https://ontology.pharma-gmp.cn/slpra/drug/",
    "equipment": "https://ontology.pharma-gmp.cn/slpra/equipment/",
    "contamination": "https://ontology.pharma-gmp.cn/slpra/contamination/",
    "risk": "https://ontology.pharma-gmp.cn/slpra/risk/",
    "cleaning": "https://ontology.pharma-gmp.cn/slpra/cleaning/",
    "facility": "https://ontology.pharma-gmp.cn/slpra/facility/",
    "personnel": "https://ontology.pharma-gmp.cn/slpra/personnel/",
    "document": "https://ontology.pharma-gmp.cn/slpra/document/",
    "drug-development": "https://ontology.pharma-gmp.cn/slpra/drug-development/",
    "integration": "https://ontology.pharma-gmp.cn/slpra/integration/",
}

# IRI → 本地 TTL 文件名。Owlready2 仅按 IRI 末段（如 "drug"）在 onto_path 中搜索文件，
# 与实际文件名（slpra-drug.ttl）不符且不读 catalog，故按文件路径显式离线加载（VR：禁联网）。
MODULE_FILES = {
    "drug": "slpra-drug.ttl",
    "equipment": "slpra-equipment.ttl",
    "contamination": "slpra-contamination.ttl",
    "risk": "slpra-risk.ttl",
    "cleaning": "slpra-cleaning.ttl",
    "facility": "slpra-facility.ttl",
    "personnel": "slpra-personnel.ttl",
    "document": "slpra-document.ttl",
    "drug-development": "slpra-drug-development.ttl",
    "integration": "slpra-integration.ttl",
}

# integration owl:imports 全部内部模块，故须最后加载（依赖先就位）。
_LOAD_ORDER = ["drug", "equipment", "contamination", "risk", "cleaning", "facility", "personnel", "document", "drug-development", "integration"]

# 外部上层本体（BFO）：随包提供的离线本地副本。各模块的类挂在 BFO 顶层范畴下，必须先于
# 模块加载，否则父范畴/父类 IRI 为空（owl:imports 在离线容器内无法联网解析）。
# IRI → 相对 ontology_dir 的本地文件路径。
_EXTERNAL_ONTOLOGIES = {
    "http://purl.obolibrary.org/obo/bfo.owl": "lib/bfo.ttl",
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
    bfo_category: str | None = None
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
            # 物化库为权威 TTL 的派生缓存（TTL 为唯一权威源，永不回写）。每次启动重建，
            # 既保证与权威 TTL 一致，又避免跨重启的三元组累积与历史失败遗留的空库。
            for stale in (self._store_path, Path(f"{self._store_path}-journal")):
                stale.unlink(missing_ok=True)
            self._world = owlready2.World(filename=str(self._store_path))
            owlready2.onto_path.insert(0, str(self._ontology_dir))

            # 先加载外部上层本体（BFO）的本地副本，使各模块的 BFO 父范畴可解析；
            # 缺失/损坏时降级为空桩并标记已加载，阻止 owl:imports 触发联网下载。
            for ext_iri, rel in _EXTERNAL_ONTOLOGIES.items():
                onto = self._world.get_ontology(ext_iri)
                ext_path = self._ontology_dir / rel
                try:
                    self._load_turtle(onto, ext_path)
                except Exception:
                    logger.warning(
                        "Upper ontology %s not loaded from %s; parent categories may be empty",
                        ext_iri, ext_path, exc_info=True,
                    )
                    onto.loaded = True

            # 按文件路径离线加载各模块（integration 末位以解析内部导入）。
            for key in _LOAD_ORDER:
                iri = MODULE_NAMES[key]
                path = self._ontology_dir / MODULE_FILES[key]
                try:
                    onto = self._world.get_ontology(iri)
                    self._load_turtle(onto, path)
                    self._ontologies[key] = onto
                except Exception:
                    logger.warning("Could not load module %s from %s", key, path, exc_info=True)

            self.is_loaded = True
            total = sum(len(list(o.classes())) for o in self._ontologies.values())
            logger.info("Loaded %d modules with %d classes total", len(self._ontologies), total)

    @staticmethod
    def _load_turtle(onto: owlready2.Ontology, path: Path) -> None:
        """离线加载 Turtle 本体到既有 owlready2 ontology 对象。模块以 Turtle 编写，而
        owlready2 仅原生解析 rdfxml/owlxml/ntriples，故先用 rdflib 转为 RDF/XML 再喂给
        owlready2（纯内存转换，only_local 禁联网，不回写权威 TTL）。"""
        graph = rdflib.Graph()
        graph.parse(str(path), format="turtle")
        rdfxml = graph.serialize(format="xml", encoding="utf-8")
        onto.load(fileobj=io.BytesIO(rdfxml), only_local=True, format="rdfxml")

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
                labels = list(onto.label or [])  # 本体无 rdfs:label 标注时 owlready2 返回 None
                label = None
                for lbl in labels:
                    if hasattr(lbl, "lang") and lbl.lang == "zh":
                        label = str(lbl)
                        break
                if not label:
                    label = str(labels[0]) if labels else key
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
            bfo_category = self._bfo_category(cls)

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
                bfo_category=bfo_category,
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

    def get_data_properties_by_domain(self, class_iri: str) -> list[dict]:
        """返回 rdfs:domain 包含此类的数据属性 ``[{iri, name, label}]``。

        规避 ``get_class_properties()`` 不返回 ``rdfs:domain`` 声明属性的 bug；
        与 ``data_property_domain_classes()`` 同机制，但按单类过滤。
        """
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                return []
            props: list[dict] = []
            seen: set[str] = set()
            for prop in self._world.data_properties():
                if cls in prop.domain and prop.iri not in seen:
                    seen.add(prop.iri)
                    props.append({
                        "iri": prop.iri,
                        "name": prop.name,
                        "label": self._get_label(prop) or prop.name,
                    })
            return props

    def get_object_properties_by_domain(self, class_iri: str) -> list[dict]:
        """返回 rdfs:domain 包含此类的对象属性 ``[{iri, name, label, range}]``。

        规避 ``get_class_properties()`` 不返回 ``rdfs:domain`` 声明属性的 bug（对数据
        属性与对象属性同样适用）；与 ``get_data_properties_by_domain`` 同机制，但遍历
        ``object_properties()`` 并多返回 ``range`` 的类 IRI 列表（供关系抽取按 range 选端点）。
        """
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                return []
            props: list[dict] = []
            seen: set[str] = set()
            for prop in self._world.object_properties():
                if cls in prop.domain and prop.iri not in seen:
                    seen.add(prop.iri)
                    props.append({
                        "iri": prop.iri,
                        "name": prop.name,
                        "label": self._get_label(prop) or prop.name,
                        "range": [getattr(r, "iri", str(r)) for r in prop.range],
                    })
            return props

    def get_subclasses(self, class_iri: str, recursive: bool = True) -> list[dict]:
        """返回某类的子类 ``[{iri, label}]``（默认递归全部后代，不含自身）。

        供文档级分类枚举 ``RegulatoryDocument`` 的全部候选文档类型（关系抽取第一步）。
        ``recursive=False`` 时仅返回直接子类。去重，按发现顺序保序。
        """
        with self._lock:
            cls = self._world.search_one(iri=class_iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                return []
            descendants = (
                cls.descendants(include_self=False) if recursive else cls.subclasses()
            )
            out: list[dict] = []
            seen: set[str] = set()
            for c in descendants:
                if not isinstance(c, owlready2.ThingClass) or c.iri in seen:
                    continue
                seen.add(c.iri)
                out.append({"iri": c.iri, "label": self._get_label(c) or c.name})
            return out

    def get_relation_schema(
        self, class_iri: str, max_hops: int = 4,
    ) -> list[dict]:
        """从指定类出发 BFS，返回多跳关系图谱 schema（纯 T-Box 结构查询）。

        每条记录::

            {hop, predicate_iri, predicate_label,
             domain_class_iri, domain_class_label,
             range_class_iri, range_class_label,
             range_subclasses: [{iri, label}],
             range_data_properties: [{iri, label}]}

        用途：给定文档类（如 CMCReport），展示其完整的关系图谱模板——
        实体类型 + 属性三元组均可从此结构推导，无需跑 NER。
        """
        with self._lock:
            if not self._world:
                return []
            root = self._world.search_one(iri=class_iri)
            if root is None or not isinstance(root, owlready2.ThingClass):
                return []

            all_obj_props = list(self._world.object_properties())
            all_data_props = list(self._world.data_properties())

            def _obj_props_for(cls):
                props = []
                seen = set()
                for prop in all_obj_props:
                    if cls in prop.domain and prop.iri not in seen:
                        seen.add(prop.iri)
                        props.append(prop)
                return props

            def _data_props_for(cls):
                props = []
                seen = set()
                for prop in all_data_props:
                    if cls in prop.domain and prop.iri not in seen:
                        seen.add(prop.iri)
                        props.append(prop)
                return props

            edges: list[dict] = []
            visited_ranges: set[str] = set()
            frontier_iris = {class_iri}

            for hop in range(1, max_hops + 1):
                next_frontier: set[str] = set()
                for domain_iri in frontier_iris:
                    domain_cls = self._world.search_one(iri=domain_iri)
                    if domain_cls is None or not isinstance(domain_cls, owlready2.ThingClass):
                        continue
                    domain_label = self._get_label(domain_cls) or domain_cls.name
                    for prop in _obj_props_for(domain_cls):
                        pred_label = self._get_label(prop) or prop.name
                        for rng in prop.range:
                            rng_iri = getattr(rng, "iri", None)
                            if not rng_iri or rng_iri in visited_ranges:
                                continue
                            visited_ranges.add(rng_iri)
                            rng_label = self._get_label(rng) or getattr(rng, "name", rng_iri)
                            subs = []
                            for c in rng.descendants(include_self=False):
                                if isinstance(c, owlready2.ThingClass):
                                    subs.append({
                                        "iri": c.iri,
                                        "label": self._get_label(c) or c.name,
                                    })
                                    visited_ranges.add(c.iri)
                            dps = []
                            for dp in _data_props_for(rng):
                                dps.append({
                                    "iri": dp.iri,
                                    "label": self._get_label(dp) or dp.name,
                                })
                            edges.append({
                                "hop": hop,
                                "predicate_iri": prop.iri,
                                "predicate_label": pred_label,
                                "domain_class_iri": domain_iri,
                                "domain_class_label": domain_label,
                                "range_class_iri": rng_iri,
                                "range_class_label": rng_label,
                                "range_subclasses": subs,
                                "range_data_properties": dps,
                            })
                            next_frontier.add(rng_iri)
                            for s in subs:
                                next_frontier.add(s["iri"])
                if not next_frontier:
                    break
                frontier_iris = next_frontier

            return edges

    def data_property_labels(self) -> list[str]:
        """返回所有数据属性的标签（优先中文），供 NER 种子标签第三源使用。"""
        with self._lock:
            if not self._world:
                return []
            labels: list[str] = []
            seen: set[str] = set()
            for prop in self._world.data_properties():
                lbl = self._get_label(prop)
                if lbl and lbl not in seen:
                    seen.add(lbl)
                    labels.append(lbl)
            return labels

    def data_property_domain_classes(self) -> list[tuple[str, str]]:
        """返回声明为某数据属性 rdfs:domain 的类 ``(iri, label)`` 列表（NER 种子用）。

        注：owlready2 ``cls.get_class_properties()`` **不**返回经 ``rdfs:domain`` 声明
        的数据属性（仅返回注解/限制属性），故 ``get_class_detail`` 的属性面板对这些类为空、
        NER 标签退化为类名兜底。此处直接按属性 domain 反查规避；根因修复留待后续 feature
        （见 document_annotator 的告警记录）。返回去重、按首次出现保序。
        """
        with self._lock:
            if not self._world:
                return []
            out: dict[str, str] = {}
            for prop in self._world.data_properties():
                for dom in prop.domain:
                    iri = getattr(dom, "iri", None)
                    if iri and iri not in out:
                        out[iri] = self._get_label(dom) or getattr(dom, "name", iri)
            return list(out.items())

    # ----------------------------------------------------------------- #
    # T-Box write methods (R1, FR-001..005) — used at publish time to
    # project the editable metadata into the Owlready2 World. Best-effort:
    # callers wrap in try/except since the World is a publish-time artefact.
    # ----------------------------------------------------------------- #
    def _target_namespace(self, module: str | None) -> owlready2.Ontology:
        if module and module in self._ontologies:
            return self._ontologies[module]
        return next(iter(self._ontologies.values()))

    def upsert_class(
        self,
        iri: str,
        label: str | None = None,
        comment: str | None = None,
        parent_iri: str | None = None,
        module: str | None = None,
    ) -> None:
        onto = self._target_namespace(module)
        parent = self._world.search_one(iri=parent_iri) if parent_iri else None
        bases = (parent,) if isinstance(parent, owlready2.ThingClass) else (owlready2.Thing,)
        with onto:
            cls = self._world.search_one(iri=iri)
            if cls is None or not isinstance(cls, owlready2.ThingClass):
                name = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                cls = owlready2.types.new_class(name, bases)
                cls.iri = iri
            if isinstance(parent, owlready2.ThingClass) and parent not in cls.is_a:
                cls.is_a.append(parent)
            self._apply_labels(cls, label, comment)

    def upsert_link_type(
        self,
        iri: str,
        label: str | None = None,
        comment: str | None = None,
        domain_iri: str | None = None,
        range_iri: str | None = None,
        module: str | None = None,
        **flags,
    ) -> None:
        onto = self._target_namespace(module)
        with onto:
            prop = self._world.search_one(iri=iri)
            if prop is None:
                name = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                prop = owlready2.types.new_class(name, (owlready2.ObjectProperty,))
                prop.iri = iri
            dom = self._world.search_one(iri=domain_iri) if domain_iri else None
            rng = self._world.search_one(iri=range_iri) if range_iri else None
            if isinstance(dom, owlready2.ThingClass):
                prop.domain = [dom]
            if isinstance(rng, owlready2.ThingClass):
                prop.range = [rng]
            self._apply_labels(prop, label, comment)

    def upsert_data_property(
        self,
        iri: str,
        label: str | None = None,
        comment: str | None = None,
        domain_iri: str | None = None,
        module: str | None = None,
        **kwargs,
    ) -> None:
        onto = self._target_namespace(module)
        with onto:
            prop = self._world.search_one(iri=iri)
            if prop is None:
                name = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                prop = owlready2.types.new_class(name, (owlready2.DataProperty,))
                prop.iri = iri
            dom = self._world.search_one(iri=domain_iri) if domain_iri else None
            if isinstance(dom, owlready2.ThingClass):
                prop.domain = [dom]
            self._apply_labels(prop, label, comment)

    def delete_entity(self, iri: str) -> None:
        ent = self._world.search_one(iri=iri)
        if ent is not None:
            owlready2.destroy_entity(ent)

    def _apply_labels(self, entity, label: str | None, comment: str | None) -> None:
        if label:
            entity.label = [owlready2.locstr(label, lang="zh")]
        if comment:
            entity.comment = [owlready2.locstr(comment, lang="zh")]

    def project_entities(self, entities: list[dict]) -> None:
        """Project a list of metadata payloads into the World, then persist.

        Each payload is a dict with a ``kind`` discriminator. Per-entity errors
        are logged and skipped so one bad axiom never aborts a whole release.
        """
        with self._lock:
            if not self._world or not self._ontologies:
                logger.warning("World not loaded; skipping projection")
                return
            for ent in entities:
                kind = ent.get("kind")
                try:
                    if kind == "class":
                        self.upsert_class(**{k: v for k, v in ent.items() if k != "kind"})
                    elif kind == "link_type":
                        self.upsert_link_type(**{k: v for k, v in ent.items() if k != "kind"})
                    elif kind == "data_property":
                        self.upsert_data_property(
                            **{k: v for k, v in ent.items() if k != "kind"}
                        )
                    # actions/restrictions are projected via TTL, not the World
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("Projection failed for %s: %s", ent.get("iri"), exc)
            try:
                self._world.save()
            except Exception as exc:  # pragma: no cover
                logger.warning("World save failed: %s", exc)

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

    def _bfo_category(self, cls) -> str | None:
        """派生 BFO 上层范畴：自类沿 is_a 向上广度优先，返回最近的 BFO_xxxx 祖先的标签。
        UI '基本' 页的 'BFO 范畴' 对只读引擎类显示此派生值（DB 元数据类则用其可编辑列）。
        无 BFO 祖先（如 integration 模块仅挂接 DRON/IDMP 等外部 IRI）时返回 None。"""
        seen: set = set()
        level = [p for p in cls.is_a if isinstance(p, owlready2.ThingClass)]
        while level:
            nxt = []
            for parent in level:
                if parent in seen:
                    continue
                seen.add(parent)
                iri = getattr(parent, "iri", "") or ""
                if parent.name.startswith("BFO_") or "/obo/BFO_" in iri:
                    return self._get_label(parent) or parent.name
                nxt.extend(
                    p for p in parent.is_a if isinstance(p, owlready2.ThingClass)
                )
            level = nxt
        return None

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
