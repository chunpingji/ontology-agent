"""Ontology-guided document recognition domain.

The package deliberately contains no API, ORM, or evaluation imports.  It is
shared by the online document-analysis application and offline evaluators.
"""

from app.services.extraction.ontology_guided.contracts import (
    CONTRACT_VERSION,
    GraphSnapshot,
    LocalMenu,
    MetadataSnapshot,
    RunProgress,
    SemanticDecision,
    VerificationTarget,
)

__all__ = [
    "CONTRACT_VERSION",
    "GraphSnapshot",
    "LocalMenu",
    "MetadataSnapshot",
    "RunProgress",
    "SemanticDecision",
    "VerificationTarget",
]
