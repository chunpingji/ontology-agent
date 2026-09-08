"""Compatibility import for the shared ontology-guided citation protocol.

Active recognition code owns this implementation under
``app.services.extraction.ontology_guided``. The evaluation import is retained
so historical tests and isolated experiment commands keep byte-identical wire
semantics without becoming a production dependency.
"""

from app.services.extraction.ontology_guided.citations import (
    PROTOCOL_VERSION,
    CitationProtocol,
)

__all__ = ["CitationProtocol", "PROTOCOL_VERSION"]
