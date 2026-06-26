"""T-Box 增补 surgical_merge round-trip（007 US1，T009；宪章 II 保真）。

[record-materialization C2.3](../../../specs/007-rnd-document-fact-source/contracts/record-materialization-invariants.md)：
重复发布幂等、未建模三元组逐字保留、外部命名 IRI（BFO/OBO）不被改写。

扫描**真实** `ontology/slpra/`（`parents[3]`）；`build_managed_graph` 用测试 db（无文档托管类 →
文档子图非托管 → 经 `surgical_merge` 逐字保留）。
"""

from __future__ import annotations

from pathlib import Path

from rdflib import RDF, RDFS, BNode, URIRef

from app.services import ttl_merge
from tests.test_integration.fixtures.doc_repo_changes import DOCUMENT_NS

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology" / "slpra"
BFO_GDC = URIRef("http://purl.obolibrary.org/obo/BFO_0000031")


def _no_bnode(t) -> bool:
    return not isinstance(t[0], BNode) and not isinstance(t[2], BNode)


def _merge(db):
    base = ttl_merge.load_base_graph(ONTOLOGY_DIR)
    managed, subjects = ttl_merge.build_managed_graph(db)
    return base, ttl_merge.surgical_merge(base, managed, subjects)


def test_document_module_survives_merge_verbatim(db):
    base, merged = _merge(db)

    # 外部命名 IRI（BFO generically dependent continuant）逐字保留、未被改写。
    reg = URIRef(f"{DOCUMENT_NS}RegulatoryDocument")
    assert (reg, RDFS.subClassOf, BFO_GDC) in merged

    # 6 个研发阶段枚举个体 round-trip 保留。
    phase_cls = URIRef(f"{DOCUMENT_NS}DevelopmentPhase")
    assert len(set(merged.subjects(RDF.type, phase_cls))) == 6

    # 文档模块全部非空白节点 base 三元组都在 merged 中（未建模 → 逐字保留，无删除）。
    doc_triples = {t for t in base if DOCUMENT_NS in str(t[0]) and _no_bnode(t)}
    assert doc_triples, "未从权威 TTL 载入任何文档模块三元组（slpra-document.ttl 缺失？）"
    assert doc_triples <= set(merged)


def test_merge_is_idempotent(db):
    _base, merged1 = _merge(db)
    managed, subjects = ttl_merge.build_managed_graph(db)
    merged2 = ttl_merge.surgical_merge(merged1, managed, subjects)
    a1 = {t for t in merged1 if _no_bnode(t)}
    a2 = {t for t in merged2 if _no_bnode(t)}
    assert a1 == a2
