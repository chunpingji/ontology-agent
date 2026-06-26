"""A-Box / T-Box 边界门禁（007 US1，T008；SC-004 关键红线）。

[record-materialization C2.1/C2.2](../../../specs/007-rnd-document-fact-source/contracts/record-materialization-invariants.md)：
物化任意批文档后，权威 `*.ttl` **无任何** `facts#` 个体三元组；`slpra-document.ttl` 仅含
类/枚举/属性（T-Box），`RegulatoryDocument` 具名实例 = 0。

注：conftest 的 `_isolate_ontology_dir` 把 `settings.ontology_dir` 指向空临时目录；本测试须扫描
**真实** `ontology/slpra/`，故用仓库路径常量（本文件在 `backend/tests/test_integration/` →
`parents[3]` = 仓库根）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rdflib import RDF, Graph, URIRef

from app.models.integration import IntegrationConnector
from app.services.integration.materializer import FactMaterializer
from tests.test_integration.fixtures.doc_repo_changes import (
    DOC_REPO_CHANGE_STAB_PRECLIN,
    DOC_REPO_CHANGE_TTR_V2,
    DOCUMENT_NS,
    inline_config,
)

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology" / "slpra"
FACTS_NS = "http://slpra.org/facts#"
_DOC_CLASSES = (
    "RegulatoryDocument",
    "INDDossier",
    "TechTransferReport",
    "ProcessValidationReport",
    "StabilityReport",
    "NDA_BLADossier",
    "PVReport",
)


def test_no_facts_triples_in_any_authoritative_ttl(db, fake_engine):
    """C2.1：物化两条文档（记录层）后，权威 TTL 仍无 facts# 三元组（SC-004 = 0）。"""
    c = IntegrationConnector(
        system_type="doc_repo",
        name="EDMS",
        connection_config=inline_config(
            changes=[DOC_REPO_CHANGE_TTR_V2, DOC_REPO_CHANGE_STAB_PRECLIN]
        ),
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    asyncio.run(FactMaterializer(db, fake_engine).run_sync(c))

    ttls = list(ONTOLOGY_DIR.glob("*.ttl"))
    assert ttls, f"未找到权威 TTL：{ONTOLOGY_DIR}"
    for ttl in ttls:
        assert FACTS_NS not in ttl.read_text(encoding="utf-8")  # 字符级
        g = Graph()
        g.parse(str(ttl), format="turtle")
        for s, _p, o in g:  # 三元组级
            assert FACTS_NS not in str(s)
            assert FACTS_NS not in str(o)


def test_document_ttl_is_tbox_only_zero_named_instances():
    """C2.2：slpra-document.ttl 仅类/枚举/属性，文档类具名实例数 = 0（T-Box 纯净）。"""
    g = Graph()
    g.parse(str(ONTOLOGY_DIR / "slpra-document.ttl"), format="turtle")
    for sub in _DOC_CLASSES:
        cls = URIRef(f"{DOCUMENT_NS}{sub}")
        assert list(g.subjects(RDF.type, cls)) == [], f"{sub} 不得有具名实例（T-Box 纯净）"
