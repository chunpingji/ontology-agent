"""Entity alignment: match extracted candidates against existing KG entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.services.ontology_engine import IndividualInfo, OntologyEngine

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    action: str  # "new", "merge", "skip"
    match_iri: str | None = None
    match_score: float = 0.0
    match_label: str | None = None


def align_entity(
    candidate: dict[str, Any],
    target_class_iri: str,
    engine: OntologyEngine,
    id_property: str | None = None,
    label_property: str | None = None,
    threshold: float = 0.85,
) -> AlignmentResult:
    """Align a candidate entity against existing individuals in the KG.

    Strategy:
    1. Exact match on ID property (e.g., equipmentID)
    2. Fuzzy match on label/name
    3. If no match above threshold -> new entity
    """
    existing = engine.get_individuals(target_class_iri) or []

    # Step 1: Exact ID match
    if id_property:
        candidate_id = _get_candidate_value(candidate, id_property)
        if candidate_id:
            for ind in existing:
                existing_id = _get_individual_value(ind, id_property)
                if existing_id and str(existing_id) == str(candidate_id):
                    return AlignmentResult(
                        action="merge", match_iri=ind.iri,
                        match_score=1.0, match_label=ind.label_zh or ind.label_en,
                    )

    # Step 2: Fuzzy label match
    candidate_label = _get_candidate_label(candidate, label_property)
    if candidate_label:
        best_score = 0.0
        best_match: IndividualInfo | None = None
        for ind in existing:
            ind_label = ind.label_zh or ind.label_en or ind.name
            score = SequenceMatcher(None, candidate_label, ind_label).ratio()
            if score > best_score:
                best_score = score
                best_match = ind

        if best_match and best_score >= threshold:
            return AlignmentResult(
                action="merge", match_iri=best_match.iri,
                match_score=best_score,
                match_label=best_match.label_zh or best_match.label_en,
            )

    return AlignmentResult(action="new", match_score=0.0)


def _get_candidate_value(candidate: dict, prop_key: str) -> Any:
    for key, val in candidate.items():
        if prop_key in key:
            return val
    return None


def _get_candidate_label(candidate: dict, label_prop: str | None) -> str | None:
    if label_prop:
        val = _get_candidate_value(candidate, label_prop)
        if val:
            return str(val)
    for key, val in candidate.items():
        if "name" in key.lower() or "label" in key.lower():
            return str(val) if val else None
    return None


def _get_individual_value(ind: IndividualInfo, prop_key: str) -> Any:
    for key, val in ind.properties.items():
        if prop_key in key:
            return val
    return None
