"""Compatibility facade for the generic, evidence-grounded extraction runner."""

from __future__ import annotations

from pathlib import Path

from app.services.extraction.docx_structure import DocStructure


def extract_relationships(
    engine,
    file_path: str | Path,
    triples: list[dict],
    doc_class: dict | None = None,
    source_filename: str | None = None,
    structure: DocStructure | None = None,
) -> dict:
    """Compatibility entrypoint backed exclusively by the generic evidence runner."""
    from app.services.extraction.document_ir import build_document_ir
    from app.services.extraction.evidence_preview import preview_relationships
    from app.services.extraction.local_semantic_model import configured_generic_runner
    from app.services.extraction.word_analysis import analyze_word_core

    ir = (
        build_document_ir(file_path, structure)
        if structure is not None
        else analyze_word_core(file_path, source_filename=source_filename).ir
    )
    runner = configured_generic_runner(engine)
    run = runner.run(ir, effective_class=(doc_class or {}).get("doc_class_iri", ""))
    return {
        "doc_class": doc_class,
        "relationships": preview_relationships(run.candidates, runner.schema),
        "evidence_run": run.model_dump(mode="json"),
        "completion": run.completion,
        "diagnostics": run.diagnostics,
        "degraded": run.degraded,
    }
