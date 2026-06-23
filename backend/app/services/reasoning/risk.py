"""高风险 QA 闸门判据（003, G2, FR-005, contracts/assess-bootstrap §3）。

纯函数：据 canonical ``results`` + ``risk_level`` 判定一条结论是否须经 QA 电子签批方可
生效。落库（``/assess``）与增量重算共用此判据、可独立单测。判据为**初始集**，可后续
迭代细化（spec Assumptions）。
"""

from __future__ import annotations

# 高危药物类别命中关键词（青霉素/头孢/高致敏，初始集）。
_HAZARD_KEYWORDS = (
    "penicillin", "青霉",
    "cephalosporin", "头孢",
    "sensitiz", "致敏", "hazard", "hormonal",
)


def _hazardous_scenario(results: dict) -> bool:
    """``hazardous_categories`` / ``cfdi_scenarios`` 命中青霉素/头孢/高致敏即 True。"""
    haystack_parts: list[str] = []
    haystack_parts.extend(str(x) for x in (results.get("hazardous_categories") or []))
    haystack_parts.extend(str(x) for x in (results.get("cfdi_scenarios") or []))
    haystack = " ".join(haystack_parts).lower()
    return any(kw in haystack for kw in _HAZARD_KEYWORDS)


def requires_qa_signature(results: dict, risk_level: str | None) -> bool:
    """高风险判据（FR-005）：高总体风险 OR 需专用化 OR 命中高危场景 → 须 QA 签批。"""
    results = results or {}
    return (
        risk_level == "HighRisk"
        or bool(results.get("requires_dedication"))
        or _hazardous_scenario(results)
    )
