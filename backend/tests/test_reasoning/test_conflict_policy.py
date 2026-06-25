"""US3-AS2 / FR-011 — 冲突消解策略即数据（E13）。

`policy.resolve_dedication_conflict` / `resolve_risk_level` 是纯函数，消费一条
`defaults.ConflictPolicy`（或其等价的 ORM 适配对象），缺省时回退到 legacy 行为
（golden-master 一致，FR-012）。本测试证明：

  • 多条矛盾的专用化结论经 `dedication`（`safety_override` / `restrictive_wins`）
    聚合后，`requires_dedication=True` 胜（安全优先元规则，CFDI §13.4）；
  • 翻转 `override_direction` 为 `permissive_wins` 即改变聚合语义——纯数据，无源码改动；
  • 风险等级按 `priority_lattice` 取最高严重度；替换格点即改变裁决——纯数据。
"""

from __future__ import annotations

from app.services.reasoning import policy
from app.services.reasoning.defaults import (
    RISK_PRIORITY_LATTICE,
    ConflictPolicy,
    default_conflict_policies,
)


def _policies() -> dict[str, ConflictPolicy]:
    return {p.dimension: p for p in default_conflict_policies()}


# --- dedication: safety_override / restrictive_wins -------------------------
def test_default_dedication_is_restrictive_wins():
    pol = _policies()["dedication"]
    assert pol.strategy == "safety_override"
    assert pol.override_direction == "restrictive_wins"


def test_restrictive_wins_true_beats_false():
    """AS2：一条要求专用化 + 一条不要求 → 安全优先，True 胜。"""
    pol = _policies()["dedication"]
    conclusions = [{"requires_dedication": False}, {"requires_dedication": True}]
    assert policy.resolve_dedication_conflict(conclusions, pol) is True


def test_restrictive_wins_all_false_stays_false():
    pol = _policies()["dedication"]
    conclusions = [{"requires_dedication": False}, {"requires_dedication": False}]
    assert policy.resolve_dedication_conflict(conclusions, pol) is False


def test_dedication_legacy_default_matches_policy():
    """policy=None（legacy 回退）与显式 restrictive_wins 策略逐字一致（FR-012）。"""
    conclusions = [{"requires_dedication": True}, {"requires_dedication": False}]
    assert policy.resolve_dedication_conflict(conclusions, None) is True
    assert policy.resolve_dedication_conflict([], None) is False


def test_permissive_wins_is_pure_data_flip():
    """把 override_direction 改成 permissive_wins（纯数据）即反转语义：显式 False 否决。"""
    permissive = ConflictPolicy(
        dimension="dedication",
        strategy="safety_override",
        regulation_ref="（演示：宽松优先）",
        description="permissive override demo",
        override_direction="permissive_wins",
    )
    conclusions = [{"requires_dedication": True}, {"requires_dedication": False}]
    assert policy.resolve_dedication_conflict(conclusions, permissive) is False
    # 无 False 否决项时仍可命中 True
    assert policy.resolve_dedication_conflict([{"requires_dedication": True}], permissive) is True


# --- risk_level: max_severity over the priority lattice ---------------------
def test_default_risk_is_max_severity():
    pol = _policies()["risk_level"]
    assert pol.strategy == "max_severity"
    assert pol.priority_lattice == dict(RISK_PRIORITY_LATTICE)


def test_max_severity_takes_highest_level():
    pol = _policies()["risk_level"]
    levels = ["LowRisk", "HighRisk", "MediumRisk"]
    assert policy.resolve_risk_level(levels, pol) == "HighRisk"


def test_no_levels_defaults_to_lowest_severity():
    pol = _policies()["risk_level"]
    assert policy.resolve_risk_level([], pol) == "LowRisk"


def test_risk_lattice_is_pure_data_override():
    """替换 priority_lattice（纯数据）即改变裁决：令 LowRisk 反成最高严重度。"""
    inverted = ConflictPolicy(
        dimension="risk_level",
        strategy="max_severity",
        regulation_ref="（演示：倒置格点）",
        description="inverted lattice demo",
        priority_lattice={"HighRisk": 1, "MediumRisk": 2, "LowRisk": 3},
    )
    assert policy.resolve_risk_level(["HighRisk", "LowRisk"], inverted) == "LowRisk"
