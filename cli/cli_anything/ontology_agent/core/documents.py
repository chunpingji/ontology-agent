"""Document domain calls — mirrors the Report Center's document axis.

The page lists uploaded documents via ``listDocuments()`` and, for the
"生成风险评估报告" action, resolves a document IRI to its extraction ``job_id``
by reading the shadow entity's ``properties_json`` (``resolveDocumentJobId``).
"""

from __future__ import annotations

from typing import Any

from cli_anything.ontology_agent.core.client import HttpClient

# Candidate property keys holding the extraction job id — verbatim from the
# frontend ``DOCUMENT_JOB_KEYS`` (submitUpload writes ``job_id``; the rest are
# historical aliases). First non-empty string wins.
DOCUMENT_JOB_KEYS: tuple[str, ...] = (
    "job_id",
    "jobId",
    "source_job_id",
    "extraction_job_id",
    "hasJob",
    "sourceJob",
)


def list_documents(client: HttpClient, *, phase: str | None = None, page_size: int = 100) -> dict[str, Any]:
    """``GET /api/entities?module=document`` → ``{items, total, page, page_size}``."""
    params: dict[str, str] = {"module": "document", "page_size": str(page_size)}
    if phase:
        params["development_phase"] = phase
    return client.get("/api/entities", params=params)


def resolve_document_job_id(client: HttpClient, iri: str) -> str | None:
    """Resolve a document IRI to its extraction job id, or ``None`` if the
    document has no associated job (never went through the extraction pipeline,
    or the pointer field is absent)."""
    if not iri:
        return None
    result = list_documents(client)
    for shadow in result.get("items", []) or []:
        if shadow.get("iri") == iri:
            props = shadow.get("properties_json") or {}
            for key in DOCUMENT_JOB_KEYS:
                value = props.get(key)
                if isinstance(value, str) and value:
                    return value
            return None
    return None
