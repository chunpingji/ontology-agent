"""get_relation_schema 单测：验证 T-Box 多跳 BFS 关系图谱输出。

使用真实 OntologyEngine + 最小 TTL（3 个类 + 对象/数据属性），覆盖：
- 2 跳 BFS 输出正确的 hop/predicate/domain/range/子类/数据属性
- 边级去重：同一 (domain, predicate, range) 元组只出现一次
- 多路径：同一 range 经不同 (domain, predicate) 到达时均被发出
- max_hops 限制
- 不存在的 class_iri → 空列表
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ontology_engine import OntologyEngine

_MINI_TTL = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix : <http://test.example.com/mini#> .

<http://test.example.com/mini> a owl:Ontology .

:Report a owl:Class ;
    rdfs:label "报告"@zh .

:Route a owl:Class ;
    rdfs:label "合成路线"@zh .

:Step a owl:Class ;
    rdfs:subClassOf :Route ;
    rdfs:label "合成步骤"@zh .

:Equipment a owl:Class ;
    rdfs:label "设备"@zh .

:hasRoute a owl:ObjectProperty ;
    rdfs:domain :Report ;
    rdfs:range :Route ;
    rdfs:label "含合成路线"@zh .

:hasEquipment a owl:ObjectProperty ;
    rdfs:domain :Report ;
    rdfs:range :Equipment ;
    rdfs:label "直接设备"@zh .

:usesEquipment a owl:ObjectProperty ;
    rdfs:domain :Route ;
    rdfs:range :Equipment ;
    rdfs:label "使用设备"@zh .

:nextStep a owl:ObjectProperty ;
    rdfs:domain :Step ;
    rdfs:range :Step ;
    rdfs:label "下一步骤"@zh .

:processDesc a owl:DatatypeProperty ;
    rdfs:domain :Route ;
    rdfs:range xsd:string ;
    rdfs:label "工艺描述"@zh .

:yieldPct a owl:DatatypeProperty ;
    rdfs:domain :Route ;
    rdfs:range xsd:string ;
    rdfs:label "收率"@zh .

:equipName a owl:DatatypeProperty ;
    rdfs:domain :Equipment ;
    rdfs:range xsd:string ;
    rdfs:label "设备名称"@zh .
"""

REPORT_IRI = "http://test.example.com/mini#Report"
ROUTE_IRI = "http://test.example.com/mini#Route"
STEP_IRI = "http://test.example.com/mini#Step"
EQUIP_IRI = "http://test.example.com/mini#Equipment"


@pytest.fixture()
def engine(tmp_path):
    ont_dir = tmp_path / "ontology" / "slpra"
    ont_dir.mkdir(parents=True)
    (ont_dir / "mini.ttl").write_text(_MINI_TTL, encoding="utf-8")
    eng = OntologyEngine(ontology_dir=ont_dir.parent, store_path=tmp_path / "owl.sqlite3")

    import app.services.ontology_engine as oe
    orig_order = oe._LOAD_ORDER
    orig_names = oe.MODULE_NAMES.copy()
    orig_files = oe.MODULE_FILES.copy()

    oe._LOAD_ORDER = ["mini"]
    oe.MODULE_NAMES["mini"] = "http://test.example.com/mini"
    oe.MODULE_FILES["mini"] = "slpra/mini.ttl"
    try:
        eng.load()
        yield eng
    finally:
        eng.close()
        oe._LOAD_ORDER = orig_order
        oe.MODULE_NAMES.clear()
        oe.MODULE_NAMES.update(orig_names)
        oe.MODULE_FILES.clear()
        oe.MODULE_FILES.update(orig_files)


def test_hop1_returns_direct_relations(engine):
    """第 1 跳返回 Report → Route + Report → Equipment。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=1)
    assert len(edges) == 2
    route_edge = next(e for e in edges if e["range_class_iri"] == ROUTE_IRI)
    assert route_edge["hop"] == 1
    assert route_edge["predicate_label"] == "含合成路线"
    assert route_edge["domain_class_iri"] == REPORT_IRI
    assert route_edge["domain_class_label"] == "报告"
    assert route_edge["range_class_label"] == "合成路线"
    sub_iris = {s["iri"] for s in route_edge["range_subclasses"]}
    assert STEP_IRI in sub_iris
    dp_labels = {d["label"] for d in route_edge["range_data_properties"]}
    assert "工艺描述" in dp_labels
    assert "收率" in dp_labels

    equip_edge = next(e for e in edges if e["range_class_iri"] == EQUIP_IRI)
    assert equip_edge["predicate_label"] == "直接设备"


def test_hop2_traverses_deeper(engine):
    """第 2 跳从 Route/Step → Equipment。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=2)
    hop2 = [e for e in edges if e["hop"] == 2]
    assert len(hop2) >= 1
    equip_edge = next(e for e in hop2 if e["range_class_iri"] == EQUIP_IRI)
    assert equip_edge["predicate_label"] == "使用设备"
    dp_labels = {d["label"] for d in equip_edge["range_data_properties"]}
    assert "设备名称" in dp_labels


def test_max_hops_limits_depth(engine):
    """max_hops=1 只返回第 1 跳，不含 hop 2 的 Route→usesEquipment 边。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=1)
    assert all(e["hop"] == 1 for e in edges)
    assert not any(e["domain_class_iri"] == ROUTE_IRI for e in edges)


def test_unknown_class_returns_empty(engine):
    edges = engine.get_relation_schema("http://test.example.com/mini#NonExistent")
    assert edges == []


def test_edge_dedup(engine):
    """同一 (domain, predicate, range) 元组只出现一次。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=4)
    edge_keys = [(e["domain_class_iri"], e["predicate_iri"], e["range_class_iri"])
                 for e in edges]
    assert len(edge_keys) == len(set(edge_keys))


def test_subclass_domain_edges_discovered(engine):
    """range 子类作为 domain 被访问，其**直接声明**的出边被发出（Codex R1 回归）。

    Report --hasRoute--> Route（hop1）；Route 的子类 Step 应在 hop1 入队、hop2 作为
    domain 被访问，从而发出 Step --nextStep--> Step。修复前 Step 在 subs 循环里被提前
    标记 frontier_seen，入队循环恒跳过，此边被静默漏掉。
    """
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=2)
    step_edges = [e for e in edges if e["domain_class_iri"] == STEP_IRI]
    assert any(e["predicate_label"] == "下一步骤" for e in step_edges), (
        "range 子类 Step 的直接出边 nextStep 未被发现——子类 domain 遍历回归"
    )
    next_edge = next(e for e in step_edges if e["predicate_label"] == "下一步骤")
    assert next_edge["hop"] == 2
    assert next_edge["domain_class_label"] == "合成步骤"


def test_multipath_range_emitted(engine):
    """同一 range 经不同 (domain, predicate) 到达时均被发出。

    Equipment 在 hop 1 经 Report→hasEquipment 到达，
    在 hop 2 经 Route→usesEquipment 再次到达——两条边都应存在。
    """
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=2)
    equip_edges = [e for e in edges if e["range_class_iri"] == EQUIP_IRI]
    assert len(equip_edges) == 2
    preds = {e["predicate_label"] for e in equip_edges}
    assert preds == {"直接设备", "使用设备"}
    domains = {e["domain_class_iri"] for e in equip_edges}
    assert REPORT_IRI in domains
    assert ROUTE_IRI in domains
