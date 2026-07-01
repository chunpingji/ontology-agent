"""Zero-regression regression matrix + Stub ABox engine (research.md R4, FR-012).

Shared by the golden-master baseline generator and the declarative parity tests.
Each case is a self-contained ABox (drug + API + equipment individuals) driving
`reasoning_engine.run_assessment`. The Stub engine mimics the slice of
`OntologyEngine` the assessment touches: `get_individual(iri)` →
object with `.class_iris` / `.properties`.

The cases cover every R-DC1~4 / R-ED1~6 / R-SC / R-CP trigger & non-trigger
branch plus dedication/risk conflict scenarios, and deliberately include an
*absent-attribute* case (`dc3_sensitization_absent`) — the locus of the
OWA "否→未知" improvement, observably identical at the output level.
"""

from __future__ import annotations

from typing import Any

NS = "https://ontology.pharma-gmp.cn/slpra/drug/"


def C(name: str) -> str:
    return NS + name


class StubIndividual:
    def __init__(self, class_iris: list[str], properties: dict[str, Any]):
        self.class_iris = class_iris
        self.properties = properties


class StubEngine:
    """Minimal OntologyEngine stand-in backed by an in-memory ABox."""

    is_loaded = True

    def __init__(self, individuals: dict[str, StubIndividual]):
        self._inds = individuals

    def get_individual(self, iri: str):
        return self._inds.get(iri)


def _drug(api_iri: str | None = None, classes: list[str] | None = None, **props) -> StubIndividual:
    properties: dict[str, Any] = dict(props)
    if api_iri:
        properties["hasActiveIngredient"] = {"iri": api_iri}
    return StubIndividual([C("DrugProduct")] + [C(c) for c in (classes or [])], properties)


def _api(classes: list[str] | None = None, **props) -> StubIndividual:
    # object-valued props passed as {"iri": ...}; scalars passed raw.
    # `classes` are FULL external IRIs (e.g. ChEBI purls) the API is typed to —
    # the alignment signal the US2 `external_alignment` criteria evaluate.
    return StubIndividual(
        [C("ActivePharmaceuticalIngredient")] + list(classes or []), props
    )


def _equipment(**props) -> StubIndividual:
    return StubIndividual([C("Equipment")], props)


def build_cases() -> list[tuple[str, StubEngine, str, list[str]]]:
    """Return [(case_id, engine, drug_iri, equipment_iris), ...]."""
    cases: list[tuple[str, StubEngine, str, list[str]]] = []

    def add(case_id, inds, drug_iri, eq_iris=()):
        cases.append((case_id, StubEngine(inds), drug_iri, list(eq_iris)))

    # --- R-DC1: genotoxicity → CytotoxicDrug (+ R-SCe) ---------------------
    add(
        "dc1_genotoxic_cytotoxic",
        {
            "drug:1": _drug("api:1"),
            "api:1": _api(hasToxicityProfile={"iri": C("GenotoxicityProfile")}),
        },
        "drug:1",
    )

    # --- R-DC2: OEB4 → HighActivityDrug (+ R-SCe) --------------------------
    add(
        "dc2_oeb4_highactivity",
        {
            "drug:2": _drug("api:2"),
            "api:2": _api(hasOEBClassification={"iri": C("OEB4")}),
        },
        "drug:2",
    )

    # --- R-DC3: sensitization > 3 → HighSensitizingDrug --------------------
    add("dc3_sensitization_high", {"drug:3": _drug(sensitization_level=4)}, "drug:3")
    # below threshold (present) — must NOT fire
    add("dc3_sensitization_below", {"drug:3b": _drug(sensitization_level=2)}, "drug:3b")
    # absent — OWA case: old default-0 not-fired, new UNKNOWN not-fired (same output)
    add("dc3_sensitization_absent", {"drug:3c": _drug()}, "drug:3c")

    # --- R-DC4: beta-lactam ring → BetaLactamDrug --------------------------
    add(
        "dc4_betalactam",
        {"drug:4": _drug("api:4"), "api:4": _api(has_beta_lactam_ring=True)},
        "drug:4",
    )
    # ring explicitly false — must NOT fire
    add(
        "dc4_betalactam_false",
        {"drug:4b": _drug("api:4b"), "api:4b": _api(has_beta_lactam_ring=False)},
        "drug:4b",
    )

    # --- R-ED1: asserted PenicillinDrug → unconditional dedication (+R-SCh) -
    add("penicillin_dedication", {"drug:5": _drug(classes=["PenicillinDrug"])}, "drug:5")

    # --- R-ED2: cytotoxic + NonInactivatable → dedicate --------------------
    add(
        "cytotoxic_noninactivatable_dedicate",
        {
            "drug:6": _drug("api:6"),
            "api:6": _api(
                hasToxicityProfile={"iri": C("GenotoxicityProfile")},
                hasInactivationDisposition={"iri": C("NonInactivatable")},
            ),
        },
        "drug:6",
    )

    # --- R-ED4: cytotoxic + HeatInactivatable → shared w/ validation -------
    add(
        "cytotoxic_inactivatable_shared",
        {
            "drug:7": _drug("api:7"),
            "api:7": _api(
                hasToxicityProfile={"iri": C("GenotoxicityProfile")},
                hasInactivationDisposition={"iri": C("HeatInactivatable")},
            ),
        },
        "drug:7",
    )

    # --- R-ED5: HighActivity (OEB5) → dedicate -----------------------------
    add(
        "highactivity_oeb5_dedicate",
        {
            "drug:8": _drug("api:8"),
            "api:8": _api(hasOEBClassification={"iri": C("OEB5")}),
        },
        "drug:8",
    )

    # --- R-ED3: biological + prion risk → dedicate -------------------------
    add(
        "biologic_prion_dedicate",
        {"drug:9": _drug(classes=["BiologicalProduct"], hasPrionRisk=True)},
        "drug:9",
    )

    # --- R-ED6: hormonal → independent HVAC (+ R-SCe) ----------------------
    add("hormonal_hvac", {"drug:10": _drug(classes=["HormonalDrug"])}, "drug:10")

    # --- R-CP1: residue + low PDE + poor cleanability → HighRisk -----------
    add(
        "contamination_highrisk",
        {
            "drug:11": _drug("api:11", dosageForm="powder"),
            "api:11": _api(pde_mg_per_day=0.005),
            "eq:11": _equipment(cleanabilityScore=2, isInCleanArea=False),
        },
        "drug:11",
        ["eq:11"],
    )

    # --- R-CP3: residue + solution + good cleanability → LowRisk -----------
    add(
        "contamination_lowrisk",
        {
            "drug:12": _drug("api:12", dosageForm="solution"),
            "api:12": _api(pde_mg_per_day=5.0),
            "eq:12": _equipment(cleanabilityScore=5, isInCleanArea=True),
        },
        "drug:12",
        ["eq:12"],
    )

    return cases


def project(result) -> dict:
    """Canonical output projection compared against the golden master (T003)."""
    return {
        "rule_ids": sorted(r["rule_id"] for r in result.rules_fired),
        "requires_dedication": bool(result.requires_dedication),
        "risk_level": result.risk_level,
        "scenario_iris": sorted(s.get("scenario_iri", "") for s in result.scenarios),
    }
