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


class RetrievalDiagnosticCarrier(EvidenceModel):
    retrieval_diagnostics: RetrievalDiagnostics | None = None
    # Search scope is distinct from admitted recognition work in the new policy.
    # Omit this field for immutable legacy payloads and their fingerprints.
    candidate_policy: Literal["sparse-candidates-v1"] | None = None

    @model_serializer(mode="wrap")
    def legacy_shape(self, handler):
        result = handler(self)
        if self.retrieval_diagnostics is None:
            result.pop("retrieval_diagnostics", None)
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
    candidate_policy = getattr(value, "candidate_policy", None)
    return {
        **({"retrieval_diagnostics": diagnostic.model_dump(mode="json")} if diagnostic else {}),
        **({"candidate_policy": candidate_policy} if candidate_policy else {}),
    }
