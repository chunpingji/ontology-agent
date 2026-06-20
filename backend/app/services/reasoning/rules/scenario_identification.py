"""Rule Group 4: CFDI scenario auto-identification (R-SC1 ~ R-SC8)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RuleResult:
    rule_id: str
    fired: bool
    description: str
    inputs: dict[str, Any]
    conclusion: dict[str, Any]
    regulation_ref: str | None = None


def r_sc1_clinical_with_commercial(
    drug_classes: list[str], co_product_classes: list[str], **_
) -> RuleResult:
    """R-SC1: Clinical trial drug + commercial drug on shared equipment."""
    has_clinical = any("ClinicalTrialDrug" in c for c in drug_classes)
    has_commercial = any("CommercialDrug" in c for c in co_product_classes)
    fired = has_clinical and has_commercial

    return RuleResult(
        rule_id="R-SC1",
        fired=fired,
        description="Clinical trial drug sharing with commercial drug → Scenario 1",
        inputs={"drug_classes": drug_classes, "co_product_classes": co_product_classes},
        conclusion={
            "scenario": "ClinicalWithCommercialScenario",
            "requires_enhanced_documentation": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §6.1",
    )


def r_sc2_cytotoxic_shared(drug_classes: list[str], is_shared: bool = True, **_) -> RuleResult:
    """R-SC2: Cytotoxic drug on shared equipment."""
    fired = any("CytotoxicDrug" in c for c in drug_classes) and is_shared
    return RuleResult(
        rule_id="R-SC2", fired=fired,
        description="Cytotoxic drug on shared line → Scenario 2",
        inputs={"drug_classes": drug_classes, "is_shared": is_shared},
        conclusion={"scenario": "CytotoxicSharedLineScenario"} if fired else {},
        regulation_ref="CFDI 2023-03 §6.2",
    )


def r_sc3_hormonal_shared(drug_classes: list[str], is_shared: bool = True, **_) -> RuleResult:
    """R-SC3: Hormonal drug on shared equipment."""
    fired = any("HormonalDrug" in c for c in drug_classes) and is_shared
    return RuleResult(
        rule_id="R-SC3", fired=fired,
        description="Hormonal drug on shared line → Scenario 3",
        inputs={"drug_classes": drug_classes, "is_shared": is_shared},
        conclusion={
            "scenario": "HormonalSharedLineScenario",
            "requires_independent_hvac": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §6.3",
    )


def r_sc4_penicillin(drug_classes: list[str], **_) -> RuleResult:
    """R-SC4: Penicillin on any equipment -> triggers dedication immediately."""
    fired = any("PenicillinDrug" in c for c in drug_classes)
    return RuleResult(
        rule_id="R-SC4", fired=fired,
        description="Penicillin drug → Scenario 4 (mandatory dedication)",
        inputs={"drug_classes": drug_classes},
        conclusion={
            "scenario": "PenicillinSharedLineScenario",
            "requires_dedication": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §6.4",
    )


def r_sc5_biologic_shared(drug_classes: list[str], is_shared: bool = True, **_) -> RuleResult:
    """R-SC5: Biological product on shared equipment."""
    fired = any("BiologicalProduct" in c for c in drug_classes) and is_shared
    return RuleResult(
        rule_id="R-SC5", fired=fired,
        description="Biological product on shared line → Scenario 5",
        inputs={"drug_classes": drug_classes, "is_shared": is_shared},
        conclusion={
            "scenario": "BiologicSharedLineScenario",
            "requires_tse_assessment": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §6.5",
    )


def r_sc6_high_potency_shared(drug_classes: list[str], is_shared: bool = True, **_) -> RuleResult:
    """R-SC6: High activity drug on shared equipment."""
    fired = any("HighActivityDrug" in c for c in drug_classes) and is_shared
    return RuleResult(
        rule_id="R-SC6", fired=fired,
        description="High potency drug on shared line → Scenario 6",
        inputs={"drug_classes": drug_classes, "is_shared": is_shared},
        conclusion={"scenario": "HighPotencySharedLineScenario"} if fired else {},
        regulation_ref="CFDI 2023-03 §6.6",
    )


def r_sc7_sterile_shared(drug_classes: list[str], is_shared: bool = True, **_) -> RuleResult:
    """R-SC7: Sterile drug on shared equipment."""
    fired = any("SterileDrugProduct" in c for c in drug_classes) and is_shared
    return RuleResult(
        rule_id="R-SC7", fired=fired,
        description="Sterile drug on shared line → Scenario 7",
        inputs={"drug_classes": drug_classes, "is_shared": is_shared},
        conclusion={
            "scenario": "SterileDrugSharedLineScenario",
            "requires_aseptic_integrity": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §6.7",
    )


def r_sc8_multi_dosage_form(
    source_form: str | None, target_form: str | None, is_shared: bool = True, **_
) -> RuleResult:
    """R-SC8: Different dosage forms on shared equipment."""
    fired = (
        is_shared
        and source_form is not None and target_form is not None
        and source_form != target_form
    )
    return RuleResult(
        rule_id="R-SC8", fired=fired,
        description="Multiple dosage forms on shared line → Scenario 8",
        inputs={"source_form": source_form, "target_form": target_form},
        conclusion={"scenario": "MultiDosageFormScenario"} if fired else {},
        regulation_ref="CFDI 2023-03 §6.8",
    )


ALL_RULES = [
    r_sc1_clinical_with_commercial,
    r_sc2_cytotoxic_shared,
    r_sc3_hormonal_shared,
    r_sc4_penicillin,
    r_sc5_biologic_shared,
    r_sc6_high_potency_shared,
    r_sc7_sterile_shared,
    r_sc8_multi_dosage_form,
]
