"""Deterministic source-value observations, without factual acceptance."""

from pydantic import Field

from app.schemas.evidence import EvidenceModel


class ParsedAttributeValue(EvidenceModel):
    """Syntax observed in the source; never authorizes an owner or predicate."""

    value: str | bool | None = None
    datatype_iri: str | None = None
    quantity: dict | None = None
    precision: str | None = None
    issues: list[str] = Field(default_factory=list)
