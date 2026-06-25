"""US1 — OWA "否→未知" for a missing attribute (FR-010 / SC-006).

When `sensitizationLevel` is *absent*, R-DC3 MUST evaluate to UNKNOWN — the
class is not lit and no negative is asserted. This is the semantic improvement
over the legacy `drug_props.get("sensitization_level", 0)` which fabricated a
0 and silently produced a (closed-world) FALSE. The improvement is invisible at
the assessment-output level (both leave the class unlit), so it is asserted at
two layers: the interpreter verdict (UNKNOWN, *not* FALSE) and the engine output
(R-DC3 not fired).
"""

from __future__ import annotations

from app.services.reasoning import engine as eng
from app.services.reasoning import interpreter
from app.services.reasoning.defaults import DEFAULT_CLASSIFICATION_CRITERIA
from tests.test_reasoning import matrix

_R_DC3 = next(c for c in DEFAULT_CLASSIFICATION_CRITERIA if c.key == "R-DC3")


def test_absent_sensitization_is_unknown_not_false():
    facts = interpreter.Facts()  # no sensitizationLevel asserted
    assert interpreter.evaluate(_R_DC3.pattern, facts) is interpreter.UNKNOWN


def test_present_below_threshold_is_false_not_unknown():
    facts = interpreter.Facts(data_values={"sensitizationLevel": 2})
    assert interpreter.evaluate(_R_DC3.pattern, facts) is interpreter.FALSE


def test_engine_does_not_light_class_on_absent_attribute():
    res = eng.run_assessment(matrix.StubEngine({"d": matrix._drug()}), "d", [])
    fired = {r["rule_id"] for r in res.rules_fired if r["rule_group"] == "drug_classification"}
    assert "R-DC3" not in fired  # unlit — neither asserted true nor false


def test_engine_path_owa_matches_below_threshold_at_output_level():
    """Absent and below-threshold differ in verdict (UNKNOWN vs FALSE) but are
    observationally identical at the output level — both leave R-DC3 unlit."""
    absent = eng.run_assessment(matrix.StubEngine({"d": matrix._drug()}), "d", [])
    below = eng.run_assessment(matrix.StubEngine({"d": matrix._drug(sensitization_level=2)}), "d", [])

    def dc(res):
        return {r["rule_id"] for r in res.rules_fired if r["rule_group"] == "drug_classification"}

    assert "R-DC3" not in dc(absent)
    assert "R-DC3" not in dc(below)
