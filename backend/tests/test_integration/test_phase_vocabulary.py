"""US3 研发阶段词表（T030；FR-005，US3 AS#2/#3）。

[provenance-and-phase C1](../../../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)：
`DevelopmentPhase` 枚举含 6 个体（DrugDiscovery/Preclinical/ClinicalI/ClinicalII_III/
NDA_BLA/PostMarket）；各携 `skos:notation`（次序）+ `rdfs:comment`（质量体系侧重）；取值
**受控**（枚举于托管 `DevelopmentPhase` 类、托管命名空间下）、**可版本化**（编入受版本控制的
权威 TTL，经 `surgical_merge` 发布并审计——发布/审计路径由 `test_document_tbox_merge` 独立坐实）。

扫描**真实** `ontology/slpra/slpra-document.ttl`（`parents[3]`），词表是 T-Box 静态资产，
与运行期引擎/DB 无关。
"""

from __future__ import annotations

from pathlib import Path

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import SKOS

from tests.test_integration.fixtures.doc_repo_changes import DOCUMENT_NS

DOC_TTL = Path(__file__).resolve().parents[3] / "ontology" / "slpra" / "slpra-document.ttl"
PHASE_CLASS = URIRef(f"{DOCUMENT_NS}DevelopmentPhase")

# C1.1：6 个有序枚举个体（按 skos:notation 次序）。
EXPECTED_PHASES = [
    "Phase_DrugDiscovery",
    "Phase_Preclinical",
    "Phase_ClinicalI",
    "Phase_ClinicalII_III",
    "Phase_NDA_BLA",
    "Phase_PostMarket",
]


def _graph() -> Graph:
    g = Graph()
    g.parse(DOC_TTL, format="turtle")
    return g


def test_development_phase_enumeration_has_six_individuals():
    """C1.1：DevelopmentPhase 枚举恰含 6 个体，且本地名与受控集一致。"""
    g = _graph()
    members = {str(s) for s in g.subjects(RDF.type, PHASE_CLASS)}
    assert len(members) == 6
    assert members == {f"{DOCUMENT_NS}{n}" for n in EXPECTED_PHASES}


def test_each_phase_carries_notation_and_comment():
    """C1.3：每阶段携 skos:notation（次序）+ rdfs:comment（质量侧重）。"""
    g = _graph()
    notations: list[int] = []
    for n in EXPECTED_PHASES:
        s = URIRef(f"{DOCUMENT_NS}{n}")
        notation = list(g.objects(s, SKOS.notation))
        comment = list(g.objects(s, RDFS.comment))
        assert len(notation) == 1, f"{n} 必须恰有一个 skos:notation（次序）"
        assert comment, f"{n} 必须携 rdfs:comment（质量体系侧重标注来源）"
        assert all(isinstance(c, Literal) and str(c).strip() for c in comment)
        notations.append(int(str(notation[0])))
    # 次序受控、连续可排序 1..6（有序枚举）。
    assert sorted(notations) == [1, 2, 3, 4, 5, 6]


def test_phase_vocabulary_is_controlled_and_versionable():
    """C1.2：取值受控（枚举于托管类、托管命名空间）、可版本化（编入带 versionIRI 的权威本体）。"""
    g = _graph()
    # 枚举类自身受管：声明于托管文档命名空间。
    assert (PHASE_CLASS, RDF.type, URIRef("http://www.w3.org/2002/07/owl#Class")) in g
    # 全部个体均落托管命名空间（受控取值，非 facts# 自由个体）。
    for s in g.subjects(RDF.type, PHASE_CLASS):
        assert str(s).startswith(DOCUMENT_NS)
    # 该模块本体带 owl:versionIRI → 可版本化发布（surgical_merge 路径见 test_document_tbox_merge）。
    onto = URIRef(DOCUMENT_NS)
    version_iris = list(g.objects(onto, URIRef("http://www.w3.org/2002/07/owl#versionIRI")))
    assert version_iris, "托管文档本体必须声明 owl:versionIRI（可版本化）"
