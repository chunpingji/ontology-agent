"""Unit tests for the declarative per-value transforms (014, R6, T039).

Focus on the *transform-failure edge case* the spec calls out: a transform
failure is **per-value and non-fatal** — the offending value is returned
unchanged with an issue ``note`` while sibling values on the same record still
succeed and the row is never discarded. Also pins the four transform types
(``none``/``controlled_vocab``/``pattern``/``cast``) and the static
``validate_transform_config`` well-formedness gate used before extraction.
"""

from __future__ import annotations

import pytest

from app.services.extraction.transforms import (
    apply_transform,
    validate_transform_config,
)


# --------------------------------------------------------------------------- #
# cast — coercion, with non-fatal failure notes
# --------------------------------------------------------------------------- #
class TestCast:
    def test_integer_cast_succeeds(self):
        out = apply_transform("cast", {"to": "integer"}, "42")
        assert out.value == 42
        assert out.ok and out.note is None

    def test_decimal_cast_succeeds(self):
        out = apply_transform("cast", {"to": "decimal"}, "1.80")
        assert out.value == 1.8
        assert out.ok

    def test_boolean_cast_chinese_token(self):
        assert apply_transform("cast", {"to": "boolean"}, "是").value is True
        assert apply_transform("cast", {"to": "boolean"}, "否").value is False

    def test_date_cast_normalizes_iso(self):
        out = apply_transform("cast", {"to": "date"}, "2026-07-03")
        assert out.value == "2026-07-03"
        assert out.ok

    def test_cast_failure_returns_value_with_note(self):
        # "abc" cannot become an integer → value kept, issue note attached.
        out = apply_transform("cast", {"to": "integer"}, "abc")
        assert out.value == "abc"  # unchanged — non-fatal
        assert not out.ok
        assert out.note and "cast→integer" in out.note

    def test_boolean_cast_failure_noted(self):
        out = apply_transform("cast", {"to": "boolean"}, "maybe")
        assert out.value == "maybe"
        assert not out.ok

    def test_cast_missing_target_noted(self):
        out = apply_transform("cast", {}, "5")
        assert out.value == "5"
        assert not out.ok
        assert "目标类型" in out.note


# --------------------------------------------------------------------------- #
# controlled_vocab — explicit map, shared vocab hint, and no-match note
# --------------------------------------------------------------------------- #
class TestControlledVocab:
    def test_explicit_map_hit(self):
        out = apply_transform("controlled_vocab", {"map": {"高": "OEB5"}}, "高")
        assert out.value == "OEB5"
        assert out.ok

    def test_vocab_hint_normalizes_case(self):
        out = apply_transform("controlled_vocab", {"vocab": "oeb"}, "oeb3")
        assert out.value == "OEB3"
        assert out.ok

    def test_shared_scan_without_hint(self):
        # No config → scan all shared vocabularies; case/space tolerant.
        out = apply_transform("controlled_vocab", None, "316l不锈钢")
        assert out.value == "316L不锈钢"
        assert out.ok

    def test_no_match_returns_value_with_note(self):
        out = apply_transform("controlled_vocab", {"vocab": "oeb"}, "OEB9")
        assert out.value == "OEB9"  # unchanged — non-fatal
        assert not out.ok
        assert out.note and "无匹配取值" in out.note

    def test_no_match_without_hint_noted(self):
        out = apply_transform("controlled_vocab", None, "搅拌釜")
        assert out.value == "搅拌釜"
        assert not out.ok


# --------------------------------------------------------------------------- #
# pattern — validation only; value is kept either way
# --------------------------------------------------------------------------- #
class TestPattern:
    @pytest.mark.parametrize("value", [None, "abc", "123"])
    def test_pattern_is_refused_for_every_value(self, value):
        with pytest.raises(ValueError, match="EXECUTABLE_PATTERN_RETIRED"):
            apply_transform("pattern", {"pattern": "[0-9]+"}, value)


# --------------------------------------------------------------------------- #
# none / None / unknown / None-value passthrough
# --------------------------------------------------------------------------- #
class TestPassthroughAndUnknown:
    def test_none_transform_passes_through(self):
        assert apply_transform("none", None, "x").value == "x"
        assert apply_transform(None, None, "x").value == "x"

    def test_none_value_short_circuits_before_cast(self):
        # A missing source value passes untouched even under a cast transform.
        out = apply_transform("cast", {"to": "integer"}, None)
        assert out.value is None
        assert out.ok

    def test_unknown_transform_type_noted(self):
        out = apply_transform("bogus", None, "x")
        assert out.value == "x"
        assert not out.ok
        assert "未知 transform_type" in out.note


# --------------------------------------------------------------------------- #
# The headline edge case — sibling values on one record survive a failure
# --------------------------------------------------------------------------- #
class TestSiblingValuesSurviveFailure:
    def test_failed_cast_does_not_taint_sibling(self):
        record = {"pde": "1.80", "count": "not-a-number"}
        good = apply_transform("cast", {"to": "decimal"}, record["pde"])
        bad = apply_transform("cast", {"to": "integer"}, record["count"])

        # Sibling succeeds and is converted…
        assert good.ok and good.value == 1.8
        # …while the failing value is preserved unchanged with a note — no raise,
        # so the caller keeps the whole row and only flags the offending field.
        assert not bad.ok
        assert bad.value == "not-a-number"


# --------------------------------------------------------------------------- #
# Static config validation (V5, FR-005) — before any extraction runs
# --------------------------------------------------------------------------- #
class TestValidateConfig:
    def test_none_transform_valid(self):
        assert validate_transform_config("none", None) is None
        assert validate_transform_config(None, None) is None

    def test_controlled_vocab_requires_map_or_known_vocab(self):
        assert validate_transform_config("controlled_vocab", None) is not None
        assert validate_transform_config("controlled_vocab", {"vocab": "oeb"}) is None
        assert validate_transform_config("controlled_vocab", {"vocab": "nope"}) is not None
        assert validate_transform_config("controlled_vocab", {"map": {"高": "OEB5"}}) is None

    def test_pattern_config_validation(self):
        assert validate_transform_config("pattern", {"pattern": r"^\d+$"}) is not None
        assert validate_transform_config("pattern", {}) is not None
        assert validate_transform_config("pattern", {"pattern": "["}) is not None

    def test_cast_target_validation(self):
        assert validate_transform_config("cast", {"to": "integer"}) is None
        assert validate_transform_config("cast", {"to": "unicorn"}) is not None
        assert validate_transform_config("cast", {}) is not None

    def test_unknown_type_rejected(self):
        assert validate_transform_config("bogus", None) is not None
