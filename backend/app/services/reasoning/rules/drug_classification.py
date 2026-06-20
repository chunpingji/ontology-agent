"""Rule Group 1: Drug product risk auto-classification (R-DC1 ~ R-DC4).

These rules classify drug products based on their API properties.
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


def r_dc1_genotoxicity_to_cytotoxic(drug_props: dict, api_props: dict) -> RuleResult:
    """R-DC1: API has GenotoxicityProfile -> product classified as CytotoxicDrug."""
    toxicity_classes = api_props.get("toxicity_profile_classes", [])
    has_genotox = any("GenotoxicityProfile" in str(c) for c in toxicity_classes)

    return RuleResult(
        rule_id="R-DC1",
        fired=has_genotox,
        description="Genotoxicity profile detected → CytotoxicDrug classification",
        inputs={"toxicity_profile_classes": toxicity_classes},
        conclusion={"add_class": "CytotoxicDrug"} if has_genotox else {},
        regulation_ref="CFDI 2023-03 §3.2",
    )


def r_dc2_oeb_to_high_activity(drug_props: dict, api_props: dict) -> RuleResult:
    """R-DC2: OEB >= 4 -> classified as HighActivityDrug."""
    oeb_classes = api_props.get("oeb_classes", [])
    high_oeb = any("OEB4" in str(c) or "OEB5" in str(c) for c in oeb_classes)

    return RuleResult(
        rule_id="R-DC2",
        fired=high_oeb,
        description="OEB4 or OEB5 classification → HighActivityDrug",
        inputs={"oeb_classes": oeb_classes},
        conclusion={"add_class": "HighActivityDrug"} if high_oeb else {},
        regulation_ref="CFDI 2023-03 §3.3",
    )


def r_dc3_sensitization_to_high_sensitizing(drug_props: dict, api_props: dict) -> RuleResult:
    """R-DC3: Sensitization level > 3 -> HighSensitizingDrug."""
    sensitization_level = drug_props.get("sensitization_level", 0)
    is_high = sensitization_level > 3

    return RuleResult(
        rule_id="R-DC3",
        fired=is_high,
        description="Sensitization level > 3 → HighSensitizingDrug",
        inputs={"sensitization_level": sensitization_level},
        conclusion={"add_class": "HighSensitizingDrug"} if is_high else {},
        regulation_ref="CFDI 2023-03 §3.4",
    )


def r_dc4_beta_lactam_ring(drug_props: dict, api_props: dict) -> RuleResult:
    """R-DC4: API has beta-lactam ring -> BetaLactamDrug."""
    has_ring = api_props.get("has_beta_lactam_ring", False)

    return RuleResult(
        rule_id="R-DC4",
        fired=has_ring,
        description="Beta-lactam ring structure → BetaLactamDrug",
        inputs={"has_beta_lactam_ring": has_ring},
        conclusion={"add_class": "BetaLactamDrug"} if has_ring else {},
        regulation_ref="CFDI 2023-03 §4.4",
    )


ALL_RULES = [
    r_dc1_genotoxicity_to_cytotoxic,
    r_dc2_oeb_to_high_activity,
    r_dc3_sensitization_to_high_sensitizing,
    r_dc4_beta_lactam_ring,
]
