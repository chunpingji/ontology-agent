"""_detect_module 文档归类（007 US1，T011；两处 dict 须同步）。

KGStore 与 api.kg 各持一份 `_detect_module`；文档类 IRI（`/slpra/document/`）须归 "document"，
既有模块（risk 等）与 `facts#`（运营事实）归类保持不变（零回归）。
"""

from __future__ import annotations

from app.api.kg import _detect_module as api_detect
from app.services.kg_store import KGStore
from tests.test_integration.fixtures.doc_repo_changes import DOCUMENT_NS

DOC_CLS = f"{DOCUMENT_NS}TechTransferReport"
RISK_CLS = "https://ontology.pharma-gmp.cn/slpra/risk/RiskAssessmentReport"
FACTS_CLS = "http://slpra.org/facts#equipment"


def test_kg_store_detects_document(db, fake_engine):
    detect = KGStore(db=db, onto_engine=fake_engine)._detect_module
    assert detect([DOC_CLS]) == "document"
    assert detect([RISK_CLS]) == "risk"  # 既有模块不变
    assert detect([FACTS_CLS]) == "integration"  # 运营事实不变


def test_api_kg_detects_document():
    assert api_detect([DOC_CLS]) == "document"
    assert api_detect([RISK_CLS]) == "risk"
    assert api_detect([FACTS_CLS]) == "integration"
