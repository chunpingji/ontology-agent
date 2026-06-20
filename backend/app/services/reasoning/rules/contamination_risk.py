"""Rule Group 3: Contamination pathway risk scoring (R-CP1 ~ R-CP4)."""

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


def r_cp1_residue_low_pde_poor_cleanability(
    pathway: str, pde: float | None, cleanability_score: int | None, **_
) -> RuleResult:
    """R-CP1: Residue contamination + PDE < 0.01 + cleanability < 3 -> High Risk."""
    fired = (
        pathway == "residue"
        and pde is not None and pde < 0.01
        and cleanability_score is not None and cleanability_score < 3
    )
    return RuleResult(
        rule_id="R-CP1",
        fired=fired,
        description="Residue pathway with low PDE and poor cleanability → High Risk",
        inputs={"pathway": pathway, "pde": pde, "cleanability_score": cleanability_score},
        conclusion={"risk_level": "HighRisk"} if fired else {},
        regulation_ref="CFDI 2023-03 §5.1",
    )


def r_cp2_powder_airborne_general_area(
    pathway: str, dosage_form: str | None, area_type: str | None, **_
) -> RuleResult:
    """R-CP2: Powder + general area + airborne transmission -> High Risk."""
    fired = pathway == "airborne" and dosage_form == "powder" and area_type == "general"
    return RuleResult(
        rule_id="R-CP2",
        fired=fired,
        description="Powder operation in general area with airborne transmission → High Risk",
        inputs={"pathway": pathway, "dosage_form": dosage_form, "area_type": area_type},
        conclusion={"risk_level": "HighRisk"} if fired else {},
        regulation_ref="CFDI 2023-03 §5.3",
    )


def r_cp3_liquid_good_cleanability(
    pathway: str, dosage_form: str | None, cleanability_score: int | None, **_
) -> RuleResult:
    """R-CP3: Solution + cleanability > 4 + residue pathway -> Low Risk."""
    fired = (
        pathway == "residue"
        and dosage_form == "solution"
        and cleanability_score is not None and cleanability_score > 4
    )
    return RuleResult(
        rule_id="R-CP3",
        fired=fired,
        description="Liquid product with excellent cleanability → Low Risk",
        inputs={"pathway": pathway, "dosage_form": dosage_form,
                "cleanability_score": cleanability_score},
        conclusion={"risk_level": "LowRisk"} if fired else {},
        regulation_ref="CFDI 2023-03 §5.1",
    )


def r_cp4_same_form_confusion(
    pathway: str, source_form: str | None, target_form: str | None, **_
) -> RuleResult:
    """R-CP4: Same dosage form + confusion pathway -> Medium Risk."""
    fired = (
        pathway == "confusion"
        and source_form is not None and target_form is not None
        and source_form == target_form
    )
    return RuleResult(
        rule_id="R-CP4",
        fired=fired,
        description="Same dosage form parallel production → confusion Medium Risk",
        inputs={"pathway": pathway, "source_form": source_form, "target_form": target_form},
        conclusion={"risk_level": "MediumRisk"} if fired else {},
        regulation_ref="CFDI 2023-03 §5.4",
    )


ALL_RULES = [
    r_cp1_residue_low_pde_poor_cleanability,
    r_cp2_powder_airborne_general_area,
    r_cp3_liquid_good_cleanability,
    r_cp4_same_form_confusion,
]
