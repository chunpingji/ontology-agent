"""Bounded concept vocabulary from frozen labels, never document mentions."""

from __future__ import annotations

from collections import Counter

from app.services.extraction.ontology_guided.contracts import OntologySnapshot

LEXICAL_QUERY_VERSION = "subject-slot-query-v2"
LEXICAL_SELECTION_VERSION = "ontology-label-selection-v1"
MAX_CONCEPT_TERMS = 8
MAX_TERM_CHARACTERS = 80
MAX_QUERY_TERM_CHARACTERS = 640
_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def select_query_vocabulary(
    ontology: OntologySnapshot,
    *,
    subject_class_iri: str,
    predicate_iri: str,
    target_iris: list[str],
) -> dict:
    """Retain provenance outside model text and count omitted terms per concept."""
    context = ontology.lexical_context
    if context is None:
        raise ValueError("ontology lexical context is required")
    if subject_class_iri not in ontology.classes:
        raise ValueError("query subject class is outside the frozen ontology")
    concepts = [
        ("predicate", predicate_iri),
        ("subject_class", subject_class_iri),
        *[("object_type", iri) for iri in sorted(set(target_iris))],
    ]
    remaining = MAX_QUERY_TERM_CHARACTERS
    selected, omitted = [], []
    for role, iri in concepts:
        # Group identical text without erasing its languages or RDF predicates.
        terms: dict[str, list[dict]] = {}
        for item in context.annotations.get(iri, []):
            terms.setdefault(item.text.strip(), []).append({
                "text": item.text,
                "language": item.language,
                "predicate_iri": item.predicate_iri,
            })
        ordered = sorted(terms, key=lambda text: (
            not any(item["predicate_iri"] == _LABEL for item in terms[text]), text,
        ))
        reasons: Counter = Counter()
        count = 0
        for text in ordered:
            if not text:
                reasons["blank"] += 1
            elif len(text) > MAX_TERM_CHARACTERS:
                reasons["term_too_long"] += 1
            elif count >= MAX_CONCEPT_TERMS:
                reasons["concept_limit"] += 1
            elif len(text) > remaining:
                reasons["query_character_budget"] += 1
            else:
                selected.append({"role": role, "iri": iri, "text": text,
                                 "sources": terms[text]})
                count += 1
                remaining -= len(text)
        if reasons:
            omitted.append({"role": role, "iri": iri, "counts": dict(reasons)})
    return {
        "version": LEXICAL_SELECTION_VERSION,
        "context_hash": context.context_hash,
        "selected": selected,
        "omitted": omitted,
        "limits": {"terms_per_concept": MAX_CONCEPT_TERMS,
                   "term_characters": MAX_TERM_CHARACTERS,
                   "query_term_characters": MAX_QUERY_TERM_CHARACTERS},
    }


def terms_for(selection: dict, role: str, iri: str) -> list[str]:
    return [item["text"] for item in selection["selected"]
            if item["role"] == role and item["iri"] == iri]
