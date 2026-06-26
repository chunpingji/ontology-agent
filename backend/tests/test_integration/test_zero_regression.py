"""零回归终局门禁（007 Polish，T042；SC-007 / FR-012）。

契约 [provenance-and-phase C5](../../../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)：
接入文档事实源（US1–US4）后——
- C5.1：既有 5 类事实源（APS/ERP/MES/LIMS/CTMS）回归**非预期变化数 = 0**：仍回退
  `APSConnector`、仍走原 `facts#<entity_type>` 物化分支、绝不被挂托管文档类/阶段。
- C5.2：既有评估结论基线对外形状（`AssessmentResult`）不变：跨**全部** R-DC/R-ED/R-SC/R-CP
  规则矩阵，附加研发阶段标注后 canonical 投影逐字一致，`phase_context` 为唯一附加面。
"""

from __future__ import annotations

import asyncio

from app.models.entity_shadow import EntityShadow
from app.models.integration import IntegrationConnector
from app.services.integration.aps_connector import APSConnector
from app.services.integration.connector_factory import connector_for, doc_type_to_class_map
from app.services.integration.materializer import FactMaterializer
from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix

FACTS = "http://slpra.org/facts#"
DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"
PHASE_CLINICAL_I = f"{DOCUMENT_NS}Phase_ClinicalI"

# 既有 5 类运营事实源（002）——doc_repo 之外的全部 system_type。
LEGACY_SYSTEM_TYPES = ["aps", "erp", "mes", "lims", "ctms"]

# `AssessmentResult` 对外形状（公开属性全集）：6 项既有结论面 + `phase_context` 附加溯源面。
EXPECTED_RESULT_ATTRS = {
    "rules_fired",
    "scenarios",
    "requires_dedication",
    "risk_level",
    "maco",
    "recommendations",
    "phase_context",
}


def _run(coro):
    return asyncio.run(coro)


def _legacy_connector(db, system_type: str) -> IntegrationConnector:
    c = IntegrationConnector(
        system_type=system_type,
        name=f"{system_type}-legacy",
        connection_config={
            "source_mode": "inline",
            "inline_changes": [
                {
                    "entity_id": f"{system_type}-EQ-1",
                    "entity_type": "equipment",
                    "version": 1,
                    "fields": {"status": "running"},
                }
            ],
        },
        field_mapping={},
        poll_interval_seconds=2,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# --- C5.1 既有 5 类事实源零回归 -------------------------------------------


def test_legacy_fact_sources_still_dispatch_to_aps():
    """C5.1：5 类既有事实源全部回退 APSConnector，且无 doc_repo 文档类映射（零回归）。"""
    for st in LEGACY_SYSTEM_TYPES:
        c = IntegrationConnector(
            system_type=st,
            connection_config={"source_mode": "inline", "inline_changes": []},
            field_mapping={},
            poll_interval_seconds=2,
        )
        assert isinstance(connector_for(c), APSConnector), f"{st} 必须回退 APSConnector"
        assert doc_type_to_class_map(c) == {}, f"{st} 不得获得托管文档类映射"


def test_legacy_materialization_unchanged_zero_unexpected_changes(db, fake_engine):
    """C5.1：5 类事实源经 run_sync 物化仍落 facts#<entity_type>，未被挂文档类/阶段。

    逐类核对「非预期变化」：class_iri 偏离 facts#、命名空间泄漏到 /slpra/document/、
    或注入了 hasDevelopmentPhase——任一即记一处偏离。终局断言偏离数 = 0。
    """
    deviations: list[str] = []
    for st in LEGACY_SYSTEM_TYPES:
        c = _legacy_connector(db, st)
        run = _run(FactMaterializer(db, fake_engine).run_sync(c))
        assert run.status == "success"

        s = (
            db.query(EntityShadow)
            .filter(EntityShadow.iri == f"{FACTS}{st}-EQ-1")
            .one()
        )
        if s.class_iri != f"{FACTS}equipment":
            deviations.append(f"{st}: class_iri={s.class_iri}")
        if "/slpra/document/" in s.class_iri:
            deviations.append(f"{st}: 命名空间泄漏 {s.class_iri}")
        if "hasDevelopmentPhase" in (s.properties_json or {}):
            deviations.append(f"{st}: 非预期注入 hasDevelopmentPhase")

    assert deviations == [], f"既有事实源非预期变化数应为 0，实得：{deviations}"


# --- C5.2 AssessmentResult 对外形状不变（跨全规则矩阵）---------------------


def test_assessment_external_shape_unchanged_across_full_matrix():
    """C5.2：跨 R-DC/R-ED/R-SC/R-CP 全矩阵，附加阶段标注后 canonical 投影逐字一致。

    `phase_context` 为唯一附加属性面（缺省 None，不进 canonical 投影）——既有评估结论
    基线对外形状零变化（FR-012/SC-007）。
    """
    shape_deviations: list[str] = []
    projection_deviations: list[str] = []

    for case_id, engine, drug_iri, eq_iris in matrix.build_cases():
        res_base = eng.run_assessment(engine, drug_iri, eq_iris)
        base_proj = matrix.project(res_base)

        # 对外形状恰为既有 6 面 + phase_context 附加面；无新结论字段。
        if set(vars(res_base)) != EXPECTED_RESULT_ATTRS:
            shape_deviations.append(f"{case_id}: {sorted(vars(res_base))}")
        # 矩阵药物不携阶段 → 附加面缺省惰性为 None。
        if res_base.phase_context is not None:
            shape_deviations.append(f"{case_id}: phase_context 非缺省 None")

        # 注入研发阶段标注后再评估：canonical 投影必须逐字一致（阶段不参与推断）。
        engine.get_individual(drug_iri).properties["hasDevelopmentPhase"] = {
            "iri": PHASE_CLINICAL_I
        }
        res_phased = eng.run_assessment(engine, drug_iri, eq_iris)
        if matrix.project(res_phased) != base_proj:
            projection_deviations.append(case_id)
        # 阶段标注此时已置（仅附加溯源），但绝不改对外结论投影。
        assert res_phased.phase_context is not None

    assert shape_deviations == [], f"AssessmentResult 对外形状偏离：{shape_deviations}"
    assert projection_deviations == [], (
        f"附加阶段标注改变了评估结论投影（违反 FR-011/SC-007）：{projection_deviations}"
    )
