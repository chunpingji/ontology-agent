"""US1 — per-rule provenance on every inferred classification (FR-002 / SC-005).

Each inferred risk class must carry, in its `rules_fired` entry, a non-empty
`rule_id` (e.g. R-DC1), a non-empty `regulation_ref` (the GMP citation), and a
non-empty `inputs` (the underlying facts the criterion fired on). This is the
regulatory traceability the §8.0 upgrade must preserve through declarativisation.
"""

from __future__ import annotations

import pytest

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix


def _run(individuals: dict, drug_iri: str):
    return eng.run_assessment(matrix.StubEngine(individuals), drug_iri, [])


_CASES = {
    "R-DC1": ({"d": matrix._drug("a"),
               "a": matrix._api(hasToxicityProfile={"iri": matrix.C("GenotoxicityProfile")})}, "d"),
    "R-DC2": ({"d": matrix._drug("a"),
               "a": matrix._api(hasOEBClassification={"iri": matrix.C("OEB4")})}, "d"),
    "R-DC3": ({"d": matrix._drug(sensitization_level=4)}, "d"),
    "R-DC4": ({"d": matrix._drug("a"), "a": matrix._api(has_beta_lactam_ring=True)}, "d"),
}


@pytest.mark.parametrize("rule_id", sorted(_CASES))
def test_inferred_class_carries_full_provenance(rule_id):
    individuals, drug_iri = _CASES[rule_id]
    res = _run(individuals, drug_iri)
    fired = {r["rule_id"]: r for r in res.rules_fired if r["rule_group"] == "drug_classification"}
    assert rule_id in fired, f"{rule_id} did not fire"
    entry = fired[rule_id]
    assert entry["rule_id"] == rule_id
    assert entry["regulation_ref"], "regulation_ref must be non-empty (GMP traceability)"
    assert entry["regulation_ref"].startswith("CFDI 2023-03"), entry["regulation_ref"]
    assert entry["inputs"], "inputs (fired-on facts) must be non-empty"
    assert entry["conclusion"].get("add_class"), "conclusion must name the inferred class"
