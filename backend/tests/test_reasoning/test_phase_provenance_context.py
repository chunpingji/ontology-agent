"""US3 研发阶段作为评估溯源上下文（T033；FR-011/Q3 — 本期仅标注）。

[provenance-and-phase C4.1/C4.3](../../../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)：
- C4.1：至少一条评估结论的**溯源**体现对应阶段的质量侧重（临床Ⅰ期 → 共线风险/清洁确认）。
- C4.3：接入阶段标注后 `AssessmentResult` **对外形状不变**（阶段为附加溯源上下文，非新结论字段）。

阶段经 `AssessmentResult.phase_context`（溯源标注面）承载，绝不进入 `interpreter.Facts` →
任何规则前件都无法引用（FR-011 红线由 T034 负向门禁独立坐实）。
"""

from __future__ import annotations

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix

DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"
PHASE_CLINICAL_I = f"{DOCUMENT_NS}Phase_ClinicalI"


def _run(individuals: dict, drug_iri: str):
    return eng.run_assessment(matrix.StubEngine(individuals), drug_iri, [])


def test_phase_context_reflects_quality_emphasis():
    """C4.1：临床Ⅰ期评估溯源标注体现该阶段质量侧重（共线污染 / 清洁验证确认）。"""
    inds = {"d": matrix._drug(classes=["PenicillinDrug"],
                              hasDevelopmentPhase={"iri": PHASE_CLINICAL_I})}
    res = _run(inds, "d")

    assert res.rules_fired, "应至少有一条评估结论（R-ED1 青霉素专用化）以承载阶段溯源上下文"
    assert res.phase_context is not None
    assert res.phase_context["phase"] == PHASE_CLINICAL_I
    emphasis = res.phase_context["quality_emphasis"]
    assert "共线" in emphasis and "清洁" in emphasis  # 临床Ⅰ期质量侧重


def test_phase_annotation_does_not_change_assessment_shape():
    """C4.3：同一药物加/不加阶段标注 → 对外结论投影逐字不变（阶段仅为附加溯源）。"""
    base = {"d": matrix._drug(classes=["PenicillinDrug"])}
    phased = {"d": matrix._drug(classes=["PenicillinDrug"],
                                hasDevelopmentPhase={"iri": PHASE_CLINICAL_I})}

    res_base = _run(base, "d")
    res_phased = _run(phased, "d")

    # 对外形状（canonical 投影：命中规则/专用化/风险/场景）逐字一致。
    assert matrix.project(res_phased) == matrix.project(res_base)
    # 阶段仅作附加溯源上下文：无阶段则 None，有阶段才置标注。
    assert res_base.phase_context is None
    assert res_phased.phase_context is not None


def test_no_phase_no_context():
    """无 hasDevelopmentPhase 的药物：phase_context 缺省为 None（零额外行为）。"""
    res = _run({"d": matrix._drug(classes=["PenicillinDrug"])}, "d")
    assert res.phase_context is None
