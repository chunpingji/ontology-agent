"""Mock 毒理研究事实源 —— PDE/OEB 推导所需 tox-study A-Box 尚未对接的 interim 替身。

CMCReport 的「共线评估数据」端点携带原文断言的 PDE（如 1.8 mg/日）。系统另有一条确定性
毒理推导管线 :func:`app.services.reasoning.derivation.derive_facts`，可由 NOAEL/种属/周期推导
``PDE → OEL → OEB 带``。要检测「推导值 vs 原文值」的冲突，推导侧需要一份规范化的毒理研究事实
（:class:`~app.services.reasoning.derivation.ToxStudy`）——而该 A-Box（重复给药毒理研究个体）
**尚未接入本系统**，故本模块 mock 一个外部毒理研究主数据源。

与 :mod:`app.services.extraction.assessment_team_source` 同构：真实内网毒理数据库/研究报告
A-Box 接入时，改由 ``app/services/integration`` 的连接器支撑，只需替换 :func:`get_tox_study_source`
返回的实现（本 :class:`ToxStudySource` Protocol 即契约）——调用方零改动。

离线安全（Principle VI 优雅降级）：源不可用 → :meth:`get_study` 返回 ``None``，调用方跳过、绝不抛出。
"""

from __future__ import annotations

from typing import Protocol

from app.services.reasoning.derivation import ToxStudy

# Canned 毒理研究事实：SD 大鼠 28 天重复给药，NOAEL 0.5 mg/kg/日。经 derive_facts（F1=5 rat、
# F2=10、F3=10 [28d]、F4=1、F5=1；特殊毒性端点缺失 → provisional +1 带）→ **推荐 OEB band 5**。
# 与原文断言 PDE 1.8 mg/日（=1800 µg/日 → band 2）构成 Δ=3 带的冲突。deterministic，无随机。
_MOCK_CMC_TOX_STUDY = ToxStudy(
    noael_mg_kg_day=0.5,
    species="rat",
    study_duration_days=28,
    notes="SD大鼠 28天重复给药 NOAEL（mock 毒理研究事实源；真实毒理 A-Box 尚未接入）",
)


class ToxStudySource(Protocol):
    """毒理研究主数据事实源契约。"""

    def get_study(self) -> ToxStudy | None:
        """返回当前评估对象的规范化毒理研究事实（源不可用 → ``None``）。"""
        ...


class MockToxStudySource:
    """毒理研究主数据（mock 外部源）。

    返回一份稳定的 canned 研究事实（SD 大鼠 28 天重复给药 NOAEL 0.5 mg/kg/日）；真实毒理数据库
    接入时在此切换为连接器支撑的实现即可，调用方无需改动。"""

    def get_study(self) -> ToxStudy | None:
        return _MOCK_CMC_TOX_STUDY


def get_tox_study_source() -> ToxStudySource:
    """返回当前生效的毒理研究主数据事实源（现为 mock，A-Box 尚未对接）。"""
    return MockToxStudySource()
