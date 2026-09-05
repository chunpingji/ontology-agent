"""Stateless document-analysis tools.

This router deliberately does not depend on ExtractionJob, the ontology engine,
or a database session.  Uploaded files exist only for the lifetime of one
request and the response contains display-only structure/summary metadata.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import settings
from app.services.extraction.doc_converter import (
    DocConversionError,
    ensure_docx_async,
)
from app.services.extraction.document_annotator import annotate_word
from app.services.extraction.docx_structure import parse_docx_structure
from app.services.extraction.word_tree_summarizer import (
    fallback_word_tree_summaries,
    summarize_word_tree,
)
from app.services.llm.local_client import get_local_llm

router = APIRouter()
logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {".doc", ".docx"}
_UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _save_temporary_upload(upload: UploadFile, destination: Path) -> None:
    """Stream an upload into the request-scoped temporary directory."""
    size = 0
    with destination.open("wb") as target:
        while chunk := await upload.read(_UPLOAD_CHUNK_SIZE):
            size += len(chunk)
            target.write(chunk)
    if size == 0:
        raise HTTPException(422, "上传的 Word 文件为空")


def _analyze_docx(docx_path: str, filename: str) -> dict:
    """Run the deterministic parser, structure-only preview, and summaries."""
    structure = parse_docx_structure(docx_path, source_filename=filename)
    content, preview_warnings, _triples, checkpoint = annotate_word(
        docx_path,
        engine=None,
        structure_only=True,
        rich_style=True,
        structure=structure,
    )
    if checkpoint is not None:  # Defensive: structure-only mode never checkpoints.
        logger.warning("Stateless Word analysis unexpectedly returned a checkpoint")

    try:
        summarize_word_tree(structure, get_local_llm())
    except Exception:
        logger.warning("Word 章节摘要失败，回退确定性摘录", exc_info=True)
        try:
            fallback_word_tree_summaries(structure)
        except Exception:
            logger.warning("Word 章节确定性摘录也失败", exc_info=True)

    section_tree = structure.section_tree
    if section_tree is None:  # parse_docx_structure currently always creates a root.
        raise ValueError("Word parser did not produce a document root")

    return {
        "filename": filename,
        "content": content,
        "warnings": list(dict.fromkeys([*structure.warnings, *preview_warnings])),
        "section_tree": section_tree.to_dict(),
        "pagination": asdict(structure.pagination),
        "parser_version": structure.parser_version,
        "summary_prompt_version": settings.word_tree_summary_prompt_version,
    }


@router.post("/word")
async def analyze_word_document(file: UploadFile = File(...)):
    """Analyze one Word upload without creating jobs, caches, or persistent files."""
    filename = Path(file.filename or "document.docx").name
    suffix = Path(filename).suffix.lower()
    try:
        if suffix not in _SUPPORTED_SUFFIXES:
            raise HTTPException(422, "仅支持 .doc / .docx 文件")
        with tempfile.TemporaryDirectory(prefix="word_analysis_") as temp_dir:
            source_path = Path(temp_dir) / f"upload{suffix}"
            await _save_temporary_upload(file, source_path)
            docx_path = await ensure_docx_async(str(source_path))
            return await asyncio.to_thread(_analyze_docx, docx_path, filename)
    except HTTPException:
        raise
    except DocConversionError as exc:
        raise HTTPException(422, f"文档转换失败：{exc}") from exc
    except Exception as exc:
        logger.warning("即时 Word 文档分析失败：%s", filename, exc_info=True)
        raise HTTPException(422, f"Word 文档解析失败：{exc}") from exc
    finally:
        await file.close()
