"""Reasoning engine orchestrator: loads entity data, runs rules, produces assessment."""

from __future__ import annotations

import logging
from typing import Any

from app.services.ontology_engine import OntologyEngine
from app.services.reasoning import interpreter, policy
from app.services.reasoning.calculators import MACOResult, calculate_maco
from app.services.reasoning.defaults import (
    default_classification_criteria,
    default_conflict_policies,
    default_decision_rules,
)
from app.services.reasoning.phase_context import phase_provenance_note

logger = logging.getLogger(__name__)


class AssessmentResult:
    def __init__(self):
        self.rules_fired: list[dict] = []
        self.scenarios: list[dict] = []
        self.requires_dedication: bool = False
        self.risk_level: str = "LowRisk"
        self.maco: MACOResult | None = None
        self.recommendations: list[str] = []
        # 研发阶段溯源上下文（007 US3，FR-011：仅标注，非 golden-master 对外字段）。
        # 缺省 None → 不参与 canonical 投影，对外 AssessmentResult 形状不变（SC-007）。
        self.phase_context: dict | None = None


def run_assessment(
    engine: OntologyEngine,
    drug_iri: str,
    equipment_iris: list[str],
    *,
    criteria: list | None = None,
    decision_rules: list | None = None,
    policies: list | None = None,
) -> AssessmentResult:
    # US3 (T034): the engine consumes the editable E11/E12/E13 artifacts the
    # assessment route loads from the active draft store. When absent (engine
    # unit tests / CLI) we fall back to the single-source in-code defaults, so
    # behaviour is byte-for-byte identical (FR-016; golden-master parity,
    # FR-012 / SC-004). Changing a threshold, adding a rule, or flipping a
    # conflict policy is therefore pure data — no code change reaches here.
    criteria = criteria if criteria is not None else default_classification_criteria()
    decision_rules = decision_rules if decision_rules is not None else default_decision_rules()
    policies = policies if policies is not None else default_conflict_policies()
    policy_by_dim = {p.dimension: p for p in policies}

    result = AssessmentResult()

    drug = engine.get_individual(drug_iri)
    if drug is None:
        raise ValueError(f"Drug not found: {drug_iri}")

    drug_classes = [str(c) for c in drug.class_iris]
    drug_props = drug.properties

    # 研发阶段：仅作溯源标注（FR-011/Q3）。读自药物个体的 hasDevelopmentPhase，挂到
    # result.phase_context；**不**进入 _build_facts → 任何规则前件都无法引用（红线）。
    phase_iri = _extract_object_prop(drug_props, "hasDevelopmentPhase")
    if phase_iri:
        result.phase_context = phase_provenance_note(phase_iri)

    api_iri = _extract_object_prop(drug_props, "hasActiveIngredient")
    api_props: dict[str, Any] = {}
    api_class_iris: list[str] = []
    api_individual = None
    if api_iri:
        api_individual = engine.get_individual(api_iri)
        if api_individual:
            api_props = _extract_api_properties(api_individual)
            # The API's own class memberships carry its external (ChEBI/ATC)
            # alignments — the signal the US2 `external_alignment` criteria read.
            api_class_iris = [str(c) for c in api_individual.class_iris]

    # --- Rule Group 1: Drug Classification (declarative, T017) ---
    # Hardcoded R-DC1~4 are demoted to declarative criteria evaluated by the
    # interpreter over a three-valued (OWA) fact view. A class lights up IFF its
    # criterion is TRUE; FALSE *and* UNKNOWN both leave it unlit, so the external
    # AssessmentResult shape is byte-for-byte preserved (golden-master parity).
    # Drug- and API-level scalars the production rules read, extracted up-front
    # so a single fact view backs classification *and* R-ED/R-SC/R-CP.
    dosage_form = _extract_literal_prop(drug_props, "dosageForm")
    pde_value = _extract_numeric_prop(api_props, "pde_mg_per_day")

    facts = _build_facts(drug_classes, drug_props, api_props, api_class_iris, dosage_form, pde_value)
    for criterion in criteria:
        if interpreter.evaluate(criterion.pattern, facts) is interpreter.TRUE:
            result.rules_fired.append({
                "rule_id": criterion.key, "rule_group": "drug_classification",
                "description": criterion.description,
                "inputs": interpreter.referenced_facts(criterion.pattern, facts),
                "conclusion": {"add_class": criterion.target_class},
                "regulation_ref": criterion.regulation_ref,
            })
            drug_classes.append(criterion.target_class)
            facts.drug_classes.append(criterion.target_class)

    # --- Rule Group 2: Equipment Dedication (declarative E12 + E13, T034) ---
    # R-ED1~6 are now interpreter ASTs over the shared fact view; the published
    # `dedication` conflict policy (E13) aggregates their conclusions.
    dedication_conclusions = _fire_group(decision_rules, "equipment_dedication", facts, result)
    result.requires_dedication = policy.resolve_dedication_conflict(
        dedication_conclusions, policy_by_dim.get("dedication")
    )

    # --- Rule Group 4: Scenario Identification (declarative E12, T034) ---
    for conclusion in _fire_group(decision_rules, "scenario_identification", facts, result):
        result.scenarios.append({
            "scenario_iri": conclusion.get("scenario", ""),
            "scenario_name": conclusion.get("scenario", ""),
            "requirements": {k: v for k, v in conclusion.items() if k != "scenario"},
        })

    # --- Rule Group 3: Contamination Risk (per equipment × pathway, T034) ---
    # Each (equipment, pathway) gets its own fact slice; R-CP1~4 evaluate over it
    # and the `risk_level` conflict policy (E13) takes the max-severity level.
    risk_levels: list[str] = []
    for eq_iri in equipment_iris:
        eq = engine.get_individual(eq_iri)
        if eq is None:
            continue
        eq_props = eq.properties
        cleanability = _extract_cleanability(eq_props)
        area_type = _detect_area_type(eq_props)

        for pathway in ["residue", "airborne", "mechanical", "confusion"]:
            cp_facts = _facts_for_pathway(facts, pathway, cleanability, area_type)
            for conclusion in _fire_group(decision_rules, "contamination_risk", cp_facts, result):
                if "risk_level" in conclusion:
                    risk_levels.append(conclusion["risk_level"])

    result.risk_level = policy.resolve_risk_level(risk_levels, policy_by_dim.get("risk_level"))

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


