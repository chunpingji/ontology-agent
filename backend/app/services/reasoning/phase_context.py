"""研发阶段作为评估结论的溯源上下文（007 US3，FR-011/Q3 — 仅标注，不进规则前件）。

阶段是溯源/合规上下文，**绝不参与推断**：本模块只把阶段映射为一条人类可读的「质量体系
侧重」溯源标注，由 `AssessmentResult.phase_context` 承载（非 golden-master 字段，不改对外
形状，FR-012/SC-007）。阶段 IRI 永不进入 `interpreter.Facts`，故任何 006 判据/决策规则都
无法引用它（FR-011 红线；负向门禁见 `test_phase_not_in_rule_antecedent`）。
"""

from __future__ import annotations

DOCUMENT_NS = "https://ontology.pharma-gmp.cn/slpra/document/"

# 各研发阶段的质量体系侧重（溯源标注文案，单一来源；与 slpra-document.ttl 枚举一一对应）。
_PHASE_EMPHASIS: dict[str, str] = {
    "Phase_DrugDiscovery":
        "药物发现阶段：靶点确证与先导优化为主，工艺/共线尚未定型，质量体系侧重早期数据可靠性。",
    "Phase_Preclinical":
        "临床前阶段：非临床安全性/有效性研究，质量体系侧重 GLP 合规与杂质/稳定性表征。",
    "Phase_ClinicalI":
        "临床Ⅰ期（首次人体试验）：质量体系侧重共线污染风险控制与清洁验证确认，"
        "防止交叉污染危及受试者安全。",
    "Phase_ClinicalII_III":
        "临床Ⅱ/Ⅲ期：确证性试验放大，质量体系侧重工艺一致性与共线清洁验证的可重复性。",
    "Phase_NDA_BLA":
        "NDA/BLA 申报阶段：质量体系侧重工艺验证完整性与申报数据可追溯。",
    "Phase_PostMarket":
        "上市后阶段：商业化生产与药物警戒，质量体系侧重变更控制与持续清洁验证维护。",
}


def _local_name(phase_iri: str) -> str:
    return phase_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def phase_provenance_note(phase_iri: str) -> dict:
    """据阶段 IRI 产出一条溯源标注（仅标注，不参与规则求值）。未知阶段回退通用文案。"""
    emphasis = _PHASE_EMPHASIS.get(_local_name(phase_iri))
    return {
        "phase": phase_iri,
        "quality_emphasis": emphasis or "研发阶段作为溯源/合规上下文（无特定质量侧重映射）。",
    }
