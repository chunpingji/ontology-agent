"""Reasoning engine orchestrator: loads entity data, runs rules, produces assessment."""

from __future__ import annotations

import logging
from typing import Any

from app.services.ontology_engine import OntologyEngine
from app.services.reasoning.calculators import MACOResult, calculate_maco, calculate_pde
from app.services.reasoning.conflict_resolver import (
    resolve_dedication_conflict,
    resolve_risk_level,
)
from app.services.reasoning.rules import (
    contamination_risk,
    drug_classification,
    equipment_dedication,
    scenario_identification,
)

logger = logging.getLogger(__name__)


class AssessmentResult:
    def __init__(self):
        self.rules_fired: list[dict] = []
        self.scenarios: list[dict] = []
        self.requires_dedication: bool = False
        self.risk_level: str = "LowRisk"
        self.maco: MACOResult | None = None
        self.recommendations: list[str] = []


def run_assessment(
    engine: OntologyEngine,
    drug_iri: str,
    equipment_iris: list[str],
) -> AssessmentResult:
    result = AssessmentResult()

    drug = engine.get_individual(drug_iri)
    if drug is None:
        raise ValueError(f"Drug not found: {drug_iri}")

    drug_classes = [str(c) for c in drug.class_iris]
    drug_props = drug.properties

    api_iri = _extract_object_prop(drug_props, "hasActiveIngredient")
    api_props: dict[str, Any] = {}
    api_individual = None
    if api_iri:
        api_individual = engine.get_individual(api_iri)
        if api_individual:
            api_props = _extract_api_properties(api_individual)

    # --- Rule Group 1: Drug Classification ---
    for rule_fn in drug_classification.ALL_RULES:
        r = rule_fn(drug_props=drug_props, api_props=api_props)
        if r.fired:
            result.rules_fired.append({
                "rule_id": r.rule_id, "rule_group": "drug_classification",
                "description": r.description, "inputs": r.inputs,
                "conclusion": r.conclusion, "regulation_ref": r.regulation_ref,
            })
            if "add_class" in r.conclusion:
                drug_classes.append(r.conclusion["add_class"])

    # --- Rule Group 2: Equipment Dedication ---
    inactivation_classes = api_props.get("inactivation_classes", [])
    oeb_classes = api_props.get("oeb_classes", [])
    has_prion_risk = drug_props.get("hasPrionRisk", False)

    dedication_conclusions = []
    for rule_fn in equipment_dedication.ALL_RULES:
        r = rule_fn(
            drug_classes=drug_classes,
            inactivation_classes=inactivation_classes,
            oeb_classes=oeb_classes,
            has_prion_risk=has_prion_risk,
        )
        if r.fired:
            result.rules_fired.append({
                "rule_id": r.rule_id, "rule_group": "equipment_dedication",
                "description": r.description, "inputs": r.inputs,
                "conclusion": r.conclusion, "regulation_ref": r.regulation_ref,
            })
            dedication_conclusions.append(r.conclusion)

    result.requires_dedication = resolve_dedication_conflict(dedication_conclusions)

    # --- Rule Group 4: Scenario Identification ---
    dosage_form = _extract_literal_prop(drug_props, "dosageForm")
    for rule_fn in scenario_identification.ALL_RULES:
        r = rule_fn(
            drug_classes=drug_classes,
            co_product_classes=[],
            is_shared=True,
            source_form=dosage_form,
            target_form=None,
        )
        if r.fired:
            result.rules_fired.append({
                "rule_id": r.rule_id, "rule_group": "scenario_identification",
                "description": r.description, "inputs": r.inputs,
                "conclusion": r.conclusion, "regulation_ref": r.regulation_ref,
            })
            result.scenarios.append({
                "scenario_iri": r.conclusion.get("scenario", ""),
                "scenario_name": r.conclusion.get("scenario", ""),
                "requirements": {
                    k: v for k, v in r.conclusion.items() if k != "scenario"
                },
            })

    # --- Rule Group 3: Contamination Risk (per equipment) ---
    pde_value = _extract_numeric_prop(api_props, "pde_mg_per_day")
    risk_levels = []

    for eq_iri in equipment_iris:
        eq = engine.get_individual(eq_iri)
        if eq is None:
            continue
        eq_props = eq.properties
        cleanability = _extract_cleanability(eq_props)
        area_type = _detect_area_type(eq_props)

        for pathway in ["residue", "airborne", "mechanical", "confusion"]:
            for rule_fn in contamination_risk.ALL_RULES:
                r = rule_fn(
                    pathway=pathway,
                    pde=pde_value,
                    cleanability_score=cleanability,
                    dosage_form=dosage_form,
                    area_type=area_type,
                    source_form=dosage_form,
                    target_form=None,
                )
                if r.fired:
                    result.rules_fired.append({
                        "rule_id": r.rule_id, "rule_group": "contamination_risk",
                        "description": r.description, "inputs": r.inputs,
                        "conclusion": r.conclusion, "regulation_ref": r.regulation_ref,
                    })
                    if "risk_level" in r.conclusion:
                        risk_levels.append(r.conclusion["risk_level"])

    result.risk_level = resolve_risk_level(risk_levels)

    # --- MACO Calculation ---
    if pde_value and pde_value > 0:
        try:
            mbs = 1000.0  # default batch size in grams
            tdd_next = _extract_numeric_prop(drug_props, "maximumDailyDose_mg") or 1000.0
            min_dose = _extract_numeric_prop(drug_props, "minimumTherapeuticDose_mg")
            ld50 = _extract_numeric_prop(api_props, "ld50_mg_per_kg")
            route = _extract_literal_prop(drug_props, "routeOfAdministration") or "oral"

            result.maco = calculate_maco(
                pde=pde_value, mbs=mbs, tdd_next=tdd_next,
                min_therapeutic_dose=min_dose, ld50=ld50, route=route,
            )
        except Exception as e:
            logger.warning("MACO calculation failed: %s", e)

    # --- Recommendations ---
    if result.requires_dedication:
        result.recommendations.append("设备必须专用化，不得共线生产")
    if any("HormonalSharedLineScenario" in s.get("scenario_iri", "") for s in result.scenarios):
        result.recommendations.append("需配置独立空气净化系统 (HVAC)")
    if result.risk_level == "HighRisk":
        result.recommendations.append("残留污染风险为高，需强化清洁验证")
    if result.maco and result.maco.value < 0.01:
        result.recommendations.append(f"MACO极低 ({result.maco.value:.6f} mg)，建议采用专属性分析方法 (HPLC)")

    return result


