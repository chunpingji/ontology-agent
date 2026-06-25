"""US1 — R-DC1~4 declarative auto-classification parity (US1-AS1~5, SC-001).

Asserts the interpreter-driven classification stage infers exactly the target
class the legacy hardcoded rule did, per trigger / non-trigger branch. The
risk class is *inferred from underlying properties*, never asserted on the drug.
"""

from __future__ import annotations

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix


def _classifications(result) -> dict[str, str]:
    """rule_id → inferred class, for the drug_classification stage only."""
    return {
        r["rule_id"]: r["conclusion"]["add_class"]
        for r in result.rules_fired
        if r["rule_group"] == "drug_classification"
    }


def _run(individuals: dict, drug_iri: str):
    return eng.run_assessment(matrix.StubEngine(individuals), drug_iri, [])


def test_r_dc1_genotoxicity_infers_cytotoxic():
    res = _run(
        {
            "d": matrix._drug("a"),
            "a": matrix._api(hasToxicityProfile={"iri": matrix.C("GenotoxicityProfile")}),
        },
        "d",
    )
    assert _classifications(res).get("R-DC1") == "CytotoxicDrug"


def test_r_dc2_oeb4_and_oeb5_infer_high_activity():
    for oeb in ("OEB4", "OEB5"):
        res = _run(
            {"d": matrix._drug("a"), "a": matrix._api(hasOEBClassification={"iri": matrix.C(oeb)})},
            "d",
        )
        assert _classifications(res).get("R-DC2") == "HighActivityDrug", oeb


def test_r_dc3_sensitization_threshold():
    # > 3 fires
    hi = _run({"d": matrix._drug(sensitization_level=4)}, "d")
    assert _classifications(hi).get("R-DC3") == "HighSensitizingDrug"
    # == below threshold does NOT fire
    lo = _run({"d": matrix._drug(sensitization_level=2)}, "d")
    assert "R-DC3" not in _classifications(lo)


def test_r_dc4_beta_lactam_ring_branches():
    yes = _run({"d": matrix._drug("a"), "a": matrix._api(has_beta_lactam_ring=True)}, "d")
    assert _classifications(yes).get("R-DC4") == "BetaLactamDrug"
    no = _run({"d": matrix._drug("a"), "a": matrix._api(has_beta_lactam_ring=False)}, "d")
    assert "R-DC4" not in _classifications(no)


def test_unrelated_drug_infers_no_classification():
    res = _run({"d": matrix._drug()}, "d")
    assert _classifications(res) == {}
