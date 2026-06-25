"""Zero-regression parity gate (T003 / T010, FR-012 / SC-001).

The golden master was captured from the *pre-refactor* hardcoded engine over
the full regression matrix (`matrix.build_cases()`). Every change to the
reasoning engine — most critically the T017 swap of the hardcoded
classification stage for the declarative interpreter — MUST keep this exact
projection: same `rules_fired` ids, same `requires_dedication`, same
`risk_level`, same `scenarios`. A diff here is a regression, full stop.

The OWA "否→未知" improvement is deliberately invisible at this projection
level: a class lights up IFF its criterion is TRUE, so FALSE and UNKNOWN both
leave it unlit and the output is identical (see `dc3_sensitization_absent`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.reasoning import engine as eng
from tests.test_reasoning import matrix

_GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_master.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", matrix.build_cases(), ids=lambda c: c[0])
def test_assessment_matches_golden_master(case):
    case_id, stub, drug_iri, eq_iris = case
    assert case_id in _GOLDEN, f"no golden master entry for {case_id!r}"
    result = eng.run_assessment(stub, drug_iri, eq_iris)
    assert matrix.project(result) == _GOLDEN[case_id]


def test_golden_master_covers_every_case():
    """Guard against silently dropping a case from the regression matrix."""
    case_ids = {c[0] for c in matrix.build_cases()}
    assert case_ids == set(_GOLDEN), "matrix cases and golden master drifted"
