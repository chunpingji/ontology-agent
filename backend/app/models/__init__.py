"""Import all ORM models so Base.metadata is fully populated.

Alembic autogenerate and `Base.metadata.create_all` both rely on every model
module being imported here.
"""

from app.models.document_analysis import (
    DocumentAnalysisArtifact,
    DocumentAnalysisControlOperation,
    DocumentAnalysisExecution,
    DocumentAnalysisRun,
    DocumentAnalysisTombstone,
    DocumentRecognitionEvent,
    DocumentRecognitionEventBatch,
    DocumentRunArtifact,
    DocumentRunArtifactHead,
    DocumentRunCandidate,
    DocumentRunCandidateHead,
    DocumentVerificationProof,
    DocumentVerificationProofHead,
)
from app.models.entity_shadow import EntityShadow
from app.models.evidence import *  # noqa: F401,F403
from app.models.extraction import *  # noqa: F401,F403
from app.models.integration import *  # noqa: F401,F403
from app.models.model_request import *  # noqa: F401,F403
from app.models.ontology_meta import (
    AppRole,
    AppUser,
    OntologyAction,
    OntologyChangeLog,
    OntologyClass,
    OntologyClassificationCriterion,
    OntologyClassMapping,
    OntologyConflictPolicy,
    OntologyDataProperty,
    OntologyDecisionRule,
    OntologyLinkType,
    OntologyPropertyBinding,
    OntologyRelease,
    OntologyRestriction,
)
from app.models.pde_conflict import PdeConflictDecision
from app.models.reasoning import (
    ActionExecution,
    AuditLog,
    ElectronicSignature,
    ReasoningExecution,
)
from app.models.reporting import *  # noqa: F401,F403

__all__ = [
    "EntityShadow",
    "DocumentAnalysisArtifact",
    "DocumentAnalysisControlOperation",
    "DocumentAnalysisExecution",
    "DocumentAnalysisRun",
    "DocumentAnalysisTombstone",
    "DocumentRecognitionEvent",
    "DocumentRecognitionEventBatch",
    "DocumentRunArtifact",
    "DocumentRunArtifactHead",
    "DocumentRunCandidate",
    "DocumentRunCandidateHead",
    "DocumentVerificationProof",
    "DocumentVerificationProofHead",
    "PdeConflictDecision",
    "AppRole",
    "AppUser",
    "OntologyClass",
    "OntologyLinkType",
    "OntologyDataProperty",
    "OntologyAction",
    "OntologyRestriction",
    "OntologyClassMapping",
    "OntologyPropertyBinding",
    "OntologyRelease",
    "OntologyChangeLog",
    "OntologyClassificationCriterion",
    "OntologyDecisionRule",
    "OntologyConflictPolicy",
    "AuditLog",
    "ReasoningExecution",
    "ActionExecution",
    "ElectronicSignature",
]
