"""One exact, immutable published-fact selector for coverage and report consumers.

Snapshot membership is the commit receipt (candidate workflow payloads in a
manifest intentionally predate publication). No annotation cache is consulted.
"""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas.evidence import Candidate, EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash

SELECTOR_VERSION = "instance-selector-v1"


class PredicateStep(EvidenceModel):
    predicate_iri: str = Field(min_length=1)
    direction: Literal["forward", "inverse"] = "forward"


def predicate_steps(path):
    return [
        step
        if isinstance(step, PredicateStep)
        else PredicateStep.model_validate(
            {"predicate_iri": step} if isinstance(step, str) else step
        )
        for step in path
    ]


class FactSelector:
    def __init__(self, snapshot: dict, *, schema: dict | None = None):
        if not snapshot or not snapshot.get("snapshot_id"):
            raise ValueError("a published snapshot is required")
        self.snapshot_id = snapshot["snapshot_id"]
        self.schema = deepcopy(schema or {})
        self.records = deepcopy(snapshot.get("assertions", []))
        self.candidates = {}
        self.entities = {}
        self._by_subject = defaultdict(list)
        self._by_object = defaultdict(list)
        for record in self.records:
            candidate = Candidate.model_validate(record["candidate"])
            if candidate.validation_status != "passed" or candidate.review_status != "confirmed":
                raise ValueError("published assertions must be validated and confirmed")
            self.candidates[record["assertion_id"]] = candidate
            self._by_subject[record["subject_iri"]].append(record)
            if candidate.kind == "entity" and candidate.positive_eligible:
                self.entities[record["subject_iri"]] = record
            if record.get("object_iri"):
                self._by_object[record["object_iri"]].append(record)

    @classmethod
    def load(cls, db, job_id, snapshot_id=None, *, schema=None):
        from app.services.fact_commit import FactCommitService

        return cls(
            FactCommitService(db, None).published_snapshot(job_id, snapshot_id), schema=schema
        )

    def class_matches(self, actual, expected):
        if not expected or actual == expected:
            return True
        pending, seen = [actual], set()
        while pending:
            current = pending.pop()
            if current == expected:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(self.schema.get(current, {}).get("parents", []))
        return False

    def instances(self, class_iri):
        return sorted(
            iri
            for iri, record in self.entities.items()
            if self.class_matches(record["candidate"]["class_iri"], class_iri)
        )

    def _applicable(self, record, applicable_at):
        value = self.candidates[record["assertion_id"]].applicable_at
        # A time-bound assertion cannot establish an unbounded fact.
        return value is None or value == applicable_at

    def _positive(self, record, applicable_at=None):
        return self.candidates[record["assertion_id"]].positive_eligible and self._applicable(
            record, applicable_at
        )

    def _key(self, record):
        candidate = self.candidates[record["assertion_id"]]
        literal = None
        if candidate.literal:
            literal = candidate.literal.model_dump(
                mode="json",
                exclude={
                    "raw_value",
                    "raw_unit",
                    "normalizer_version",
                    "conversion_record",
                },
            )
            if candidate.literal.kind in {"number", "range", "comparison"}:
                for key in ("normalized_value", "lower", "upper"):
                    if literal[key] is not None:
                        sign, digits, exponent = Decimal(literal[key]).as_tuple()
                        digits = list(digits)
                        while digits and digits[-1] == 0:
                            digits.pop()
                            exponent += 1
                        literal[key] = [sign, digits, exponent] if digits else [0, [0], 0]
            if not candidate.literal.canonical_unit:
                literal["unresolved_unit"] = candidate.literal.raw_unit
        return evidence_hash(
            [
                record["subject_iri"],
                candidate.predicate_iri,
                record.get("object_iri"),
                literal,
            ]
        )

    def conflicts(self, applicable_at=None):
        """Preserve, but do not project, contradictory or over-cardinality facts."""
        positive, negative, properties = defaultdict(list), defaultdict(list), defaultdict(list)
        for record in self.records:
            candidate = self.candidates[record["assertion_id"]]
            if not self._applicable(record, applicable_at):
                continue
            if candidate.assertion_status == "negated" and not (
                candidate.condition_anchors or candidate.condition_provenance_indexes
            ):
                negative[self._key(record)].append(record["assertion_id"])
            elif self._positive(record, applicable_at):
                positive[self._key(record)].append(record["assertion_id"])
                if candidate.kind in {"property", "relationship"}:
                    properties[(record["subject_iri"], candidate.predicate_iri)].append(record)
        conflicted = set()
        for key in positive.keys() & negative.keys():
            conflicted.update(positive[key] + negative[key])
        for (subject, predicate), values in properties.items():
            entity = self.entities.get(subject)
            if entity is None:
                conflicted.update(r["assertion_id"] for r in values)
                continue
            definition = self.schema.get(entity["candidate"]["class_iri"], {})
            menu = [*definition.get("properties", []), *definition.get("relationships", [])]
            maximum = next((p.get("max_count") for p in menu if p["iri"] == predicate), None)
            if maximum is not None and len({self._key(r) for r in values}) > maximum:
                conflicted.update(r["assertion_id"] for r in values)
        return conflicted

    def select(
        self,
        subject_iri,
        path,
        *,
        range_class_iri=None,
        object_iris=None,
        required_properties=(),
        applicable_at=None,
    ):
        steps = predicate_steps(path)
        if not steps:
            raise ValueError("selection requires a complete predicate path")
        if len(steps) > 16:
            raise ValueError("selection path exceeds traversal budget")
        conflicts = self.conflicts(applicable_at)
        relevant_conflicts, negative_ids, negative_objects = set(), set(), set()
        frontier = {subject_iri: set()} if subject_iri in self.entities else {}
        requested = set(object_iris) if object_iris is not None else None
        for index, step in enumerate(steps):
            following = defaultdict(set)
            final = index == len(steps) - 1
            for node, supporting in frontier.items():
                records = (self._by_subject if step.direction == "forward" else self._by_object)[
                    node
                ]
                for record in records:
                    candidate = self.candidates[record["assertion_id"]]
                    if (
                        candidate.kind != "relationship"
                        or candidate.predicate_iri != step.predicate_iri
                    ):
                        continue
                    if not self._applicable(record, applicable_at):
                        continue
                    target = (
                        record["object_iri"]
                        if step.direction == "forward"
                        else record["subject_iri"]
                    )
                    if target not in self.entities:
                        continue
                    if final and (
                        not self.class_matches(
                            self.entities[target]["candidate"]["class_iri"], range_class_iri
                        )
                        or (requested is not None and target not in requested)
                    ):
                        continue
                    identity = record["assertion_id"]
                    if identity in conflicts:
                        relevant_conflicts.add(identity)
                        continue
                    if self._positive(record, applicable_at):
                        following[target].update(supporting | {identity})
                    elif (
                        final
                        and requested
                        and candidate.assertion_status == "negated"
                        and not candidate.condition_anchors
                        and not candidate.condition_provenance_indexes
                    ):
                        negative_objects.add(target)
                        negative_ids.update(supporting | {identity})
            frontier = following
        objects, qualified, fact_ids = [], [], set()
        for target, supporting in sorted(frontier.items()):
            values = defaultdict(list)
            for record in self._by_subject[target]:
                candidate = self.candidates[record["assertion_id"]]
                if candidate.kind != "property" or not self._applicable(record, applicable_at):
                    continue
                if record["assertion_id"] in conflicts:
                    if candidate.predicate_iri in required_properties:
                        relevant_conflicts.add(record["assertion_id"])
                    continue
                if self._positive(record, applicable_at):
                    values[candidate.predicate_iri].append(
                        {
                            "assertion_id": record["assertion_id"],
                            "literal": candidate.literal.model_dump(mode="json"),
                            "provenance": record["candidate"]["provenance"],
                        }
                    )
            missing = [prop for prop in required_properties if not values[prop]]
            ids = supporting | {v["assertion_id"] for group in values.values() for v in group}
            ids.add(self.entities[target]["assertion_id"])
            objects.append(
                {
                    "instance_iri": target,
                    "text": self.entities[target]["candidate"]["text"],
                    "class_iri": self.entities[target]["candidate"]["class_iri"],
                    "properties": dict(values),
                    "missing_properties": missing,
                    "assertion_ids": sorted(ids),
                }
            )
            fact_ids.update(ids)
            if not missing:
                qualified.append(target)
        if relevant_conflicts:
            qualified = []
        return {
            "snapshot_id": self.snapshot_id,
            "selector_version": SELECTOR_VERSION,
            "subject_instance_iri": subject_iri,
            "predicate_path": [step.model_dump() for step in steps],
            "objects": objects,
            "qualified_object_iris": qualified,
            "assertion_ids": sorted(fact_ids),
            "negative_assertion_ids": sorted(negative_ids),
            "conflict_assertion_ids": sorted(relevant_conflicts),
            "confirmed_absent": bool(requested)
            and requested <= negative_objects
            and not relevant_conflicts
            and not objects,
        }

    def edges(self, *, assertion_ids=None):
        """Compatibility presentation of selected facts; never enrich or collapse values.

        Every edge and each property retain instance identity and assertion IDs.
        Ambiguous scalar labels are omitted rather than last-writer overwritten.
        """
        allowed = set(assertion_ids) if assertion_ids is not None else None
        conflicts = self.conflicts()
        output = []
        for record in self.records:
            candidate = self.candidates[record["assertion_id"]]
            if (
                candidate.kind != "relationship"
                or not self._positive(record)
                or record["assertion_id"] in conflicts
                or (allowed is not None and record["assertion_id"] not in allowed)
            ):
                continue
            target = self.entities.get(record["object_iri"])
            subject = self.entities.get(record["subject_iri"])
            if not target or not subject:
                continue
            properties = []
            labels = defaultdict(list)
            definition = self.schema.get(target["candidate"]["class_iri"], {})
            for prop in self._by_subject[record["object_iri"]]:
                value = self.candidates[prop["assertion_id"]]
                if (
                    value.kind != "property"
                    or not self._positive(prop)
                    or prop["assertion_id"] in conflicts
                    or (allowed is not None and prop["assertion_id"] not in allowed)
                ):
                    continue
                label = next(
                    (
                        p.get("label") or p["iri"]
                        for p in definition.get("properties", [])
                        if p["iri"] == value.predicate_iri
                    ),
                    value.predicate_iri,
                )
                properties.append(
                    {
                        "property_iri": value.predicate_iri,
                        "property_label": label,
                        "value": value.literal.raw_value,
                        "literal": value.literal.model_dump(mode="json"),
                        "assertion_id": prop["assertion_id"],
                        "provenance": prop["candidate"]["provenance"],
                    }
                )
                labels[label].append(value.literal.raw_value)
            output.append(
                {
                    "subject_iri": record["subject_iri"],
                    "subject_text": subject["candidate"]["text"],
                    "subject_class_iri": subject["candidate"]["class_iri"],
                    "predicate_iri": candidate.predicate_iri,
                    "predicate_label": candidate.predicate_iri,
                    "object_iri": record["object_iri"],
                    "object_text": target["candidate"]["text"],
                    "object_class_iri": target["candidate"]["class_iri"],
                    "object_properties": {
                        key: vals[0] for key, vals in labels.items() if len(set(vals)) == 1
                    },
                    "property_assertions": properties,
                    "assertion_id": record["assertion_id"],
                    "source_ref": "assertion:" + record["assertion_id"],
                    "provenance": record["candidate"]["provenance"],
                    "snapshot_id": self.snapshot_id,
                }
            )
        return output
