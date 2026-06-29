"""get_relation_schema 单测：验证 T-Box 多跳 BFS 关系图谱输出。

使用真实 OntologyEngine + 最小 TTL（3 个类 + 对象/数据属性），覆盖：
- 2 跳 BFS 输出正确的 hop/predicate/domain/range/子类/数据属性
- range 类去重（同一类从多条路径到达只出现一次）
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

:usesEquipment a owl:ObjectProperty ;
    rdfs:domain :Route ;
    rdfs:range :Equipment ;
    rdfs:label "使用设备"@zh .

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
    """第 1 跳返回 Report → Route 的对象属性 + Route 的子类和数据属性。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=1)
    assert len(edges) == 1
    e = edges[0]
    assert e["hop"] == 1
    assert e["predicate_label"] == "含合成路线"
    assert e["domain_class_iri"] == REPORT_IRI
    assert e["domain_class_label"] == "报告"
    assert e["range_class_iri"] == ROUTE_IRI
    assert e["range_class_label"] == "合成路线"
    sub_iris = {s["iri"] for s in e["range_subclasses"]}
    assert STEP_IRI in sub_iris
    dp_labels = {d["label"] for d in e["range_data_properties"]}
    assert "工艺描述" in dp_labels
    assert "收率" in dp_labels


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
    """max_hops=1 只返回第 1 跳，不含 Equipment。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=1)
    all_range_iris = {e["range_class_iri"] for e in edges}
    assert EQUIP_IRI not in all_range_iris


def test_unknown_class_returns_empty(engine):
    edges = engine.get_relation_schema("http://test.example.com/mini#NonExistent")
    assert edges == []


def test_range_dedup(engine):
    """同一 range 类只出现一次（去重）。"""
    edges = engine.get_relation_schema(REPORT_IRI, max_hops=4)
    range_iris = [e["range_class_iri"] for e in edges]
    assert len(range_iris) == len(set(range_iris))
