"""Generic structural scanners. Offsets are half-open Unicode codepoint spans.

These routines recognize formatting grammars, never domain entities or relations.
They intentionally do not normalize the underlying evidence text.
"""

from collections.abc import Iterator

_ZH_DIGITS = frozenset("一二三四五六七八九十百千万零〇两")


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def compact_space(text: str) -> str:
    return "".join(text.split())


def first_ascii_number(text: str) -> str | None:
    for start, char in enumerate(text):
        if char in "0123456789":
            end = start + 1
            while end < len(text) and text[end] in "0123456789":
                end += 1
            return text[start:end]
    return None


def numbered_heading_depth(text: str) -> int:
    value = text.lstrip()
    pos, depth = 0, 0
    while pos < len(value):
        start = pos
        while pos < len(value) and value[pos] in "0123456789":
            pos += 1
        if pos == start:
            break
        depth += 1
        if pos == len(value) or value[pos] not in ".．":
            break
        pos += 1
    return min(depth, 6)


def chinese_heading_prefix(text: str) -> bool:
    value = text.lstrip()
    if not value:
        return False
    chapter = value.startswith("第")
    pos = 1 if chapter or value[0] in "（(" else 0
    start = pos
    while pos < len(value) and (
        value[pos] in _ZH_DIGITS or (chapter and value[pos] in "0123456789")
    ):
        pos += 1
    endings = "章节部分" if chapter else "）)、.．"
    return pos > start and pos < len(value) and value[pos] in endings


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def key_value_spans(text: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if "\n" in text or "\r" in text:
        return None
    for pos, char in enumerate(text):
        if char not in ":：":
            continue
        key = _trim_span(text, 0, pos)
        if not 0 < key[1] - key[0] <= 80:
            return None
        return key, _trim_span(text, pos + 1, len(text))
    return None


def unicode_words(text: str) -> Iterator[tuple[str, int, int]]:
    """Preserve ASCII identifiers; emit every other non-space codepoint singly."""
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        start = pos
        if text[pos].isascii() and text[pos].isalnum():
            pos += 1
            while pos < len(text):
                char = text[pos]
                if char.isascii() and char.isalnum():
                    pos += 1
                elif char in "._-/%" and pos + 1 < len(text) and (
                    text[pos + 1].isascii() and text[pos + 1].isalnum()
                ):
                    pos += 1
                else:
                    break
        else:
            pos += 1
        yield text[start:pos], start, pos


def strip_tag_blocks(text: str, tag: str) -> str:
    """Remove complete or truncated hidden blocks, including nested blocks."""
    opening, closing = f"<{tag}>", f"</{tag}>"
    parts: list[str] = []
    pos, depth = 0, 0
    while pos < len(text):
        if text.startswith(opening, pos):
            depth += 1
            pos += len(opening)
        elif text.startswith(closing, pos):
            depth = max(0, depth - 1)
            pos += len(closing)
        else:
            if depth == 0:
                parts.append(text[pos])
            pos += 1
    return "".join(parts)