def _extract_object_prop(props: dict, prop_name: str) -> str | None:
    for key, val in props.items():
        if prop_name in key:
            if isinstance(val, dict) and "iri" in val:
                return val["iri"]
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def _extract_literal_prop(props: dict, prop_name: str) -> str | None:
    for key, val in props.items():
        if prop_name in key:
            return str(val) if val is not None else None
    return None


def _extract_numeric_prop(props: dict, prop_name: str) -> float | None:
    for key, val in props.items():
        if prop_name in key:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
    return None


def _extract_api_properties(api_individual) -> dict[str, Any]:
    props = api_individual.properties
    result: dict[str, Any] = {}

    toxicity_classes = []
    oeb_classes = []
    inactivation_classes = []
    for key, val in props.items():
        if "ToxicityProfile" in key or "Toxicity" in key:
            if isinstance(val, dict) and "iri" in val:
                toxicity_classes.append(val["iri"])
            elif isinstance(val, list):
                toxicity_classes.extend(v.get("iri", str(v)) for v in val if isinstance(v, dict))
        if "OEB" in key:
            if isinstance(val, dict) and "iri" in val:
                oeb_classes.append(val["iri"])
        if "Inactivation" in key or "inactivation" in key:
            if isinstance(val, dict) and "iri" in val:
                inactivation_classes.append(val["iri"])

        if isinstance(val, (int, float, str, bool)):
            result[key.rsplit("/", 1)[-1]] = val

    result["toxicity_profile_classes"] = toxicity_classes
    result["oeb_classes"] = oeb_classes
    result["inactivation_classes"] = inactivation_classes
    return result


def _extract_cleanability(eq_props: dict) -> int | None:
    for key, val in eq_props.items():
        if "cleanabilityScore" in key or "cleanability" in key:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def _detect_area_type(eq_props: dict) -> str:
    for key, val in eq_props.items():
        if "isInCleanArea" in key:
            if val is True or str(val).lower() == "true":
                return "clean"
            return "general"
    return "general"
