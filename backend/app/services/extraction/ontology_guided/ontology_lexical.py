"""Frozen ontology lexical annotations, with no generated or domain-specific terms."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash

RDFS_LABEL_IRI = "http://www.w3.org/2000/01/rdf-schema#label"
SKOS_ALT_LABEL_IRI = "http://www.w3.org/2004/02/skos/core#altLabel"
LEXICAL_CONTEXT_VERSION = "v1"


class OntologyLexicalTerm(EvidenceModel):
    text: str = Field(min_length=1)
    language: str | None = None
    predicate_iri: Literal[
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://www.w3.org/2004/02/skos/core#altLabel",
    ]

    @field_validator("text", "language")
    @classmethod
    def nonblank_value(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("ontology lexical text and language must not be blank")
        return value


def _term_key(term: OntologyLexicalTerm) -> tuple[str, str, str]:
    return term.text, term.language or "", term.predicate_iri


class OntologyLexicalContext(EvidenceModel):
    version: Literal["v1"] = LEXICAL_CONTEXT_VERSION
    # Empty lists explicitly record that a requested IRI had no lexical labels.
    annotations: dict[Annotated[str, Field(min_length=1)], list[OntologyLexicalTerm]]
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_frozen_content(self):
        for terms in self.annotations.values():
            keys = [_term_key(term) for term in terms]
            if keys != sorted(set(keys)):
                raise ValueError("ontology lexical terms must be sorted and deduplicated")
        expected = evidence_hash({"version": self.version, "annotations": self.annotations})
        if self.context_hash != expected:
            raise ValueError("ontology lexical context hash does not match its content")
        return self


def build_lexical_context(
    annotations: dict[str, list[dict[str, Any] | OntologyLexicalTerm]],
) -> OntologyLexicalContext:
    """Canonicalize a complete engine read while retaining every source predicate.

    Equal text from different predicates/languages remains separately traceable;
    a retrieval query can deduplicate text without losing the frozen provenance.
    """
    canonical: dict[str, list[OntologyLexicalTerm]] = {}
    for iri, values in sorted(annotations.items()):
        terms = [OntologyLexicalTerm.model_validate(value, strict=True) for value in values]
        unique = {_term_key(term): term for term in terms}
        canonical[iri] = [unique[key] for key in sorted(unique)]
    return OntologyLexicalContext(
        annotations=canonical,
        context_hash=evidence_hash({
            "version": LEXICAL_CONTEXT_VERSION, "annotations": canonical,
        }),
    )
