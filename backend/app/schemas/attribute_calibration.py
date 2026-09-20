"""Source-backed attribute candidates awaiting semantic calibration, never facts."""

from typing import Any, Literal

from pydantic import Field

from app.schemas.attribute_value import ParsedAttributeValue
from app.schemas.evidence import EvidenceAnchor, EvidenceModel


class AttributeCalibrationCandidate(EvidenceModel):
    candidate_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    field_id: str | None = None
    field_label: str
    raw_value: str
    parsed_value: ParsedAttributeValue | None = None
    label_refs: list[EvidenceAnchor] = Field(default_factory=list)
    value_refs: list[EvidenceAnchor] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["pending", "rejected_mapping", "resolved"] = "pending"
    reason_codes: list[str] = Field(default_factory=list)
    checks: dict[str, str] = Field(default_factory=dict)
    source_claim_id: str | None = None
