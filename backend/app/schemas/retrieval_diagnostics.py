"""Optional search diagnostics; counts refine coverage and never add to it."""

from typing import Literal

from pydantic import Field, model_serializer, model_validator

from app.schemas.evidence import EvidenceModel


class RetrievalDiagnostics(EvidenceModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["adaptive-retrieval-v1"] = "adaptive-retrieval-v1"
    records_soft_pruned: int = Field(default=0, ge=0)
    records_pending_disposition: int = Field(default=0, ge=0)
    records_reactivatable: int = Field(default=0, ge=0)
    records_dependency_exhausted: int = Field(default=0, ge=0)
    reason_counts: dict[str, int] = Field(default_factory=dict)
    search_status: str = "searching"
    pruning_quality: Literal["unvalidated"] | None = None

    @model_serializer(mode="wrap")
    def preserve_previous_shape(self, handler):
        result = handler(self)
        if self.pruning_quality is None:
            result.pop("pruning_quality", None)
        return result


class RecordDiscoveryDiagnostics(EvidenceModel):
    policy: Literal["semantic-record-discovery-v1"] = "semantic-record-discovery-v1"
    mode: Literal["semantic", "deterministic"]
    reading_groups: int = Field(ge=0)
    ranked_groups: int = Field(ge=0)
    remaining_pairs: int = Field(ge=0)
    admitted_pairs: int = Field(ge=0)
    unselected_pairs: int = Field(ge=0)
    unselected_groups: int = Field(ge=0)


class RetrievalDiagnosticCarrier(EvidenceModel):
    retrieval_diagnostics: RetrievalDiagnostics | None = None
    record_discovery: RecordDiscoveryDiagnostics | None = None
    # Search scope is distinct from admitted recognition work in the new policy.
    # Omit this field for immutable legacy payloads and their fingerprints.
    candidate_policy: Literal["sparse-candidates-v1"] | None = None

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        result = handler(self)
        if self.retrieval_diagnostics is None:
            result.pop("retrieval_diagnostics", None)
        if self.record_discovery is None:
            result.pop("record_discovery", None)
        if self.candidate_policy is None:
            result.pop("candidate_policy", None)
        if result.get("completion") is None:
            result.pop("completion", None)
        return result

    @model_validator(mode="after")
    def pruned_subset(self):
        unattempted = getattr(self, "records_unattempted", getattr(self, "unattempted", 0))
        if self.candidate_policy is None and self.retrieval_diagnostics and (
            self.retrieval_diagnostics.records_soft_pruned > unattempted
        ):
            raise ValueError("soft-pruned records must be a subset of unattempted coverage")
        return self


def diagnostic_payload(value):
    diagnostic = getattr(value, "retrieval_diagnostics", None)
    discovery = getattr(value, "record_discovery", None)
    candidate_policy = getattr(value, "candidate_policy", None)
    return {
        **({"retrieval_diagnostics": diagnostic.model_dump(mode="json")} if diagnostic else {}),
        **({"record_discovery": discovery.model_dump(mode="json")} if discovery else {}),
        **({"candidate_policy": candidate_policy} if candidate_policy else {}),
    }
