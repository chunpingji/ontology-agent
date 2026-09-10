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
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from app.evaluation.quality_guided_variant import OntologyGuidedEvaluationResult
from app.evaluation.semantic_ranking_evaluation import summarize_costs
from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.document_ir import DocumentIR
from app.services.extraction.evidence_identity import canonical_json, stable_id

REFERENCE_SCHEMA_VERSION = "ontology-guided-reference-v1"
SCORER_VERSION = "ontology-guided-scorer-v2"


class EntityMatcher(EvidenceModel):
    class_iri: str = Field(min_length=1)
    label: str | None = None
    document_root: bool = False
    local_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    mention_evidence_sets: list[list["EvidenceSpan"]] = Field(default_factory=list)

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
    global_identity_expected: bool | None = None


class ReferenceProperty(EvidenceModel):
    subject: EntityMatcher
    predicate_iri: str = Field(min_length=1)
    value: Any
    polarity: Literal["affirmed", "negated", "conditional", "uncertain"] = "affirmed"
    direction: Literal["outbound"] = "outbound"
    conditions: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    expectation: Literal["expected", "forbidden", "undetermined"] = "expected"
    allowed_evidence_sets: list[list[EvidenceSpan]] = Field(default_factory=list)


class ReferenceRelationship(EvidenceModel):
    subject: EntityMatcher
    predicate_iri: str = Field(min_length=1)
    object: EntityMatcher
    polarity: Literal["affirmed", "negated", "conditional", "uncertain"] = "affirmed"
    direction: Literal["outbound", "inbound"] = "outbound"
    conditions: list[str] = Field(default_factory=list)
    applicability: dict[str, Any] = Field(default_factory=dict)
    expectation: Literal["expected", "forbidden", "undetermined"] = "expected"
    allowed_evidence_sets: list[list[EvidenceSpan]] = Field(default_factory=list)


class ReferencePath(EvidenceModel):
    path_id: str = Field(min_length=1)
    edges: list[ReferenceRelationship] = Field(min_length=1)

    @model_validator(mode="after")
    def is_rooted_and_contiguous(self):
        if not self.edges[0].subject.document_root:
            raise ValueError("reference path must start at the document root")
        if any(edge.polarity != "affirmed" or edge.expectation != "expected"
               or edge.direction != "outbound" for edge in self.edges):
            raise ValueError("recursive paths require expected affirmed outbound edges")
        if any(_entity_key(left.object) != _entity_key(right.subject)
               for left, right in zip(self.edges, self.edges[1:])):
            raise ValueError("reference path edges are not contiguous")
        return self


class ReferenceEntityPartition(EvidenceModel):
    local_id: str = Field(min_length=1)
    class_iri: str = Field(min_length=1)
    mention_spans: list[EvidenceSpan] = Field(min_length=1)


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
    paths: list[ReferencePath] = Field(default_factory=list)
    entity_partitions: list[ReferenceEntityPartition] = Field(default_factory=list)
    max_path_traversals: int = Field(default=10000, ge=1, le=1000000)
    annotation_complete: bool = False
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
    return ("entity", value.class_iri, value.local_id or _normal(value.label))


def _node_key(node, matchers=()) -> tuple:
    if node.root:
        return ("root", node.class_iri)
    candidates = {
        _entity_key(matcher) for matcher in matchers
        if not matcher.document_root and matcher.class_iri == node.class_iri
        and _normal(node.label) in {_normal(matcher.label), *map(_normal, matcher.aliases)}
    }
    matches = set()
    for key in candidates:
        restrictions = [option for matcher in matchers if _entity_key(matcher) == key
                        for option in matcher.mention_evidence_sets]
        if not restrictions or _evidence_matches(node.evidence_refs, restrictions):
            matches.add(key)
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        return ("ambiguous_entity", node.entity_id)
    if candidates:
        # A weaker repeated endpoint matcher cannot bypass explicit mention
        # restrictions, and a failed source match must never fall back to name.
        return ("unmatched_entity", node.entity_id)
    return ("entity", node.class_iri, _normal(node.label))


