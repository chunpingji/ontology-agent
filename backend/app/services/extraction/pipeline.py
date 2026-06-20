"""Extraction pipeline: parse -> extract -> align -> review queue."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.extraction import ExtractionCandidate, ExtractionConfig, ExtractionJob
from app.services.extraction.aligner import align_entity
from app.services.extraction.parser import parse_excel, parse_word
from app.services.ontology_engine import OntologyEngine

logger = logging.getLogger(__name__)


async def run_extraction_pipeline(
    job: ExtractionJob,
    config: ExtractionConfig,
    file_path: Path,
    engine: OntologyEngine,
    db: Session,
) -> ExtractionJob:
    """Run the full extraction pipeline for a job."""
    try:
        job.status = "parsing"
        db.commit()

        # Stage 1: Parse
        if config.source_type == "excel":
            raw_data = parse_excel(
                file_path,
                column_mapping=config.column_mapping or {},
            )
        elif config.source_type == "word":
            raw_data = parse_word(file_path)
        else:
            raise ValueError(f"Unsupported source type: {config.source_type}")

        job.status = "extracting"
        db.commit()

        # Stage 2: Extract (for Excel with direct column mapping, raw_data is already structured)
        candidates_data = raw_data

        job.status = "aligning"
        db.commit()

        # Stage 3: Align
        id_prop = _find_id_property(config.column_mapping)
        label_prop = _find_label_property(config.column_mapping)

        for entity_data in candidates_data:
            alignment = align_entity(
                candidate=entity_data,
                target_class_iri=config.target_class_iri,
                engine=engine,
                id_property=id_prop,
                label_property=label_prop,
            )

            candidate = ExtractionCandidate(
                job_id=job.id,
                target_class_iri=config.target_class_iri,
                extracted_properties=entity_data,
                alignment_result=alignment.action,
                aligned_iri=alignment.match_iri,
                match_score=alignment.match_score,
                review_status="pending",
            )
            db.add(candidate)

        job.total_candidates = len(candidates_data)
        job.status = "reviewing"
        db.commit()

    except Exception as e:
        logger.exception("Extraction pipeline failed")
        job.status = "failed"
        job.error_message = str(e)
        db.commit()

    return job


def _find_id_property(column_mapping: dict | None) -> str | None:
    if not column_mapping:
        return None
    for col, prop in column_mapping.items():
        if "id" in col.lower() or "id" in prop.lower() or "编号" in col:
            return prop
    return None


def _find_label_property(column_mapping: dict | None) -> str | None:
    if not column_mapping:
        return None
    for col, prop in column_mapping.items():
        if "name" in col.lower() or "name" in prop.lower() or "名称" in col:
            return prop
    return None
