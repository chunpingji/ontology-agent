"""Reference-isolated scoring for ontology-guided evaluation run artifacts.

The scorer is deliberately outside the recognition package. Its reference
contract cannot be imported by the production executor or active evaluator.
Only explicitly approved expert references produce formal metrics; draft or
assistant-generated annotations fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from app.evaluation.quality_guided_variant import OntologyGuidedEvaluationResult
from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import canonical_json, stable_id

REFERENCE_SCHEMA_VERSION = "ontology-guided-reference-v1"
SCORER_VERSION = "ontology-guided-scorer-v1"


class EntityMatcher(EvidenceModel):
    class_iri: str = Field(min_length=1)
    label: str | None = None
    document_root: bool = False

    @model_validator(mode="after")
    def local_entity_needs_label(self):
        if not self.document_root and not self.label:
            raise ValueError("non-root entity matcher requires a label")
        return self


class EvidenceSpan(EvidenceModel):
    evidence_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def non_empty(self):
        if self.end <= self.start:
            raise ValueError("reference evidence span must be non-empty")
        return self


class ReferenceEntity(EvidenceModel):
    entity: EntityMatcher
    expectation: Literal["expected", "forbidden"] = "expected"
    allowed_evidence_sets: list[list[EvidenceSpan]] = Field(default_factory=list)


class ReferenceProperty(EvidenceModel):
    subject: EntityMatcher
    predicate_iri: str = Field(min_length=1)
    value: Any
    polarity: Literal["affirmed", "negated", "conditional", "uncertain"] = "affirmed"
    expectation: Literal["expected", "forbidden"] = "expected"
    allowed_evidence_sets: list[list[EvidenceSpan]] = Field(default_factory=list)


class ReferenceRelationship(EvidenceModel):
    subject: EntityMatcher
    predicate_iri: str = Field(min_length=1)
    object: EntityMatcher
    polarity: Literal["affirmed", "negated", "conditional", "uncertain"] = "affirmed"
    expectation: Literal["expected", "forbidden"] = "expected"
    allowed_evidence_sets: list[list[EvidenceSpan]] = Field(default_factory=list)


class ExpertReview(EvidenceModel):
    status: Literal["draft", "approved"] = "draft"
    reviewer: str | None = None
    reviewed_at: str | None = None
    reference_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def approved_has_audit_identity(self):
        if self.status == "approved" and (
            not self.reviewer or not self.reviewed_at or not self.reference_hash
        ):
            raise ValueError(
                "approved reference requires reviewer, reviewed_at and reference_hash"
            )
        return self


class QualityThresholds(EvidenceModel):
    source: str = Field(min_length=1)
    minimum_overall_precision: float = Field(ge=0, le=1)
    minimum_overall_recall: float = Field(ge=0, le=1)
    minimum_overall_f1: float = Field(ge=0, le=1)
    maximum_forbidden_accepted: int = Field(default=0, ge=0)
    require_complete_coverage: bool = True


class OntologyGuidedReference(EvidenceModel):
    schema_version: Literal["ontology-guided-reference-v1"] = REFERENCE_SCHEMA_VERSION
    reference_id: str = Field(min_length=1)
    document_hash: str = Field(min_length=64, max_length=64)
    ontology_hash: str = Field(min_length=64, max_length=64)
    root_class_iri: str = Field(min_length=1)
    scope_mode: Literal["document_graph", "focus_path"]
    focus_path: list[str] = Field(default_factory=list)
    scored_predicate_iris: list[str] = Field(default_factory=list)
    entities: list[ReferenceEntity] = Field(default_factory=list)
    properties: list[ReferenceProperty] = Field(default_factory=list)
    relationships: list[ReferenceRelationship] = Field(default_factory=list)
    expert_review: ExpertReview = Field(default_factory=ExpertReview)
    quality_thresholds: QualityThresholds | None = None

    @model_validator(mode="after")
    def scope_is_consistent(self):
        if self.scope_mode == "focus_path" and not self.focus_path:
            raise ValueError("focus_path reference requires at least one predicate")
        if self.scope_mode == "document_graph" and self.focus_path:
            raise ValueError("document_graph reference cannot carry a focus path")
        return self


def _normal(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _entity_key(value: EntityMatcher) -> tuple:
    if value.document_root:
        return ("root", value.class_iri)
    return ("entity", value.class_iri, _normal(value.label))


def _node_key(node) -> tuple:
    if node.root:
        return ("root", node.class_iri)
    return ("entity", node.class_iri, _normal(node.label))


def _score(expected: set[tuple], predicted: set[tuple]) -> dict[str, Any]:
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _anchor_key(value: EvidenceAnchor | EvidenceSpan) -> tuple[str, int, int]:
    return (
        value.evidence_id,
        value.span_start if isinstance(value, EvidenceAnchor) else value.start,
        value.span_end if isinstance(value, EvidenceAnchor) else value.end,
    )


def _evidence_matches(predicted, alternatives: list[list[EvidenceSpan]]) -> bool | None:
    if not alternatives:
        return None
    observed = {_anchor_key(item) for item in predicted}
    return any({_anchor_key(item) for item in option}.issubset(observed) for option in alternatives)


def score_evaluation(
    run: OntologyGuidedEvaluationResult,
    reference: OntologyGuidedReference,
    *,
    ir: DocumentIR,
) -> dict[str, Any]:
    """Score one immutable run without exposing the reference to recognition."""
    if reference.expert_review.status != "approved":
        raise ValueError("formal scoring requires an approved expert reference")
    if run.metadata_snapshot.document_hash != reference.document_hash:
        raise ValueError("reference document hash does not match evaluation run")
    if run.ontology_snapshot.ontology_hash != reference.ontology_hash:
        raise ValueError("reference ontology hash does not match evaluation run")
    if run.root_class_iri != reference.root_class_iri:
        raise ValueError("reference root class does not match evaluation run")
    if run.scope_mode != reference.scope_mode or run.focus_path != reference.focus_path:
        raise ValueError("reference scope does not match evaluation run")
    if ir.document_hash != reference.document_hash:
        raise ValueError("reference document hash does not match replay IR")

    nodes = {node.entity_id: node for node in run.graph.nodes}
    eligible_nodes = [
        node
        for node in run.graph.nodes
        if not node.root and node.decision_status == "supported"
        and node.independent_review != "rejected"
    ]
    eligible_properties = [
        item
        for item in run.graph.properties
        if item.structural_valid and item.policy_eligible
        and item.decision_status == "supported" and item.independent_review != "rejected"
    ]
    eligible_edges = [
        item
        for item in run.graph.edges
        if item.structural_valid and item.policy_eligible
        and item.decision_status == "supported" and item.independent_review != "rejected"
    ]
    scored_predicates = set(reference.scored_predicate_iris)

    def property_key(item) -> tuple:
        subject = nodes[item.subject_ref.id]
        value = item.normalized_value if item.normalized_value is not None else item.raw_value
        return (
            "property", _node_key(subject), item.predicate_iri,
            canonical_json(value), item.polarity,
        )

    def reference_property_key(item: ReferenceProperty) -> tuple:
        return (
            "property", _entity_key(item.subject), item.predicate_iri,
            canonical_json(item.value), item.polarity,
        )

    def relationship_key(item) -> tuple:
        return (
            "relationship", _node_key(nodes[item.subject_ref.id]), item.predicate_iri,
            _node_key(nodes[item.object_ref.id]), item.polarity,
        )

    def reference_relationship_key(item: ReferenceRelationship) -> tuple:
        return (
            "relationship", _entity_key(item.subject), item.predicate_iri,
            _entity_key(item.object), item.polarity,
        )

    predicted = {
        "entity": {_node_key(item) for item in eligible_nodes},
        "property": {
            property_key(item)
            for item in eligible_properties
            if not scored_predicates or item.predicate_iri in scored_predicates
        },
        "relationship": {
            relationship_key(item)
            for item in eligible_edges
            if not scored_predicates or item.predicate_iri in scored_predicates
        },
    }
    expected = {
        "entity": {
            _entity_key(item.entity)
            for item in reference.entities if item.expectation == "expected"
        },
        "property": {
            reference_property_key(item)
            for item in reference.properties if item.expectation == "expected"
        },
        "relationship": {
            reference_relationship_key(item)
            for item in reference.relationships if item.expectation == "expected"
        },
    }
    forbidden = {
        "entity": {
            _entity_key(item.entity)
            for item in reference.entities if item.expectation == "forbidden"
        },
        "property": {
            reference_property_key(item)
            for item in reference.properties if item.expectation == "forbidden"
        },
        "relationship": {
            reference_relationship_key(item)
            for item in reference.relationships if item.expectation == "forbidden"
        },
    }
    metrics = {kind: _score(expected[kind], predicted[kind]) for kind in expected}
    metrics["overall"] = _score(
        set().union(*expected.values()), set().union(*predicted.values())
    )

    replay_failures = []
    all_anchors = [
        anchor
        for item in [*run.graph.nodes, *run.graph.properties, *run.graph.edges]
        for field in (
            "evidence_refs", "subject_evidence_refs", "object_evidence_refs",
            "value_evidence_refs", "predicate_evidence_refs",
            "condition_evidence_refs", "counterevidence_refs",
        )
        for anchor in getattr(item, field, [])
    ]
    for anchor in all_anchors:
        try:
            ir.resolve(anchor)
        except (KeyError, ValueError) as exc:
            replay_failures.append(
                {"anchor": anchor.model_dump(mode="json"), "error": type(exc).__name__}
            )

    evidence_results = []
    for item in reference.entities:
        matches = [node for node in eligible_nodes if _node_key(node) == _entity_key(item.entity)]
        if matches:
            evidence_results.append(_evidence_matches(matches[0].evidence_refs,
                                                       item.allowed_evidence_sets))
    for item in reference.properties:
        matches = [value for value in eligible_properties
                   if property_key(value) == reference_property_key(item)]
        if matches:
            evidence_results.append(_evidence_matches(matches[0].evidence_refs,
                                                       item.allowed_evidence_sets))
    for item in reference.relationships:
        matches = [value for value in eligible_edges
                   if relationship_key(value) == reference_relationship_key(item)]
        if matches:
            evidence_results.append(_evidence_matches(matches[0].evidence_refs,
                                                       item.allowed_evidence_sets))
    checked_evidence = [value for value in evidence_results if value is not None]

    violations = {
        kind: sorted(predicted[kind] & forbidden[kind], key=repr) for kind in forbidden
    }
    violation_count = sum(len(value) for value in violations.values())
    thresholds = reference.quality_thresholds
    formal_gate = "not_configured"
    if thresholds is not None:
        overall = metrics["overall"]
        threshold_pass = all(
            value is not None and value >= minimum
            for value, minimum in (
                (overall["precision"], thresholds.minimum_overall_precision),
                (overall["recall"], thresholds.minimum_overall_recall),
                (overall["f1"], thresholds.minimum_overall_f1),
            )
        )
        threshold_pass = (
            threshold_pass
            and violation_count <= thresholds.maximum_forbidden_accepted
            and not replay_failures
            and (
                not thresholds.require_complete_coverage
                or run.graph.progress.completion == "in_scope_complete"
            )
        )
        formal_gate = "pass" if threshold_pass else "fail"
    return {
        "schema_version": "ontology-guided-score-v1",
        "scorer_version": SCORER_VERSION,
        "score_id": stable_id(
            "ontology-guided-score",
            [run.run_fingerprint, reference.reference_id, reference.expert_review],
        ),
        "run_fingerprint": run.run_fingerprint,
        "reference_id": reference.reference_id,
        "reference_review": reference.expert_review.model_dump(mode="json"),
        "metrics": metrics,
        "forbidden_assertions": {
            "reference_count": sum(len(value) for value in forbidden.values()),
            "accepted_count": violation_count,
            "accepted": violations,
        },
        "evidence": {
            "replay_total": len(all_anchors),
            "replay_failures": replay_failures,
            "equivalence_checked": len(checked_evidence),
            "equivalence_passed": sum(checked_evidence),
        },
        "coverage": run.graph.progress.model_dump(mode="json"),
        "unscored_predictions": {
            "properties": sum(item.predicate_iri not in scored_predicates
                              for item in eligible_properties) if scored_predicates else 0,
            "relationships": sum(item.predicate_iri not in scored_predicates
                                 for item in eligible_edges) if scored_predicates else 0,
        },
        "duplicate_eligible_predictions": {
            "entities": len(eligible_nodes) - len(predicted["entity"]),
            "properties": len(eligible_properties) - len({property_key(i)
                                                          for i in eligible_properties}),
            "relationships": len(eligible_edges) - len({relationship_key(i)
                                                        for i in eligible_edges}),
        },
        "identity_precision": None,
        "identity_metric_status": "not_represented_by_reference_v1",
        "quality_thresholds": (
            thresholds.model_dump(mode="json") if thresholds is not None else None
        ),
        "formal_quality_gate": formal_gate,
    }


def _read(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--ir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = score_evaluation(
        OntologyGuidedEvaluationResult.model_validate(_read(args.run), strict=True),
        OntologyGuidedReference.model_validate(_read(args.reference), strict=True),
        ir=DocumentIR.model_validate(_read(args.ir), strict=True),
    )
    result["reference_file_hash"] = hashlib.sha256(
        Path(args.reference).read_bytes()
    ).hexdigest()
    target = Path(args.output)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