def _fire_group(
    decision_rules: list, group: str, facts: interpreter.Facts, result: AssessmentResult
) -> list[dict]:
    """Evaluate every E12 rule in `group` against `facts`; record the TRUE ones in
    `result.rules_fired` and return their consequents (deterministic
    priority→key order) for the E13 conflict policy to aggregate.

    A rule fires IFF its antecedent evaluates to TRUE — FALSE *and* UNKNOWN both
    leave it unfired (OWA 否→未知), so the external AssessmentResult shape is
    preserved (golden-master parity, FR-012)."""
    conclusions: list[dict] = []
    ordered = sorted(
        (r for r in decision_rules if r.rule_group == group),
        key=lambda r: (r.priority, r.key),
    )
    for rule in ordered:
        if interpreter.evaluate(rule.antecedent, facts) is interpreter.TRUE:
            result.rules_fired.append({
                "rule_id": rule.key, "rule_group": group,
                "description": rule.description,
                "inputs": interpreter.referenced_facts(rule.antecedent, facts),
                "conclusion": rule.consequent,
                "regulation_ref": rule.regulation_ref,
            })
            conclusions.append(rule.consequent)
    return conclusions


def _build_facts(
    drug_classes: list[str],
    drug_props: dict,
    api_props: dict,
    api_class_iris: list[str] | None,
    dosage_form: str | None,
    pde_value: float | None,
) -> interpreter.Facts:
    """Normalise the drug + API individuals into the interpreter's fact view for
    BOTH classification (R-DC) and the production rules (R-ED/R-SC/R-CP).

    OWA (FR-010): a fact is asserted ONLY when the source individual actually
    carries it — absence surfaces as a missing key (→ UNKNOWN), never a
    fabricated 0/False. The one legacy exception kept for parity is
    `hasPrionRisk`, which the original engine defaulted to False.
    """
    relations: dict[str, list] = {}
    data_values: dict[str, Any] = {}
    alignments: dict[str, list] = {}
    scalars: dict[str, Any] = {}

    # -- classification relations / data values (R-DC) --
    tox = api_props.get("toxicity_profile_classes")
    if tox is not None:
        relations["hasToxicityProfile"] = tox
    oeb = api_props.get("oeb_classes")
    if oeb is not None:
        relations["hasOEBClassification"] = oeb  # reused by R-ED5

    if "sensitization_level" in drug_props:
        data_values["sensitizationLevel"] = drug_props["sensitization_level"]
    if "has_beta_lactam_ring" in api_props:
        data_values["hasBetaLactamRing"] = api_props["has_beta_lactam_ring"]

    # US2: the API's class memberships (ChEBI/ATC external classes) are the
    # alignment fact the `external_alignment` criteria evaluate via the
    # `hasActiveIngredient` relation. Asserted only when an API individual exists
    # (key absent → UNKNOWN, never a negative assertion).
    if api_class_iris:
        alignments["hasActiveIngredient"] = list(api_class_iris)

    # -- production-rule facts (R-ED / R-SC / R-CP) --
    # Object relations the dedication/scenario rules read. Empty list → no
    # asserted filler → UNKNOWN under class_membership (parity with legacy
    # `any(... for c in [])` → not fired).
    relations["hasInactivationProfile"] = api_props.get("inactivation_classes", [])  # R-ED2/4
    relations["coProduct"] = []  # single-product assessment carries no co-products (R-SC1)
    scalars["isShared"] = True  # legacy `is_shared=True` default (R-SC2/3/5/6/7/8)
    scalars["hasPrionRisk"] = drug_props.get("hasPrionRisk", False)  # legacy default (R-ED3)
    if dosage_form is not None:
        scalars["dosageForm"] = dosage_form  # R-CP2/3
    if pde_value is not None:
        scalars["pde"] = pde_value  # R-CP1
    # Dosage-form relation (R-SC8 / R-CP4) needs BOTH a source and a target form;
    # this single-product assessment has no target form, so `formRelation` stays
    # absent → UNKNOWN → those two rules stay unfired (parity with the legacy
    # `target_form=None` wiring).

    return interpreter.Facts(
        drug_classes=list(drug_classes),
        relations=relations,
        data_values=data_values,
        alignments=alignments,
        scalars=scalars,
    )


def _facts_for_pathway(
    base: interpreter.Facts, pathway: str, cleanability: int | None, area_type: str
) -> interpreter.Facts:
    """Clone `base` with the per-(equipment, pathway) contamination scalars set
    (the drug/API-level scalars in `base` are carried through unchanged)."""
    scalars = dict(base.scalars)
    scalars["pathway"] = pathway
    scalars["areaType"] = area_type
    if cleanability is not None:
        scalars["cleanability"] = cleanability  # R-CP1/3
    return interpreter.Facts(
        drug_classes=base.drug_classes,
        relations=base.relations,
        data_values=base.data_values,
        alignments=base.alignments,
        scalars=scalars,
    )


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
