"""Format-independent content nodes shared by browser, DOCX and signing envelopes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.template_v2 import Model


class OutputNode(Model):
    node_id: str
    kind: Literal[
        "document",
        "section",
        "group",
        "paragraph",
        "text",
        "value",
        "table",
        "row",
        "cell",
        "list",
        "form_field",
        "citation",
        "signature_region",
        "envelope",
        "signature",
    ]
    text: str = ""
    children: list[OutputNode] = Field(default_factory=list)
    record_id: str | None = None
    field_id: str | None = None
    state: str | None = None
    input_ref: dict | None = None
    fact_refs: list[str] = Field(default_factory=list)
    provenance_refs: list[dict] = Field(default_factory=list)
    claim_ref: str | None = None
    signature_ref: str | None = None
    signature_region_id: str | None = None
    body_ast_ref: str | None = None
    body_hash: str | None = None
    ordered: bool = False
    header: bool = False


def content_hash(node):
    return evidence_hash(node)


def walk(node):
    node = OutputNode.model_validate(node) if isinstance(node, dict) else node
    yield node
    for child in node.children:
        yield from walk(child)


def plain_text(node):
    node = OutputNode.model_validate(node) if isinstance(node, dict) else node
    if node.kind == "row":
        return " | ".join(plain_text(c) for c in node.children)
    separator = "" if node.kind in {"paragraph", "cell", "value"} else "\n"
    values = [node.text] if node.text else []
    values.extend(plain_text(child) for child in node.children)
    return separator.join(v for v in values if v)
