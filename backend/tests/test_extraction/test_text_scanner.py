"""Structural scanners preserve text offsets without business matching rules."""

import pytest

from app.services.extraction.text_scanner import (
    chinese_heading_prefix,
    compact_space,
    first_ascii_number,
    key_value_spans,
    normalize_space,
    numbered_heading_depth,
    strip_tag_blocks,
    unicode_words,
)


@pytest.mark.parametrize("text, expected", [
    ("  1.2.3 产品", 3), ("2．4 标题", 2), ("说明", 0), ("3、范围", 1),
])
def test_numbered_headings_use_generic_numbering_grammar(text, expected):
    assert numbered_heading_depth(text) == expected


@pytest.mark.parametrize("text", ["（三）内容", "第十二章 内容", "一、范围"])
def test_chinese_heading_prefix(text):
    assert chinese_heading_prefix(text)
    assert not chinese_heading_prefix("药品信息")


def test_key_value_keeps_offsets_and_does_not_cross_paragraphs():
    text = "  规格 ： 250 mg "
    key, value = key_value_spans(text)
    assert text[slice(*key)] == "规格"
    assert text[slice(*value)] == "250 mg"
    assert key_value_spans("其他段落\n规格：250 mg") is None
    assert key_value_spans("没有字段") is None
    assert key_value_spans("字段：")[1] == (3, 3)


def test_unicode_token_offsets_include_non_bmp_without_losing_source():
    text = "药品 A-01 规格250mg，𠀀😀。"
    tokens = list(unicode_words(text))
    assert all(text[start:end] == token for token, start, end in tokens)
    assert "".join(token for token, _, _ in tokens) == compact_space(text)
    assert ("𠀀", text.index("𠀀"), text.index("𠀀") + 1) in tokens


def test_generic_space_and_style_scanning():
    assert normalize_space(" 标题\t 2\n") == "标题 2"
    assert first_ascii_number("Heading 12") == "12"
    assert first_ascii_number("标题") is None


def test_hidden_reasoning_is_removed_even_if_truncated():
    assert strip_tag_blocks('<think>{"fake":1}</think>{"real":2}', "think") == '{"real":2}'
    assert strip_tag_blocks('<think>{"fake":1}', "think") == ""
