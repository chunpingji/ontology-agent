"""Prototype derive-stage tests (006 follow-on).

Anchored on the real case under discussion:

    单次给药毒性：小鼠静脉给药 LD50 ≈ 4 mg/kg，主要毒性靶器官为肝脏
    重复给药毒性：大鼠 28 天 NOAEL = 2 mg/kg/天（增重减缓、转氨酶升高）
    遗传毒性 / 致癌性 / 生殖毒性：未提供

Expectation: a NOAEL-derived point band of OEB3, escalated to a *recommended*
OEB4 because the genotox/carcinogen/repro endpoints are absent (provisional),
with a fully reconstructable provenance record. The three OWA-contrast tests
assert that *absent* (None) ≠ *negative* (False) — absence stays provisional,
an asserted negative closes the axis.
"""

from __future__ import annotations

from app.services.reasoning.derivation import DEFAULT_METHOD, ToxStudy, derive_facts


def _real_case(**overrides) -> ToxStudy:
    base = dict(
        noael_mg_kg_day=2.0,
        species="rat",
        study_duration_days=28,
        acute_ld50_mg_kg=4.0,
        acute_route="IV",
        acute_species="mouse",
        target_organs=("liver",),
        # genotoxic / carcinogenic / reproductive_toxicant left as None (absent)
    )
    base.update(overrides)
    return ToxStudy(**base)


def test_golden_case_oeb3_recommended_oeb4_provisional():
    r = derive_facts(_real_case())

    # PDE = 2·50 / (F1=5 · F2=10 · F3=10 · F4=1 · F5=1) = 0.2 mg/day = 200 µg/day
    assert r.pde_ug_day == 200.0
    # OEL = 200 / 10 m³ = 20 µg/m³ → band 3 (10 < 20 ≤ 100)
    assert r.oel_ug_m3 == 20.0
    assert r.oeb_band_point == 3

    # genotox absent → provisional, recommended one band more conservative
    assert r.provisional is True
    assert r.oeb_band_recommended == 4
    assert r.hazard_floor_band is None  # no *positive* endpoint asserts a floor
    assert any("genotoxicity" in reason for reason in r.provisional_reasons)


def test_golden_case_provenance_is_reconstructable():
    r = derive_facts(_real_case())
    p = r.provenance

    assert p["method"]["version"] == DEFAULT_METHOD.version
    # composite UF reconstructs the PDE exactly: 5·10·10·1·1 = 500
    assert p["factors"]["composite_UF"] == 500.0
    assert p["factors"]["F1_interspecies"] == 5.0   # rat
    assert p["factors"]["F3_duration"] == 10.0      # 28-day study
    assert p["bands"] == {"point": 3, "hazard_floor": None, "recommended": 4}
    assert p["provisional"]["data_gaps"] == [
        "genotoxicity", "carcinogenicity", "reproductive/developmental toxicity"
    ]
    # acute axis recorded separately, never folded into OEB
    assert "Category 1" in r.acute_tox_category


def test_genotox_positive_escalates_floor_and_closes_provisional():
    # Endpoints now *asserted* (genotox+, carc−, repro−): no data gap remains.
    r = derive_facts(_real_case(genotoxic=True, carcinogenic=False, reproductive_toxicant=False))
    assert r.provisional is False
    assert r.hazard_floor_band == 5                 # genotoxic → OEB5 floor (TTC)
    assert r.oeb_band_recommended == 5              # floor dominates the point band


def test_all_endpoints_negative_is_not_provisional_oeb3_stands():
    # OWA contrast: present-and-negative ≠ absent. Band 3 is NOT escalated.
    r = derive_facts(_real_case(genotoxic=False, carcinogenic=False, reproductive_toxicant=False))
    assert r.provisional is False
    assert r.oeb_band_point == 3
    assert r.oeb_band_recommended == 3


def test_missing_noael_is_unknown_not_zero():
    # OWA: absent NOAEL → None (not derivable), never a fabricated 0 → OEB1.
    r = derive_facts(_real_case(noael_mg_kg_day=None))
    assert r.pde_ug_day is None
    assert r.oel_ug_m3 is None
    assert r.oeb_band_point is None
    assert any("NOAEL" in reason for reason in r.provisional_reasons)
