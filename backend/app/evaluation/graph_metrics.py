"""Source-scoped graph evaluation, independent of model and validation verdicts.

``evaluate_graph(run, reference, ir=ir)`` accepts JSON dictionaries. Reference v1
contains ``annotation_level`` (``assistant_silver`` or ``human_gold``), optional
``document_hash``, positive ``entities``, ``properties`` and ``relationships``,
explicit ``forbidden`` assertions, and ``scopes``. Every reference has an ``id``;
entity references specify ``class_iri`` and exact ``aliases``. Assertion endpoints
are entity reference IDs. Evidence is a list of ``{evidence_id, start?, end?}``;
its entries are alternative locations, not semantic facts inferred from a summary.
Scopes specify ``evidence_ids`` and ``exhaustive`` lists named ``entity_classes``,
``property_predicates`` and ``relationship_predicates``. Only those combinations
are closed-world annotations. Other unmatched outputs remain unscored.

Matching is one-to-one, including duplicate predictions. Correct source replay
does not imply semantic correctness. No reference means no accuracy estimate;
assistant-created silver never becomes human-verified full-document accuracy.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

KINDS = ("entity", "property", "relationship")
REFERENCE_KEYS = {"entity": "entities", "property": "properties", "relationship": "relationships"}
SCOPE_KEYS = {
    "entity": "entity_classes", "property": "property_predicates",
    "relationship": "relationship_predicates",
}
ASSERTIONS = {"affirmed", "negated", "conditional", "hypothetical", "uncertain"}


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _anchors(candidate: dict) -> list[dict]:
    return [anchor for source in candidate.get("provenance", [])
            if source.get("kind") == "document" for anchor in source.get("anchors", [])]


def _interval(item: dict, *, reference: bool = False) -> tuple[int | None, int | None]:
    if reference:
        return item.get("start"), item.get("end")
    return item.get("span_start"), item.get("span_end")


def _evidence_matches(candidate: dict, expected: dict) -> bool:
    wanted = expected.get("evidence", [])
    if not wanted:
        return True
    for source in _anchors(candidate):
        for target in wanted:
            if source.get("evidence_id") != target.get("evidence_id"):
                continue
            left, right = _interval(source)
            start, end = _interval(target, reference=True)
            if left is None or start is None or max(left, start) < min(right, end):
                return True
    return False


def _entity_matches(candidate: dict, expected: dict, *, polarity: bool = True) -> bool:
    aliases = expected.get("aliases", [])
    if expected.get("text"):
        aliases = [*aliases, expected["text"]]
    identity_matches = _text(candidate.get("text", "")) in {_text(alias) for alias in aliases}
    if expected.get("is_document_root"):
        identity_matches = bool(expected.get("_document_hash")) and candidate.get(
            "identity", {}
        ).get("document_root") == expected["_document_hash"]
    elif expected.get("identity_mode") == "source_record":
        identity_matches = bool(expected.get("evidence"))
    return (
        candidate.get("kind") == "entity"
        and candidate.get("class_iri") == expected.get("class_iri")
        and identity_matches
        and (not polarity or candidate.get("assertion_status", "affirmed")
             == expected.get("assertion_status", "affirmed"))
        and _evidence_matches(candidate, expected)
    )


def _decimal_equal(left: Any, right: Any) -> bool:
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and a == b
    except (InvalidOperation, ValueError):
        return False


def _literal_matches(candidate: dict, expected: dict) -> bool:
    """Compare supplied semantic fields; never erase units, ranges or polarity."""
    if not candidate or not expected:
        return False
    kind = expected.get("kind", candidate.get("kind", "text"))
    if "kind" in expected and candidate.get("kind") != kind:
        return False
    numeric = kind in {"number", "comparison", "range"}
    for key in ("canonical_unit", "raw_unit", "dimension", "datatype_iri"):
        if key in expected and candidate.get(key) != expected[key]:
            return False
    if numeric and candidate.get("operator", "eq") != expected.get("operator", "eq"):
        return False
    if kind == "range":
        return all(_decimal_equal(candidate.get(key), expected.get(key))
                   for key in ("lower", "upper")) and all(
            candidate.get(key, True) == expected.get(key, True)
            for key in ("lower_inclusive", "upper_inclusive")
        )
    if "normalized_value" in expected and expected["normalized_value"] is not None:
        left, right = candidate.get("normalized_value"), expected["normalized_value"]
        if numeric:
            return _decimal_equal(left, right)
        if kind == "boolean":
            return type(left) is bool and type(right) is bool and left == right
        return left is not None and _text(left) == _text(right)
    return "raw_value" in expected and _text(candidate.get("raw_value", "")) == _text(
        expected["raw_value"]
    )


def _endpoint_map(candidates: list[dict], entities: list[dict]) -> dict[tuple, str]:
    result: dict[tuple, str] = {}
    counts = Counter((item.get("candidate_id"), item.get("revision", 1)) for item in candidates)
    for candidate in candidates:
        if candidate.get("kind") != "entity":
            continue
        key = (candidate.get("candidate_id"), candidate.get("revision", 1))
        matches = [item["id"] for item in entities if _entity_matches(candidate, item)]
        # Repeated labels in different records are not automatically the same entity.
        if len(matches) == 1 and counts[key] == 1:
            result[key] = matches[0]
    return result


def _endpoint(reference: Any, identities: dict) -> str | None:
    if not isinstance(reference, dict):
        return None
    return identities.get((reference.get("candidate_id"), reference.get("revision", 1)))


def _matches(candidate: dict, expected: dict, kind: str, identities: dict) -> bool:
    if kind == "entity":
        return _entity_matches(candidate, expected)
    if (candidate.get("kind") != kind
            or candidate.get("predicate_iri") != expected.get("predicate_iri")
            or candidate.get("assertion_status", "affirmed")
            != expected.get("assertion_status", "affirmed")
            or _endpoint(candidate.get("subject"), identities) != expected.get("subject")
            or not _evidence_matches(candidate, expected)):
        return False
    if kind == "relationship":
        return _endpoint(candidate.get("object"), identities) == expected.get("object")
    return _literal_matches(candidate.get("literal", {}), expected.get("literal", {}))


def _maximum_matching(edges: list[list[int]]) -> dict[int, int]:
    """Small deterministic bipartite matching avoids order-dependent greedy scores."""
    owner: dict[int, int] = {}

    def assign(prediction: int, seen: set[int]) -> bool:
        for reference in edges[prediction]:
            if reference in seen:
                continue
            seen.add(reference)
            if reference not in owner or assign(owner[reference], seen):
                owner[reference] = prediction
                return True
        return False

    for prediction in range(len(edges)):
        assign(prediction, set())
    return {prediction: reference for reference, prediction in owner.items()}


def _in_scope(candidate: dict, scopes: list[dict], kind: str) -> bool:
    key = candidate.get("class_iri" if kind == "entity" else "predicate_iri")
    ids = {anchor.get("evidence_id") for anchor in _anchors(candidate)}
    return any(
        key in scope.get("exhaustive", {}).get(SCOPE_KEYS[kind], [])
        and ids.intersection(scope.get("evidence_ids", []))
        for scope in scopes
    )


def _rates(tp: int, fp: int, fn: int) -> dict:
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def _binding_diagnostics(candidates: list[dict], identities: dict) -> dict:
    available = Counter((item.get("candidate_id"), item.get("revision", 1))
                        for item in candidates if item.get("kind") == "entity")
    unresolved = []
    for item in candidates:
        roles = ("subject", "object") if item.get("kind") == "relationship" else ("subject",)
        if item.get("kind") not in {"property", "relationship"}:
            continue
        failures = []
        for role in roles:
            endpoint = item.get(role) or {}
            key = (endpoint.get("candidate_id"), endpoint.get("revision", 1))
            if key in identities:
                continue
            reason = (
                "endpoint_missing_from_evaluated_subset" if not available[key] else
                "duplicate_candidate_identity_revision" if available[key] > 1 else
                "endpoint_not_uniquely_matched_to_reference"
            )
            failures.append({"role": role, "candidate_id": key[0], "revision": key[1],
                             "reason": reason})
        if failures:
            unresolved.append({"candidate_id": item.get("candidate_id"), "kind": item["kind"],
                               "endpoints": failures})
    return {
        "unresolved_assertion_count": len(unresolved), "unresolved_assertions": unresolved,
        "interpretation": "Unresolved bindings cannot match references. They are false positives "
                          "only inside exhaustive scopes; elsewhere they remain unscored. "
                          "Unmatched positive references still count as false negatives.",
    }


def _score(candidates: list[dict], reference: dict, *, exclude_metadata_entities=False) -> dict:
    entities = [{**item, "_document_hash": reference.get("document_hash")}
                for item in reference.get("entities", [])]
    identities = _endpoint_map(candidates, entities)
    result = {"binding_resolution": _binding_diagnostics(candidates, identities)}
    total = Counter()
    for kind in KINDS:
        predictions = [item for item in candidates if item.get("kind") == kind]
        expected = entities if kind == "entity" else reference.get(REFERENCE_KEYS[kind], [])
        if exclude_metadata_entities and kind == "entity":
            predictions = [item for item in predictions if not (
                reference.get("document_hash") and item.get("identity", {}).get("document_root")
                == reference["document_hash"]
            )]
            expected = [item for item in expected if not item.get("is_document_root")]
        forbidden = [item for item in reference.get("forbidden", []) if item["kind"] == kind]
        edges = [[j for j, target in enumerate(expected)
                  if _matches(prediction, target, kind, identities)]
                 for prediction in predictions]
        matched = _maximum_matching(edges)
        negative_hits = {
            item["id"]: [prediction.get("candidate_id") for prediction in predictions
                         if _matches(prediction, item, kind, identities)]
            for item in forbidden
        }
        fp, unscored = [], []
        for i, prediction in enumerate(predictions):
            if i in matched:
                continue
            reason = None
            if edges[i]:
                reason = "duplicate_reference_assertion"
            elif any(_matches(prediction, item, kind, identities) for item in forbidden):
                reason = "explicit_forbidden_assertion"
            elif _in_scope(prediction, reference.get("scopes", []), kind):
                reason = "unmatched_in_exhaustive_scope"
            if reason:
                fp.append({"candidate_id": prediction.get("candidate_id"), "reason": reason})
            else:
                unscored.append(prediction.get("candidate_id"))
        counts = _rates(len(matched), len(fp), len(expected) - len(matched))
        total.update({key: counts[key] for key in ("tp", "fp", "fn")})
        total.update(predictions=len(predictions), unscored=len(unscored))
        result[kind] = {
            **counts, "predictions": len(predictions), "reference_count": len(expected),
            "matched": [{"candidate_id": predictions[i].get("candidate_id"),
                         "reference_id": expected[j]["id"]} for i, j in sorted(matched.items())],
            "false_positives": fp,
            "missed_reference_ids": [item["id"] for j, item in enumerate(expected)
                                     if j not in matched.values()],
            "unscored_count": len(unscored), "unscored_candidate_ids": unscored,
            "negative_cases": {"count": len(forbidden),
                               "violated_count": sum(bool(items)
                                                     for items in negative_hits.values()),
                               "violations": {key: items for key, items in negative_hits.items()
                                              if items}},
        }
    result["micro"] = {
        **_rates(total["tp"], total["fp"], total["fn"]),
        "predictions": total["predictions"], "unscored_count": total["unscored"],
        "scored_prediction_fraction": (
            (total["tp"] + total["fp"]) / total["predictions"]
            if total["predictions"] else None
        ),
    }
    return result


def _scores_with_extracted_primary(candidates: list[dict], reference: dict) -> dict:
    result = _score(candidates, reference)
    result["extracted_only"] = _score(candidates, reference, exclude_metadata_entities=True)
    return result


def _resolve(anchor: dict, ir: dict, units: dict) -> str:
    unit = units.get(anchor.get("evidence_id"))
    if unit is None:
        raise ValueError("unknown evidence_id")
    for key in ("document_hash", "structure_hash", "parser_version"):
        if key not in ir or anchor.get(key) != ir[key]:
            raise ValueError(f"source identity mismatch: {key}")
    for key in ("section_node_id", "block_id", "paragraph_index", "fragment_index",
                "table_path", "row_index", "column_index", "physical_page_number"):
        expected = unit.get(key, 0 if key == "fragment_index" else None)
        if anchor.get(key) != expected:
            raise ValueError(f"source coordinate mismatch: {key}")
    start, end = _interval(anchor)
    text = unit.get("text", "")
    if start is None and end is None:
        return text
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
        raise ValueError("invalid source interval")
    return text[start:end]


def provenance_replay(candidates: list[dict], ir: dict | None) -> dict:
    """Audit anchors against immutable IR coordinates, independently of matching."""
    if ir is None:
        return {"status": "unavailable", "reason": "source IR not supplied", "replay_rate": None}
    units = {unit["evidence_id"]: unit for unit in ir.get("evidence_units", [])}
    valid = invalid = excerpts_total = excerpts_valid = no_source = 0
    failures = []
    for candidate in candidates:
        sources = [source for source in candidate.get("provenance", [])
                   if source.get("kind") == "document"]
        if not any(source.get("anchors") for source in sources):
            no_source += 1
        groups = [(source.get("anchors", []), source.get("excerpts", [])) for source in sources]
        groups.extend((binding.get("anchors", []), []) for binding in candidate.get("bindings", []))
        groups.append((candidate.get("condition_anchors", []), []))
        for anchors, excerpts in groups:
            resolved = []
            for anchor in anchors:
                try:
                    resolved.append(_resolve(anchor, ir, units))
                    valid += 1
                except ValueError as error:
                    invalid += 1
                    failures.append({"candidate_id": candidate.get("candidate_id"),
                                     "evidence_id": anchor.get("evidence_id"),
                                     "reason": str(error)})
            excerpts_total += len(excerpts)
            excerpts_valid += sum(excerpt in resolved for excerpt in excerpts)
    return {
        "status": "evaluated", "anchor_count": valid + invalid,
        "replayed_anchor_count": valid, "invalid_anchor_count": invalid,
        "replay_rate": valid / (valid + invalid) if valid + invalid else None,
        "excerpt_count": excerpts_total, "replayed_excerpt_count": excerpts_valid,
        "excerpt_replay_rate": excerpts_valid / excerpts_total if excerpts_total else None,
        "candidates_without_document_anchors": no_source, "failures": failures,
        "interpretation": "Source-coordinate integrity only; not semantic accuracy.",
    }


def _validate_reference(reference: dict, ir: dict | None) -> None:
    if reference.get("schema_version") != 1:
        raise ValueError("reference requires schema_version=1")
    if reference.get("annotation_level") not in {"assistant_silver", "human_gold"}:
        raise ValueError("reference must declare assistant_silver or human_gold annotation_level")
    if (ir and reference.get("document_hash")
            and reference["document_hash"] != ir.get("document_hash")):
        raise ValueError("reference document_hash does not match source IR")
    entities = reference.get("entities", [])
    entity_ids = {item["id"] for item in entities}
    all_refs = [(kind, item) for kind in KINDS for item in reference.get(REFERENCE_KEYS[kind], [])]
    all_refs.extend((item.get("kind"), item) for item in reference.get("forbidden", []))
    ids = [item.get("id") for _, item in all_refs]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("reference IDs must be nonempty and globally unique")
    units = {unit["evidence_id"]: unit for unit in ir.get("evidence_units", [])} if ir else None
    for kind, item in all_refs:
        if kind not in KINDS:
            raise ValueError("unknown reference assertion kind")
        if item.get("assertion_status", "affirmed") not in ASSERTIONS:
            raise ValueError("unknown assertion polarity")
        if kind == "entity":
            if not item.get("class_iri") or not (item.get("aliases") or item.get("text")):
                raise ValueError("entity reference requires class_iri and exact aliases")
            if item.get("identity_mode", "exact_alias") not in {"exact_alias", "source_record"}:
                raise ValueError("unknown entity identity_mode")
            if item.get("identity_mode") == "source_record" and not item.get("evidence"):
                raise ValueError("source_record identity requires explicit identity evidence")
            if item.get("is_document_root") and not reference.get("document_hash"):
                raise ValueError("document root identity requires reference document_hash")
        else:
            if item.get("subject") not in entity_ids or not item.get("predicate_iri"):
                raise ValueError("assertion requires existing entity subject and predicate_iri")
            if kind == "relationship" and item.get("object") not in entity_ids:
                raise ValueError("relationship requires existing entity object")
            if kind == "property" and not item.get("literal"):
                raise ValueError("property requires literal")
        for evidence in item.get("evidence", []):
            if not evidence.get("evidence_id"):
                raise ValueError("reference evidence requires evidence_id")
            start, end = _interval(evidence, reference=True)
            if (start is None) != (end is None) or (start is not None and not (
                type(start) is int and type(end) is int and 0 <= start < end
            )):
                raise ValueError("invalid reference source interval")
            if units is not None:
                unit = units.get(evidence["evidence_id"])
                if unit is None or (end is not None and end > len(unit["text"])):
                    raise ValueError("reference evidence is outside supplied source IR")
    for scope in reference.get("scopes", []):
        if not scope.get("evidence_ids"):
            raise ValueError("exhaustive scope requires explicit evidence_ids")
        if units is not None and any(item not in units for item in scope["evidence_ids"]):
            raise ValueError("scope references unknown source evidence")


def evaluate_graph(run: dict, reference: dict | None, *, ir: dict | None = None) -> dict:
    """Score raw and passed candidates without treating validation as ground truth."""
    candidates = run.get("candidates", [])
    source_ir = ir if ir is not None else run.get("ir")
    validated = [item for item in candidates if item.get("validation_status") == "passed"]
    result = {
        "metric_version": "source-scoped-graph-v1",
        "raw_candidate_count": len(candidates), "validated_candidate_count": len(validated),
        "validation_status_counts": dict(Counter(item.get("validation_status", "pending")
                                                  for item in candidates)),
        "run_completion": run.get("completion", "unknown"),
        "provenance_replay": {"raw": provenance_replay(candidates, source_ir),
                              "validated": provenance_replay(validated, source_ir)},
    }
    if reference is None:
        return {**result, "annotation_level": "none", "semantic_metrics_available": False,
                "raw": None, "validated": None,
                "interpretation": "No independent reference: semantic accuracy is not measured."}
    _validate_reference(reference, source_ir)
    return {
        **result, "annotation_level": reference["annotation_level"],
        "semantic_metrics_available": True,
        "reference_document_hash": reference.get("document_hash"),
        "reference_counts": {kind: len(reference.get(key, []))
                             for kind, key in REFERENCE_KEYS.items()},
        "supplied_metadata_entity_count": sum(bool(item.get("is_document_root"))
                                              for item in reference.get("entities", [])),
        "source_record_entity_count": sum(item.get("identity_mode") == "source_record"
                                          for item in reference.get("entities", [])),
        "scope_count": len(reference.get("scopes", [])),
        "raw": _scores_with_extracted_primary(candidates, reference),
        "validated": _scores_with_extracted_primary(validated, reference),
        "primary_metric": "raw.extracted_only and validated.extracted_only",
        "metadata_exclusion": "Primary extracted_only metrics exclude externally supplied document "
                              "root entity predictions/references, while retaining roots for "
                              "property and relationship endpoint matching.",
        "interpretation": (
            "Agreement with source-annotated assistant silver on explicit scopes only; "
            "not human-verified accuracy or full-document accuracy."
            if reference["annotation_level"] == "assistant_silver" else
            "Agreement with supplied human gold on explicit scopes only; "
            "not full-document accuracy unless the reference exhaustively covers the document."
        ),
    }
