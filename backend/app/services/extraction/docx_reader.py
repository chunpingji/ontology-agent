"""Document-local lookup caching for read-only Word parsing."""

from functools import cache
from pathlib import Path

from docx import Document
from docx.document import Document as WordDocument


def load_docx_for_reading(file_path: str | Path) -> WordDocument:
    """Load a fresh document whose style definitions remain unchanged while reading.

    python-docx resolves the default style by scanning all style definitions on
    every paragraph.style access. Enterprise files can carry thousands of styles;
    scanning them again for each paragraph dominates structure/preview parsing.
    Cache the library's own resolver on this document part only, preserving its
    missing-ID/type fallback behavior without sharing state between documents.
    Do not use this loader for authoring code that changes style definitions.
    """
    document = Document(str(file_path))
    document.part.get_style = cache(document.part.get_style)
    return document