def _score(expected: set[tuple], predicted: set[tuple]) -> dict[str, Any]:
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
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
    return any(option and {_anchor_key(item) for item in option}.issubset(observed)
               for option in alternatives)


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
    matchers = [item.entity for item in reference.entities]
    matchers.extend(item.subject for item in reference.properties)
    for edge in [*reference.relationships, *(e for p in reference.paths for e in p.edges)]:
        matchers.extend((edge.subject, edge.object))

    def node_key(node):
        return _node_key(node, matchers)

    def qualifiers(item):
        return (item.direction, item.polarity,
                canonical_json(sorted(set(item.conditions))), canonical_json(item.applicability))

    def property_key(item):
        if item.subject_ref.id not in nodes:
            return ("dangling_property", item.candidate_id)
        if nodes[item.subject_ref.id].revision != item.subject_ref.revision:
            return ("stale_property_subject", item.candidate_id)
        value = item.normalized_value if item.normalized_value is not None else item.raw_value
        return ("property", node_key(nodes[item.subject_ref.id]), item.predicate_iri,
                canonical_json(value), *qualifiers(item))

    def reference_property_key(item):
        return ("property", _entity_key(item.subject), item.predicate_iri,
                canonical_json(item.value), *qualifiers(item))

    def relationship_key(item):
        if item.subject_ref.id not in nodes or item.object_ref.id not in nodes:
            return ("dangling_relationship", item.candidate_id)
        if (nodes[item.subject_ref.id].revision != item.subject_ref.revision
                or nodes[item.object_ref.id].revision != item.object_ref.revision):
            return ("stale_relationship_endpoint", item.candidate_id)
        return ("relationship", node_key(nodes[item.subject_ref.id]), item.predicate_iri,
                node_key(nodes[item.object_ref.id]), *qualifiers(item))

    def reference_relationship_key(item):
        return ("relationship", _entity_key(item.subject), item.predicate_iri,
                _entity_key(item.object), *qualifiers(item))

    def eligible(item):
        return (item.decision_status == "supported" and item.independent_review != "rejected"
                and getattr(item, "structural_valid", True)
                and getattr(item, "policy_eligible", True))

    candidates = {
        "entity": [node for node in nodes.values() if not node.root and eligible(node)],
        "property": [item for item in run.graph.properties if eligible(item)],
        "relationship": [item for item in run.graph.edges if eligible(item)],
    }
    references = {"entity": reference.entities, "property": reference.properties,
                  "relationship": reference.relationships}
    key_functions = {"entity": node_key, "property": property_key,
                     "relationship": relationship_key}
    ref_functions = {"entity": lambda item: _entity_key(item.entity),
                     "property": reference_property_key,
                     "relationship": reference_relationship_key}
    scored_predicates = set(reference.scored_predicate_iris)
    fields = ("evidence_refs", "subject_evidence_refs", "object_evidence_refs",
              "value_evidence_refs", "predicate_evidence_refs", "condition_evidence_refs")

    def anchors(item):
        return [anchor for field in fields for anchor in getattr(item, field, [])]

    replay_failures = []
    replay_cache = {}

    def replayable(item):
        valid = True
        for anchor in anchors(item):
            key = canonical_json(anchor)
            if key not in replay_cache:
                try:
                    ir.resolve(anchor)
                    replay_cache[key] = True
                except (KeyError, ValueError) as exc:
                    replay_cache[key] = False
                    replay_failures.append({"anchor": anchor.model_dump(mode="json"),
                                            "error": type(exc).__name__})
            valid = replay_cache[key] and valid
        return valid

    def valid_proof(item, expected):
        return bool(_evidence_matches(anchors(item), expected.allowed_evidence_sets)
                    and replayable(item))

    def counts(tp, fp, fn):
        return {"tp": tp, "fp": fp, "fn": fn,
                "precision": tp / (tp + fp) if tp + fp else None,
                "recall": tp / (tp + fn) if tp + fn else None,
                "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}

    metrics, semantic_metrics, unscored, duplicate_counts = {}, {}, {}, {}
    violations, proof_results, accepted_keys = {}, [], {}
    unscored_details = []
    for kind, predicted in candidates.items():
        expected = {ref_functions[kind](item): item for item in references[kind]
                    if item.expectation == "expected"}
        if len(expected) != sum(item.expectation == "expected" for item in references[kind]):
            raise ValueError("reference contains duplicate expected tuples")
        forbidden = {ref_functions[kind](item) for item in references[kind]
                     if item.expectation == "forbidden"}
        undecided = {ref_functions[kind](item) for item in references[kind]
                     if item.expectation == "undetermined"}
        accepted_keys[kind] = set()
        matched, semantic_matched = set(), set()
        tp = fp = unknown = semantic_fp = 0
        seen = Counter()
        violations[kind] = []
        # Prefer independently valid evidence when duplicate predictions share a tuple.
        predicted = sorted(predicted, key=lambda item: not (
            key_functions[kind](item) in expected
            and valid_proof(item, expected[key_functions[kind](item)])
        ))
        for item in predicted:
            key = key_functions[kind](item)
            seen[key] += 1
            reference_item = expected.get(key)
            outside = (kind != "entity" and scored_predicates
                       and item.predicate_iri not in scored_predicates)
            reason = None
            if key in forbidden:
                violations[kind].append(key)
            if outside:
                reason = "predicate_outside_annotation_scope"
            elif key in undecided:
                reason = "expert_undetermined"
            elif (reference_item is None and key not in forbidden
                  and not reference.annotation_complete):
                reason = "prediction_not_adjudicated"
            elif reference_item is not None and not reference_item.allowed_evidence_sets:
                reason = "reference_proof_not_configured"
            if reason is not None:
                unknown += 1
                unscored_details.append({"kind": kind, "key": key, "reason": reason})
                continue
            if reference_item is not None and key not in semantic_matched:
                semantic_matched.add(key)
            else:
                semantic_fp += 1
            proof_ok = bool(reference_item and valid_proof(item, reference_item))
            if reference_item is not None:
                proof_results.append(proof_ok)
            if proof_ok and key not in matched:
                tp += 1
                matched.add(key)
                accepted_keys[kind].add(key)
            else:
                fp += 1
        metrics[kind] = counts(tp, fp, len(expected) - len(matched))
        semantic_metrics[kind] = counts(len(semantic_matched), semantic_fp,
                                       len(expected) - len(semantic_matched))
        unscored[kind] = unknown
        duplicate_counts[kind] = sum(count - 1 for count in seen.values())

    metrics["overall"] = counts(*(sum(metrics[kind][name] for kind in candidates)
                                  for name in ("tp", "fp", "fn")))
    metrics["assertions"] = counts(*(sum(metrics[kind][name]
                                         for kind in ("property", "relationship"))
                                     for name in ("tp", "fp", "fn")))
    unknown_total = sum(unscored.values())
    accepted_total = sum(len(items) for items in candidates.values())
    overall = metrics["overall"]
    precision_bounds = {
        "lower": overall["tp"] / accepted_total if accepted_total else None,
        "upper": (overall["tp"] + unknown_total) / accepted_total if accepted_total else None,
    }

    # Enumerate only actual contiguous run paths of registered lengths. Every hop
    # must be independently correct; repeated entities cannot manufacture a path.
    reference_paths = {
        tuple(reference_relationship_key(edge) for edge in path.edges): path
        for path in reference.paths
    }
    predicted_paths = {}
    lengths = {len(path.edges) for path in reference.paths}
    root_ids = {node.entity_id for node in nodes.values() if node.root}
    eligible_edges = [edge for edge in candidates["relationship"]
                      if edge.direction == "outbound" and edge.polarity == "affirmed"]
    path_traversals = 0
    paths_truncated = False

    def walk(current, keys, visited, actual_edges):
        nonlocal path_traversals, paths_truncated
        path_traversals += 1
        if path_traversals > reference.max_path_traversals:
            paths_truncated = True
            return
        if len(keys) in lengths:
            predicted_paths.setdefault(tuple(keys), []).append(actual_edges)
        if not lengths or len(keys) >= max(lengths):
            return
        for edge in eligible_edges:
            if paths_truncated:
                return
            if edge.subject_ref.id == current and edge.object_ref.id not in visited:
                walk(edge.object_ref.id, [*keys, relationship_key(edge)],
                     visited | {edge.object_ref.id}, [*actual_edges, edge])

    for root_id in root_ids:
        walk(root_id, [], {root_id}, [])
    valid_paths = set()
    for keys, path in reference_paths.items():
        if any(all(valid_proof(edge, reference_edge)
                   for edge, reference_edge in zip(actual_edges, path.edges))
               for actual_edges in predicted_paths.get(keys, [])):
            valid_paths.add(keys)
    path_metrics = counts(len(valid_paths), len(set(predicted_paths) - valid_paths),
                          len(reference_paths) - len(valid_paths))
    path_metrics["status"] = ("truncated" if paths_truncated else
                              "scored" if reference.paths else "not_annotated")
    path_metrics["traversals"] = path_traversals

    identity_expectations = {_entity_key(item.entity): item.global_identity_expected
                             for item in reference.entities}
    identity_tp = identity_fp = identity_unknown = 0
    for node in candidates["entity"]:
        if node.identity_status != "verified":
            continue
        expected_identity = identity_expectations.get(node_key(node))
        if expected_identity is True:
            identity_tp += 1
        elif expected_identity is False:
            identity_fp += 1
        else:
            identity_unknown += 1

    partitions = reference.entity_partitions
    partition_nodes = {group.local_id: set() for group in partitions}
    partition_mentions = {}
    for group in partitions:
        for span in group.mention_spans:
            key = (group.class_iri, _anchor_key(span))
            if key in partition_mentions and partition_mentions[key] != group.local_id:
                raise ValueError("expert entity partitions overlap the same physical mention")
            partition_mentions[key] = group.local_id
    merges, unmapped_nodes = [], []
    for node in candidates["entity"]:
        groups = {partition_mentions[(node.class_iri, _anchor_key(anchor))]
                  for anchor in node.evidence_refs
                  if (node.class_iri, _anchor_key(anchor)) in partition_mentions}
        for group in groups:
            partition_nodes[group].add(node.entity_id)
        if len(groups) > 1:
            merges.append({"entity_id": node.entity_id, "expected_local_ids": sorted(groups)})
        elif not groups:
            unmapped_nodes.append(node.entity_id)
    splits = [{"local_id": group, "entity_ids": sorted(ids)}
              for group, ids in partition_nodes.items() if len(ids) > 1]
    partition_result = {
        "status": ("not_annotated" if not partitions else
                   "partially_unscored" if unmapped_nodes else "scored"),
        "wrong_merge_count": len(merges) if partitions else None,
        "wrong_split_count": len(splits) if partitions else None,
        "reference_groups": len(partitions), "predicted_nodes": len(candidates["entity"]),
        "unmapped_node_ids": unmapped_nodes if partitions else [],
        "merges": merges, "splits": splits,
    }

    thresholds = reference.quality_thresholds
    violation_count = sum(len(items) for items in violations.values())
    formal_gate = "not_configured"
    gate_reasons = []
    if paths_truncated:
        gate_reasons.append("path_enumeration_incomplete")
    if unknown_total:
        gate_reasons.append("unscored_predictions")
    if identity_unknown:
        gate_reasons.append("unscored_global_identity")
    if identity_fp:
        gate_reasons.append("incorrect_global_identity")
    if partitions and (merges or splits or unmapped_nodes):
        gate_reasons.append("entity_partition_errors_or_unscored_nodes")
    if any(not item.allowed_evidence_sets for items in references.values()
           for item in items if item.expectation == "expected"):
        gate_reasons.append("reference_proof_not_configured")
    if thresholds is not None:
        threshold_pass = all(
            value is not None and value >= minimum for value, minimum in (
                (overall["precision"], thresholds.minimum_overall_precision),
                (overall["recall"], thresholds.minimum_overall_recall),
                (overall["f1"], thresholds.minimum_overall_f1),
            )
        )
        threshold_pass = (threshold_pass and not gate_reasons and not replay_failures
                          and violation_count <= thresholds.maximum_forbidden_accepted
                          and (not thresholds.require_complete_coverage
                               or run.graph.progress.completion == "in_scope_complete"))
        formal_gate = "pass" if threshold_pass else "fail"
    return {
        "schema_version": "ontology-guided-score-v2", "scorer_version": SCORER_VERSION,
        "score_id": stable_id("ontology-guided-score", [run.run_fingerprint, reference,
                                                         SCORER_VERSION]),
        "run_fingerprint": run.run_fingerprint, "reference_id": reference.reference_id,
        "reference_review": reference.expert_review.model_dump(mode="json"),
        "metrics": metrics, "semantic_match_metrics": semantic_metrics,
        "costs": summarize_costs(run),
        "paths": path_metrics,
        "forbidden_assertions": {"reference_count": sum(
            item.expectation == "forbidden" for items in references.values() for item in items),
            "accepted_count": violation_count, "accepted": violations},
        "evidence": {"replay_total": len(replay_cache), "replay_failures": replay_failures,
                     "equivalence_checked": len(proof_results),
                     "equivalence_passed": sum(proof_results)},
        "coverage": run.graph.progress.model_dump(mode="json"),
        "unscored_predictions": {"entities": unscored["entity"],
                                 "properties": unscored["property"],
                                 "relationships": unscored["relationship"],
                                 "total": unknown_total,
                                 "fraction": (unknown_total / accepted_total
                                              if accepted_total else 0),
                                 "details": unscored_details},
        "precision_bounds": precision_bounds,
        "duplicate_eligible_predictions": {"entities": duplicate_counts["entity"],
                                           "properties": duplicate_counts["property"],
                                           "relationships": duplicate_counts["relationship"]},
        "identity_precision": (identity_tp / (identity_tp + identity_fp)
                               if identity_tp + identity_fp else None),
        "identity_counts": {"tp": identity_tp, "fp": identity_fp, "unscored": identity_unknown},
        "identity_metric_status": ("partially_unscored" if identity_unknown else
                                   "scored" if identity_tp + identity_fp else "no_global_claims"),
        "entity_partitions": partition_result,
        "quality_thresholds": thresholds.model_dump(mode="json") if thresholds else None,
        "formal_quality_gate": formal_gate, "gate_reasons": gate_reasons,
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
