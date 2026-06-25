"""Import all ORM models so Base.metadata is fully populated.

Alembic autogenerate and `Base.metadata.create_all` both rely on every model
module being imported here.
"""

from app.models.entity_shadow import EntityShadow
from app.models.extraction import *  # noqa: F401,F403
from app.models.integration import *  # noqa: F401,F403
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
    OntologyRelease,
    OntologyRestriction,
)
from app.models.reasoning import (
    ActionExecution,
    AuditLog,
    ElectronicSignature,
    ReasoningExecution,
)

__all__ = [
    "EntityShadow",
    "AppRole",
    "AppUser",
    "OntologyClass",
    "OntologyLinkType",
    "OntologyDataProperty",
    "OntologyAction",
    "OntologyRestriction",
    "OntologyClassMapping",
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
