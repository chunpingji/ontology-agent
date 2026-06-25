"""US2 — inferred PenicillinDrug wires into downstream dedication (US2-AS4, FR-011).

A penicillin that is *inferred* (via ChEBI alignment, not asserted on the drug)
must reach the equipment-dedication stage exactly as an asserted one would: the
engine appends the inferred class to `drug_classes` before the R-ED rules run, so
R-ED1 fires and `requires_dedication` is True. This proves the §8.0 upgrade is
behaviour-equivalent downstream, not just a new label.
"""

from __future__ import annotations

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix

PENICILLIN = "http://purl.obolibrary.org/obo/CHEBI_17334"


def _run(individuals: dict, drug_iri: str):
    return eng.run_assessment(matrix.StubEngine(individuals), drug_iri, [])


def test_inferred_penicillin_triggers_unconditional_dedication():
    res = _run({"d": matrix._drug("a"), "a": matrix._api(classes=[PENICILLIN])}, "d")

    # Inferred (not asserted on the drug) ...
    classifications = {
        r["rule_id"]: r["conclusion"]["add_class"]
        for r in res.rules_fired
        if r["rule_group"] == "drug_classification"
    }
    assert classifications.get("PenicillinDrug-suff") == "PenicillinDrug"

    # ... yet R-ED1 fires downstream exactly as for an asserted penicillin.
    ded_rules = {
        r["rule_id"] for r in res.rules_fired if r["rule_group"] == "equipment_dedication"
    }
    assert "R-ED1" in ded_rules
    assert res.requires_dedication is True
