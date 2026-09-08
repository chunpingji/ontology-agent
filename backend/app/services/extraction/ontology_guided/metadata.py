"""Frozen structure/summary metadata used only for retrieval and display."""

from __future__ import annotations

from typing import Any

from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    METADATA_POLICY_VERSION,
    MetadataNode,
    MetadataSnapshot,
)
from app.services.extraction.ontology_guided.records import RecordIndex


def _flatten_tree(root: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], path: list[str]) -> None:
        heading = str(node.get("heading") or "")
        next_path = [*path, heading] if heading else path
        result.append({**node, "_path": next_path})
        for child in node.get("children", []) or []:
            visit(child, next_path)

    visit(root, [])
    return result


def prepare_metadata(
    ir: DocumentIR,
    *,
    section_tree: dict[str, Any],
    summary_version: str,
    summary_model_identity: str | None = None,
    generation_source: str | None = None,
) -> MetadataSnapshot:
    """Freeze a metadata snapshot after parsing/summarization has stopped mutating."""
    index = RecordIndex(ir)
    records_by_node: dict[str, list[str]] = {}
    for record in index.records:
        records_by_node.setdefault(record.section_node_id, []).append(record.record_id)
    nodes = []
    observed_sources: set[str] = set()
    for raw in _flatten_tree(section_tree):
        layer = raw.get("layer_metadata") or {}
        source = str(layer.get("summary_source") or "none")
        observed_sources.add(source)
        nodes.append(
            MetadataNode(
                node_id=str(raw["node_id"]),
                heading=str(raw.get("heading") or ""),
                path=list(raw.get("path") or raw.get("_path") or []),
                summary=layer.get("content_summary"),
                summary_status=str(layer.get("summary_status") or "pending"),
                summary_source=source,
                source_record_refs=records_by_node.get(str(raw["node_id"]), []),
            )
        )
    if generation_source is None:
        if "llm" in observed_sources:
            generation_source = "model_summary"
        elif "extractive_fallback" in observed_sources:
            generation_source = "extractive_fallback"
        else:
            generation_source = "structure_only"
    source_refs = [record.record_id for record in index.records]
    dependencies = {
        "analysis_id": ir.analysis_id,
        "document_hash": ir.document_hash,
        "structure_hash": ir.structure_hash,
        "summary_version": summary_version,
        "summary_model_identity": summary_model_identity,
        "generation_source": generation_source,
        "nodes": [item.model_dump(mode="json") for item in nodes],
        "source_record_refs": source_refs,
        "policy_version": METADATA_POLICY_VERSION,
    }
    dependency_hash = evidence_hash(dependencies)
    return MetadataSnapshot(
        snapshot_id=stable_id("metadata-snapshot", dependencies),
        analysis_id=ir.analysis_id,
        document_hash=ir.document_hash,
        structure_hash=ir.structure_hash,
        summary_version=summary_version,
        summary_model_identity=summary_model_identity,
        generation_source=generation_source,
        source_record_refs=source_refs,
        node_summaries=nodes,
        dependency_hash=dependency_hash,
    )
