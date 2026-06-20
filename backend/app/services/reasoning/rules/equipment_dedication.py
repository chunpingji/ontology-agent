"""Rule Group 2: Equipment dedication decisions (R-ED1 ~ R-ED6).

Determines whether equipment must be dedicated or can be shared.
"""

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


def r_ed1_penicillin_dedication(drug_classes: list[str], **_) -> RuleResult:
    """R-ED1: Penicillin drugs -> MUST dedicate (unconditional, CFDI mandatory)."""
    is_penicillin = any("PenicillinDrug" in c for c in drug_classes)

    return RuleResult(
        rule_id="R-ED1",
        fired=is_penicillin,
        description="Penicillin drug → mandatory equipment dedication",
        inputs={"drug_classes": drug_classes},
        conclusion={"requires_dedication": True, "unconditional": True} if is_penicillin else {},
        regulation_ref="CFDI 2023-03 §4.4: 必须专用独立厂房、设施和设备",
    )


def r_ed2_cytotoxic_non_inactivatable(
    drug_classes: list[str], inactivation_classes: list[str], **_
) -> RuleResult:
    """R-ED2: Cytotoxic + NonInactivatable -> MUST dedicate."""
    is_cytotoxic = any("CytotoxicDrug" in c for c in drug_classes)
    non_inactivatable = any("NonInactivatable" in c for c in inactivation_classes)
    fired = is_cytotoxic and non_inactivatable

    return RuleResult(
        rule_id="R-ED2",
        fired=fired,
        description="Cytotoxic drug with no inactivation method → equipment dedication required",
        inputs={"drug_classes": drug_classes, "inactivation_classes": inactivation_classes},
        conclusion={"requires_dedication": True} if fired else {},
        regulation_ref="CFDI 2023-03 §4.2",
    )


def r_ed3_biologic_prion_risk(drug_classes: list[str], has_prion_risk: bool = False, **_) -> RuleResult:
    """R-ED3: Biological product + prion risk -> MUST dedicate."""
    is_biologic = any("BiologicalProduct" in c for c in drug_classes)
    fired = is_biologic and has_prion_risk

    return RuleResult(
        rule_id="R-ED3",
        fired=fired,
        description="Biological product with prion risk → equipment dedication required",
        inputs={"drug_classes": drug_classes, "has_prion_risk": has_prion_risk},
        conclusion={"requires_dedication": True} if fired else {},
        regulation_ref="CFDI 2023-03 §4.5",
    )


def r_ed4_cytotoxic_inactivatable(
    drug_classes: list[str], inactivation_classes: list[str], **_
) -> RuleResult:
    """R-ED4: Cytotoxic + inactivatable -> allowed shared (with conditions)."""
    is_cytotoxic = any("CytotoxicDrug" in c for c in drug_classes)
    can_inactivate = any(
        "HeatInactivatable" in c or "ChemicalInactivatable" in c
        for c in inactivation_classes
    )
    fired = is_cytotoxic and can_inactivate

    return RuleResult(
        rule_id="R-ED4",
        fired=fired,
        description="Cytotoxic drug with validated inactivation → shared line allowed with conditions",
        inputs={"drug_classes": drug_classes, "inactivation_classes": inactivation_classes},
        conclusion={
            "requires_dedication": False,
            "requires_inactivation_validation": True,
        } if fired else {},
        regulation_ref="CFDI 2023-03 §4.2 (conditional)",
    )


def r_ed5_oeb5_dedication(drug_classes: list[str], oeb_classes: list[str], **_) -> RuleResult:
    """R-ED5: OEB5 (highest potency) -> MUST dedicate."""
    is_high_activity = any("HighActivityDrug" in c for c in drug_classes)
    is_oeb5 = any("OEB5" in c for c in oeb_classes)
    fired = is_high_activity and is_oeb5

    return RuleResult(
        rule_id="R-ED5",
        fired=fired,
        description="OEB5 classification (highest potency) → equipment dedication required",
        inputs={"drug_classes": drug_classes, "oeb_classes": oeb_classes},
        conclusion={"requires_dedication": True} if fired else {},
        regulation_ref="CFDI 2023-03 §4.6",
    )


def r_ed6_hormonal_hvac(drug_classes: list[str], **_) -> RuleResult:
    """R-ED6: Hormonal drug -> requires independent HVAC."""
    is_hormonal = any("HormonalDrug" in c for c in drug_classes)

    return RuleResult(
        rule_id="R-ED6",
        fired=is_hormonal,
        description="Hormonal drug → independent air handling system required",
        inputs={"drug_classes": drug_classes},
        conclusion={"requires_independent_hvac": True} if is_hormonal else {},
        regulation_ref="CFDI 2023-03 §4.3",
    )


ALL_RULES = [
    r_ed1_penicillin_dedication,
    r_ed2_cytotoxic_non_inactivatable,
    r_ed3_biologic_prion_risk,
    r_ed4_cytotoxic_inactivatable,
    r_ed5_oeb5_dedication,
    r_ed6_hormonal_hvac,
]
