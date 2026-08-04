"""Unit tests for the shared deterministic ``{{占位符}}`` substitution utility."""

from __future__ import annotations

from app.services.reporting.placeholder_util import PLACEHOLDER_RE, substitute


def _missing(label: str) -> str:
    return f"【待补充：{label}】"


class TestSubstitute:
    def test_replaces_single_placeholder(self):
        assert (
            substitute("无法进行{{药物名称/代号}}临床批生产", {"药物名称/代号": "HRS-1597"}, missing=_missing)
            == "无法进行HRS-1597临床批生产"
        )

    def test_same_placeholder_repeated(self):
        out = substitute(
            "{{药物名称/代号}}与{{药物名称/代号}}", {"药物名称/代号": "HRS-1597"}, missing=_missing
        )
        assert out == "HRS-1597与HRS-1597"

    def test_multiple_distinct_placeholders(self):
        out = substitute(
            "{{药物名称/代号}}在{{车间}}生产{{剂型}}",
            {"药物名称/代号": "HRS-1597", "车间": "642车间", "剂型": "冻干粉针"},
            missing=_missing,
        )
        assert out == "HRS-1597在642车间生产冻干粉针"

    def test_key_whitespace_and_cjk_slash_trimmed(self):
        assert substitute("{{  药物名称/代号  }}", {"药物名称/代号": "X"}, missing=_missing) == "X"

    def test_missing_key_uses_sentinel(self):
        assert substitute("{{车间}}", {}, missing=_missing) == "【待补充：车间】"

    def test_none_value_uses_sentinel(self):
        assert substitute("{{车间}}", {"车间": None}, missing=_missing) == "【待补充：车间】"

    def test_empty_value_uses_sentinel(self):
        assert substitute("{{车间}}", {"车间": ""}, missing=_missing) == "【待补充：车间】"

    def test_substituted_value_is_not_re_expanded(self):
        # A resolved value that itself contains a placeholder must be emitted
        # literally — single pass, no recursion, no injection.
        out = substitute("{{药物名称/代号}}", {"药物名称/代号": "{{车间}}"}, missing=_missing)
        assert out == "{{车间}}"

    def test_text_without_placeholders_is_unchanged(self):
        text = "共线生产废弃物按环保要求分类处理"
        assert substitute(text, {"车间": "642车间"}, missing=_missing) == text

    def test_empty_text_returns_empty(self):
        assert substitute("", {"车间": "642车间"}, missing=_missing) == ""

    def test_mixed_resolved_and_missing(self):
        out = substitute(
            "{{药物名称/代号}}在{{车间}}",
            {"药物名称/代号": "HRS-1597"},
            missing=_missing,
        )
        assert out == "HRS-1597在【待补充：车间】"


class TestPlaceholderRe:
    def test_matches_trimmed_label(self):
        assert PLACEHOLDER_RE.findall("{{ 药物名称/代号 }} and {{车间}}") == [
            "药物名称/代号",
            "车间",
        ]
