"""Source-bound subject queries; retrieval text never establishes a fact."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    GraphNode,
    SlotSpec,
    SubjectRef,
    VersionedRef,
)
from app.services.extraction.ontology_guided.records import RecordIndex


class QueryMention(EvidenceModel):
    text: str
    source_refs: list[EvidenceAnchor] = Field(default_factory=list)
    role: str = "subject_mention"
    trust_state: Literal["supported", "hypothesis", "quarantined"] = "quarantined"
    identity_scope: str = "document_local"


class SubjectSlotQuery(EvidenceModel):
    query_id: str
    query_version: str = "subject-slot-query-v1"
    run_fingerprint: str
    subject_ref: SubjectRef
    predicate_iri: str
    retrieval_intent: Literal["discover", "counterevidence"]
    model_text: str
    query_dependency_hash: str
    trusted_context: list[QueryMention] = Field(default_factory=list)
    excluded_sources: list[QueryMention] = Field(default_factory=list)
    dependency_refs: list[VersionedRef] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    authority: Literal["retrieval_only"] = "retrieval_only"


def build_subject_queries(
    *,
    subject: SubjectRef,
    subject_node: GraphNode,
    predicate: SlotSpec | EdgeSpec,
    index: RecordIndex,
    run_fingerprint: str,
    root_ref: VersionedRef,
    root_class_iri: str,
    mentions: list[QueryMention] | None = None,
    dependency_refs: list[VersionedRef] | None = None,
) -> list[SubjectSlotQuery]:
    if (subject.entity_id, subject.revision, subject.class_iri) != (
        subject_node.entity_id,
        subject_node.revision,
        subject_node.class_iri,
    ):
        raise ValueError("query subject does not match the current node")
    trusted: list[QueryMention] = []
    excluded: list[QueryMention] = []
    # Root filenames are audit labels, never inferred product mentions.
    candidates = list(mentions or [])
    if not subject.is_document_root:
        candidates.extend(
            QueryMention(
                text=index.ir.resolve(anchor),
                source_refs=[anchor],
                trust_state=(
                    "supported"
                    if subject_node.decision_status == "supported"
                    and subject_node.independent_review != "rejected"
                    else "quarantined"
                ),
            )
            for anchor in subject_node.evidence_refs
        )
    for item in candidates:
        replayed = [index.ir.resolve(anchor) for anchor in item.source_refs]
        if (
            item.trust_state == "supported"
            and replayed
            and item.text
            and any(item.text in value for value in replayed)
            and item.role in {"subject_mention", "verified_local_name", "verified_local_code"}
        ):
            if item not in trusted:
                trusted.append(item)
        else:
            excluded.append(item)
    dependencies = dependency_refs or []
    common = {
        "subject_class": subject.class_iri,
        "subject_class_label": subject_node.class_label,
        "document_root": subject.is_document_root,
        "subject_mentions": [item.text for item in trusted],
        "predicate": {
            "iri": predicate.iri,
            "label": predicate.label,
            "definition": predicate.description,
        },
        "allowed_object_types": (
            [
                {"iri": item.iri, "label": item.label, "definition": item.description}
                for item in predicate.range_classes
            ]
            or [{"iri": iri} for iri in predicate.range_class_iris]
            if isinstance(predicate, EdgeSpec)
            else []
        ),
        "literal_spec": predicate.model_dump(mode="json")
        if isinstance(predicate, SlotSpec)
        else None,
        "document_class": root_class_iri,
        "contrast_roles": ["产品", "API原料", "批次", "试验来源", "工艺步骤", "中间体"],
    }
    dependency_hash = evidence_hash(
        {
            "subject": subject,
            "predicate": predicate,
            "trusted": trusted,
            "dependencies": dependencies,
            "root": root_ref,
            "source": index.ir.document_hash,
        }
    )
    result = []
    for intent, instruction in (
        ("discover", "寻找讨论当前主体与直接谓词的原文；肯定、否定和条件均可能相关。"),
        ("counterevidence", "寻找明确否定、限制或改变当前主体谓词适用对象的原文。"),
    ):
        model_text = json.dumps(
            {**common, "intent": instruction},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result.append(
            SubjectSlotQuery(
                query_id=stable_id("slot-query", [run_fingerprint, dependency_hash, intent]),
                run_fingerprint=run_fingerprint,
                subject_ref=subject,
                predicate_iri=predicate.iri,
                retrieval_intent=intent,
                model_text=model_text,
                query_dependency_hash=dependency_hash,
                trusted_context=trusted,
                excluded_sources=excluded,
                dependency_refs=dependencies,
                diagnostics=[] if predicate.description else ["predicate_definition_missing"],
            )
        )
    return result
