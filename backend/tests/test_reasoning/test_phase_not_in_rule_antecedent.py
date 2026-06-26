"""US3 FR-011 负向门禁：研发阶段不得进入任何 006 规则前件（T034；红线 C4.2/Q3）。

[provenance-and-phase C4.2](../../../specs/007-rnd-document-fact-source/contracts/provenance-and-phase-query.md)：
研发阶段本期**仅作溯源/合规上下文**，MUST NOT 出现在任何 006 声明式判据（E11 pattern）或
决策规则（E12 antecedent）的**前件**中——防止越界把阶段织入推断逻辑。

本测试递归扫描权威默认判据/决策规则的前件 AST，断言任何位置都不引用阶段词表
（`DevelopmentPhase` / `Phase_*` / `hasDevelopmentPhase` / 托管文档命名空间）。
"""

from __future__ import annotations

from app.services.reasoning.defaults import (
    default_classification_criteria,
    default_decision_rules,
)

DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"
# 阶段相关 token：本地名前缀、枚举类名、关联属性、托管文档命名空间。
FORBIDDEN_TOKENS = ("DevelopmentPhase", "Phase_", "hasDevelopmentPhase", DOCUMENT_NS)


def _string_leaves(node) -> list[str]:
    """递归收集 AST（dict/list 嵌套）中的全部字符串叶子（含键与值）。"""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(_string_leaves(v))
    elif isinstance(node, (list, tuple)):
        for item in node:
            out.extend(_string_leaves(item))
    return out


def _assert_phase_free(antecedent: dict, label: str):
    for leaf in _string_leaves(antecedent):
        for token in FORBIDDEN_TOKENS:
            assert token not in leaf, (
                f"{label} 的前件引用了阶段 token {token!r}（违反 FR-011：阶段不参与推断）：{leaf!r}"
            )


def test_no_classification_criterion_references_phase():
    """E11 判据 pattern（前件）均不引用研发阶段。"""
    criteria = default_classification_criteria()
    assert criteria, "应存在默认分类判据"
    for c in criteria:
        _assert_phase_free(c.pattern, f"判据 {c.key}")


def test_no_decision_rule_antecedent_references_phase():
    """E12 决策规则 antecedent（前件）均不引用研发阶段。"""
    rules = default_decision_rules()
    assert rules, "应存在默认决策规则"
    for r in rules:
        _assert_phase_free(r.antecedent, f"决策规则 {r.key}")


def test_phase_token_detector_is_live():
    """元测试：探测器对含阶段 token 的前件确实会失败（守门不空转）。"""
    poisoned = {"op": "literal_eq", "key": "hasDevelopmentPhase",
                "value": f"{DOCUMENT_NS}Phase_ClinicalI"}
    flagged = any(
        tok in leaf for leaf in _string_leaves(poisoned) for tok in FORBIDDEN_TOKENS
    )
    assert flagged, "探测器必须能识别阶段 token（否则负向门禁形同虚设）"
