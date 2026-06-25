"""US2 — close the "assertable-only / inexpressible" gap (US2-AS1~3, SC-001).

Hormonal / penicillin upgrade from *assertable-only* to *inferable*, and the new
AntineoplasticDrug class becomes inferable, all driven by an `external_alignment`
criterion: the drug's API individual is typed to a byte-verified ChEBI class
(T021), the engine surfaces that as an alignment fact under `hasActiveIngredient`,
and the criterion lights the risk class. The antineoplastic inference carries its
ATC L01 / ChEBI:35610 provenance (FR-002).

OWA (FR-010): an API with no external alignment leaves all three unlit (UNKNOWN),
never asserts a negative.
"""

from __future__ import annotations

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix

# Byte-verified ChEBI alignment targets (research.md R3, T021, 2026-06-24).
CHEBI = "http://purl.obolibrary.org/obo/CHEBI_"
HORMONE = CHEBI + "24621"
PENICILLIN = CHEBI + "17334"
ANTINEOPLASTIC = CHEBI + "35610"


def _classifications(result) -> dict[str, str]:
    return {
        r["rule_id"]: r["conclusion"]["add_class"]
        for r in result.rules_fired
        if r["rule_group"] == "drug_classification"
    }


def _fired(result, rule_id):
    for r in result.rules_fired:
        if r["rule_id"] == rule_id:
            return r
    return None


def _run(individuals: dict, drug_iri: str):
    return eng.run_assessment(matrix.StubEngine(individuals), drug_iri, [])


def test_hormonal_inferred_from_chebi_alignment():
    res = _run({"d": matrix._drug("a"), "a": matrix._api(classes=[HORMONE])}, "d")
    assert _classifications(res).get("HormonalDrug-suff") == "HormonalDrug"


def test_penicillin_inferred_from_chebi_alignment():
    res = _run({"d": matrix._drug("a"), "a": matrix._api(classes=[PENICILLIN])}, "d")
    assert _classifications(res).get("PenicillinDrug-suff") == "PenicillinDrug"


def test_antineoplastic_inferred_with_atc_chebi_provenance():
    res = _run({"d": matrix._drug("a"), "a": matrix._api(classes=[ANTINEOPLASTIC])}, "d")
    fired = _fired(res, "AntineoplasticDrug-suff")
    assert fired is not None
    assert fired["conclusion"]["add_class"] == "AntineoplasticDrug"
    # ChEBI alignment flows into the fired rule's inputs (FR-002 provenance).
    assert "CHEBI_35610" in str(fired["inputs"])
    # ATC L01 / ChEBI provenance recorded on the rule itself.
    ref = fired["regulation_ref"] or ""
    assert "L01" in ref or "35610" in ref


def test_no_alignment_leaves_all_three_unlit_owa():
    # API with no external alignment → UNKNOWN, none of the three fire.
    res = _run({"d": matrix._drug("a"), "a": matrix._api()}, "d")
    cls = _classifications(res)
    assert "HormonalDrug-suff" not in cls
    assert "PenicillinDrug-suff" not in cls
    assert "AntineoplasticDrug-suff" not in cls
