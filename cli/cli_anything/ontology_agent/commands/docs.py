"""``oa docs`` — list uploaded documents (the Report Center document axis).

Mirrors ``listDocuments()`` and augments each row with the extraction ``job_id``
resolved exactly as ``resolveDocumentJobId`` does, so the id is ready to hand to
``report generate --job-id`` (or just pass the document IRI to ``report generate``).
"""

from __future__ import annotations

from typing import Any

import click

from cli_anything.ontology_agent.commands._ctx import build_client, handle, print_result
from cli_anything.ontology_agent.core import documents as documents_mod


def _project(shadow: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw ``EntityShadow`` to the fields that matter for the report flow."""
    props = shadow.get("properties_json") or {}
    job_id = None
    for key in documents_mod.DOCUMENT_JOB_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value:
            job_id = value
            break
    return {
        "iri": shadow.get("iri"),
        "label": shadow.get("label_zh") or shadow.get("label_en"),
        "class_iri": shadow.get("class_iri"),
        "job_id": job_id,
    }


@click.group()
def docs() -> None:
    """Uploaded documents."""


@docs.command("list")
@click.option("--phase", default=None, help="Filter by development_phase IRI")
@click.pass_context
def docs_list(ctx: click.Context, phase: str | None) -> None:
    """List uploaded documents, each with its resolved extraction job id."""
    result = handle(ctx, documents_mod.list_documents, build_client(ctx), phase=phase)
    items = [_project(s) for s in (result.get("items") or [])]
    print_result(ctx, items)
