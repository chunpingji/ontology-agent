"""Deterministic toxicology → potency derivation (006 follow-on prototype).

The declarative interpreter (`interpreter.py`) is, by design, **arithmetic-free**:
its algebra is `and/or` + a fixed set of predicate ops over a closed `Facts`
vocabulary, returning Ternary. That is what keeps the rule layer decidable and
auditable. Potency banding, however, is a *derivation* —

    PDE/ADE = NOAEL × BW / (F1·F2·F3·F4·F5)      (ISPE Risk-MaPP / EMA / ICH Q3C)
    OEL     = PDE / V_breathing
    OEB     = band(OEL)

— so it must NOT live in the AST. Instead this module is a deterministic
*derive stage* that runs **before** the interpreter and materialises the result
as ordinary scalar facts (`oeb_band`, `oel_ug_m3`, …). The existing declarative
criteria then consume those via the normal `literal_cmp` op — zero new
interpreter ops, decidability untouched. This is the same socket `_build_facts`
already opens for the pre-computed `pde` scalar; here we *compute* it.

Three invariants this prototype is built to honour:

  • Reproducible, no LLM. The function is pure; every factor it applies is
    recorded in `provenance` so a conclusion is reconstructable byte-for-byte.
  • OWA / data-gap = provisional, never a fabricated negative. A NOAEL-derived
    band is *necessary-but-not-sufficient* for "highly potent": a missing
    special-hazard endpoint (genotox/carcinogen/repro) could only push the band
    *up*. So absence ⇒ `provisional=True` + a one-band-more-conservative
    recommendation, NOT a closed "not potent". (Mirrors `test_owa_unknown`.)
  • Acute ≠ chronic. LD50 (acute lethality) is banded on a *separate* axis and
    never folded into the occupational OEB.

The `DerivationMethod` parameter set is what would become the versioned E14
artifact (UF table, OEL cutoffs, BW) — editable as data, single-sourced, the
derived fact pinning the method version used.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# E14 (prototype) — versioned derivation method / parameter set
# --------------------------------------------------------------------------- #
# ICH Q3C(R8) F1 interspecies allometric factors; unknown species → conservative.
_SPECIES_FACTOR: dict[str, float] = {
    "human": 1.0, "monkey": 2.0, "dog": 2.0, "rabbit": 2.5, "rat": 5.0, "mouse": 12.0,
}
# F3 sub-chronic→chronic ladder: ascending (max_study_days, factor); above the
# last rung → `duration_factor_chronic`. A 28-day study lands on factor 10.
_DURATION_LADDER: tuple[tuple[int, float], ...] = ((28, 10.0), (90, 5.0), (180, 2.0))
# OEL (µg/m³) → OEB band: descending (threshold, band); OEL ≤ last → `oeb_floor_band`.
_OEL_CUTOFFS: tuple[tuple[float, int], ...] = ((1000.0, 1), (100.0, 2), (10.0, 3), (1.0, 4))


@dataclass(frozen=True)
class DerivationMethod:
    """Declarative parameters for the ADE/OEL/OEB pipeline (future E14 row)."""

    method_id: str = "ADE-OEB/ISPE-RiskMaPP+ICH-Q3C"
    version: str = "0.1.0-prototype"
    default_body_weight_kg: float = 50.0          # EMA default adult worker
    breathing_volume_m3: float = 10.0             # 8h shift, light work
    intraindividual_factor: float = 10.0          # F2
    severity_factor_severe: float = 10.0          # F4 when a severe endpoint is positive
    severity_factor_default: float = 1.0          # F4 baseline
    loael_extra_factor: float = 10.0              # F5 when only a LOAEL is available
    oeb_floor_band: int = 5                        # OEL ≤ smallest cutoff → OEB5
    species_factor: dict = field(default_factory=lambda: dict(_SPECIES_FACTOR))
    species_factor_unknown: float = 10.0
    duration_ladder: tuple = _DURATION_LADDER
    duration_factor_chronic: float = 1.0
    oel_cutoffs: tuple = _OEL_CUTOFFS
    # Band a positive special-hazard endpoint guarantees (a "hazard floor").
    genotoxic_floor_band: int = 5
    carcinogenic_floor_band: int = 4
    reproductive_floor_band: int = 4
    regulation_ref: str = (
        "ISPE Risk-MaPP (2nd ed.); EMA EMA/CHMP/CVMP/SWP/169430/2012; ICH Q3C(R8) factors"
    )


DEFAULT_METHOD = DerivationMethod()


# --------------------------------------------------------------------------- #
# Inputs / outputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToxStudy:
    """Normalised tox-study facts. Tri-state endpoints use `None = not tested =
    UNKNOWN` (the OWA convention), distinct from `False = tested negative`."""

    # repeat-dose study → chronic occupational potency (OEB)
    noael_mg_kg_day: float | None
    species: str
    study_duration_days: int
    noael_is_loael: bool = False
    # acute lethality → separate axis, never folded into OEB
    acute_ld50_mg_kg: float | None = None
    acute_route: str | None = None
    acute_species: str | None = None
    # special-hazard endpoints (None ⇒ absent ⇒ provisional)
    genotoxic: bool | None = None
    carcinogenic: bool | None = None
    reproductive_toxicant: bool | None = None
    # context (recorded, not computed on)
    target_organs: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class DerivationResult:
    pde_ug_day: float | None
    oel_ug_m3: float | None
    oeb_band_point: int | None        # estimate from the data actually present
    hazard_floor_band: int | None     # floor implied by *positive* special endpoints
    oeb_band_recommended: int | None  # the band to engineer to (provisional-aware)
    provisional: bool
    provisional_reasons: tuple[str, ...]
    acute_tox_category: str | None
    derived_scalars: dict             # merge into interpreter Facts.scalars
    provenance: dict                  # fully reconstructable audit record


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _duration_factor(days: int, m: DerivationMethod) -> float:
    for max_days, factor in m.duration_ladder:
        if days <= max_days:
            return factor
    return m.duration_factor_chronic


def _band_from_oel(oel: float, m: DerivationMethod) -> int:
    for threshold, band in m.oel_cutoffs:  # descending thresholds
        if oel > threshold:
            return band
    return m.oeb_floor_band


def _acute_category(ld50: float | None, route: str | None) -> str | None:
    if ld50 is None:
        return None
    if ld50 <= 5:
        cat = "Category 1 (extremely toxic)"
    elif ld50 <= 50:
        cat = "Category 2 (highly toxic)"
    elif ld50 <= 300:
        cat = "Category 3"
    elif ld50 <= 2000:
        cat = "Category 4"
    elif ld50 <= 5000:
        cat = "Category 5"
    else:
        cat = "unclassified"
    return (
        f"GHS-analog acute {cat} (LD50={ld50} mg/kg, {route or 'route?'}; "
        "note: GHS reference routes are oral/dermal/inhalation — IV banded by analogy)"
    )


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #
def derive_facts(study: ToxStudy, method: DerivationMethod = DEFAULT_METHOD) -> DerivationResult:
    """Derive ADE/OEL/OEB from a tox study, deterministically and with provenance."""
    reasons: list[str] = []
    factors: dict[str, float] = {}
    pde = oel = None
    point_band: int | None = None

    # --- chronic axis: PDE/ADE → OEL → OEB band -----------------------------
    if study.noael_mg_kg_day is None:
        # OWA: a missing input is UNKNOWN, never a fabricated 0.
        reasons.append("no repeat-dose NOAEL/LOAEL asserted → chronic potency not derivable")
    else:
        bw = method.default_body_weight_kg
        f1 = method.species_factor.get(study.species.lower(), method.species_factor_unknown)
        f2 = method.intraindividual_factor
        f3 = _duration_factor(study.study_duration_days, method)
        # F4 severity per ICH Q3C is for severe *non-genotoxic* findings
        # (carcinogenicity / teratogenicity). Genotoxicity is handled as a band
        # floor (TTC), not via F4 — so it does not double-count here.
        severe = (study.carcinogenic is True) or (study.reproductive_toxicant is True)
        f4 = method.severity_factor_severe if severe else method.severity_factor_default
        f5 = method.loael_extra_factor if study.noael_is_loael else 1.0
        composite = f1 * f2 * f3 * f4 * f5
        pde = (study.noael_mg_kg_day * bw) / composite * 1000.0  # mg/day → µg/day
        oel = pde / method.breathing_volume_m3
        point_band = _band_from_oel(oel, method)
        factors = {
            "BW_kg": bw, "F1_interspecies": f1, "F2_intraindividual": f2,
            "F3_duration": f3, "F4_severity": f4, "F5_loael": f5,
            "composite_UF": composite, "breathing_volume_m3": method.breathing_volume_m3,
        }

    # --- hazard floor from *positive* special-hazard endpoints --------------
    floor_candidates: list[int] = []
    if study.genotoxic is True:
        floor_candidates.append(method.genotoxic_floor_band)
    if study.carcinogenic is True:
        floor_candidates.append(method.carcinogenic_floor_band)
    if study.reproductive_toxicant is True:
        floor_candidates.append(method.reproductive_floor_band)
    hazard_floor = max(floor_candidates) if floor_candidates else None

    # --- OWA data-gap → provisional -----------------------------------------
    gaps: list[str] = []
    if study.genotoxic is None:
        gaps.append("genotoxicity")
    if study.carcinogenic is None:
        gaps.append("carcinogenicity")
    if study.reproductive_toxicant is None:
        gaps.append("reproductive/developmental toxicity")
    provisional = bool(gaps)
    if provisional:
        reasons.append(
            "special-hazard endpoint(s) absent (" + ", ".join(gaps) + ") — severity "
            "factor F4 / TTC floor not excludable; could escalate the band. Handle at "
            "the recommended (one-band-more-conservative) band until characterised."
        )

    # --- combine: recommended (engineer-to) band ----------------------------
    base_candidates = [b for b in (point_band, hazard_floor) if b is not None]
    base_band = max(base_candidates) if base_candidates else None
    if base_band is not None and provisional:
        recommended = min(method.oeb_floor_band, base_band + 1)
    else:
        recommended = base_band

    acute = _acute_category(study.acute_ld50_mg_kg, study.acute_route)

    # Scalars to merge into interpreter Facts.scalars. NOTE: named `pde_ug_day`
    # (not `pde`) on purpose — wiring into the engine's existing `pde` scalar
    # (R-CP1) must first confirm that rule's unit; left to the engine-integration step.
    derived_scalars = {
        "oeb_band": recommended,           # provisional-aware band a HighlyPotent criterion reads
        "oeb_band_point": point_band,
        "oel_ug_m3": oel,
        "pde_ug_day": pde,
        "potency_provisional": provisional,
    }

    provenance = {
        "method": {
            "id": method.method_id, "version": method.version,
            "regulation_ref": method.regulation_ref,
        },
        "formula": "PDE = NOAEL·BW / (F1·F2·F3·F4·F5); OEL = PDE / V; OEB = band(OEL)",
        "inputs": {
            "noael_mg_kg_day": study.noael_mg_kg_day, "species": study.species,
            "study_duration_days": study.study_duration_days,
            "noael_is_loael": study.noael_is_loael,
            "acute_ld50_mg_kg": study.acute_ld50_mg_kg, "acute_route": study.acute_route,
            "genotoxic": study.genotoxic, "carcinogenic": study.carcinogenic,
            "reproductive_toxicant": study.reproductive_toxicant,
            "target_organs": list(study.target_organs),
        },
        "factors": factors,
        "intermediate": {"pde_ug_day": pde, "oel_ug_m3": oel},
        "bands": {
            "point": point_band, "hazard_floor": hazard_floor, "recommended": recommended,
        },
        "provisional": {"is_provisional": provisional, "data_gaps": gaps, "reasons": reasons},
        "acute_axis": {"category": acute},
    }

    return DerivationResult(
        pde_ug_day=pde,
        oel_ug_m3=oel,
        oeb_band_point=point_band,
        hazard_floor_band=hazard_floor,
        oeb_band_recommended=recommended,
        provisional=provisional,
        provisional_reasons=tuple(reasons),
        acute_tox_category=acute,
        derived_scalars=derived_scalars,
        provenance=provenance,
    )
