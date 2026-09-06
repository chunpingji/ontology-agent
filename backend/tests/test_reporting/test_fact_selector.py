"""Snapshot selection contracts; fixtures are not real-model quality evidence."""

import pytest

from app.schemas.evidence import BindingEvidence, Candidate, CandidateRef, LiteralValue
from app.services.fact_selector import FactSelector
from app.services.ontology_instance_writer import assertion_record
from tests.test_extraction.test_fact_commit import entity, manual


def relation(identity, subject, target, predicate="urn:uses", polarity="affirmed"):
    return Candidate(
        candidate_id=identity,
        kind="relationship",
        subject=CandidateRef(candidate_id=subject, revision=1),
        object=CandidateRef(candidate_id=target, revision=1),
        predicate_iri=predicate,
        assertion_status=polarity,
        provenance=[manual(identity)],
        validation_status="passed",
        bindings=[
            BindingEvidence(
                method="manual_decision",
                subject_candidate_id=subject,
                object_candidate_id=target,
                predicate_iri=predicate,
                provenance_indexes=[0],
            )
        ],
    )


def property_value(identity, subject, predicate="urn:material", value="steel"):
    return Candidate(
        candidate_id=identity,
        kind="property",
        subject=CandidateRef(candidate_id=subject, revision=1),
        predicate_iri=predicate,
        literal=LiteralValue(kind="text", raw_value=value, normalized_value=value),
        provenance=[manual(value)],
        validation_status="passed",
        bindings=[
            BindingEvidence(
                method="manual_decision",
                subject_candidate_id=subject,
                predicate_iri=predicate,
                provenance_indexes=[0],
            )
        ],
    )


def snapshot(*values, identity="snapshot-1"):
    values = [c.model_copy(update={"review_status": "confirmed"}) for c in values]
    entities = {c.candidate_id: c for c in values if c.kind == "entity"}
    return {"snapshot_id": identity, "assertions": [assertion_record(c, entities) for c in values]}


def iri(name):
    return "urn:evidence:entity:" + name


def test_exact_subject_predicate_endpoint_properties():
    selector = FactSelector(
        snapshot(
            entity("A"),
            entity("B"),
            entity("E", "urn:Equipment"),
            entity("F", "urn:Equipment"),
            relation("ae", "A", "E"),
            relation("bf", "B", "F", "urn:stores"),
            property_value("em", "E"),
        )
    )
    selected = selector.select(iri("A"), ["urn:uses"], required_properties=["urn:material"])
    assert selected["qualified_object_iris"] == [iri("E")]
    assert selector.select(iri("B"), ["urn:uses"])["objects"] == []
    assert selector.select(iri("A"), ["urn:uses"], object_iris=[iri("F")])["objects"] == []
    assert (
        selector.select(iri("B"), ["urn:stores"], required_properties=["urn:material"])[
            "qualified_object_iris"
        ]
        == []
    )


def test_full_path_direction_and_subclass():
    selector = FactSelector(
        snapshot(
            entity("A"),
            entity("E", "urn:Reactor"),
            entity("W", "urn:Workshop"),
            relation("ae", "A", "E"),
            relation("ew", "E", "W", "urn:locatedIn"),
        ),
        schema={"urn:Reactor": {"parents": ["urn:Equipment"]}},
    )
    assert selector.select(iri("A"), ["urn:uses"], range_class_iri="urn:Equipment")[
        "qualified_object_iris"
    ] == [iri("E")]
    assert selector.select(iri("A"), ["urn:uses", "urn:locatedIn"])["qualified_object_iris"] == [
        iri("W")
    ]
    assert selector.select(iri("W"), [{"predicate_iri": "urn:locatedIn", "direction": "inverse"}])[
        "qualified_object_iris"
    ] == [iri("E")]
    assert selector.select(iri("A"), ["urn:locatedIn"])["objects"] == []


def test_negative_only_supports_explicit_matching_object_scope():
    selector = FactSelector(
        snapshot(
            entity("A"),
            entity("E"),
            relation("no", "A", "E", polarity="negated"),
        )
    )
    assert selector.select(iri("A"), ["urn:uses"])["confirmed_absent"] is False
    selected = selector.select(iri("A"), ["urn:uses"], object_iris=[iri("E")])
    assert selected["confirmed_absent"] is True
    assert selected["negative_assertion_ids"]
    assert not selected["objects"]
    assert not selector.select(iri("A"), ["urn:uses"], object_iris=[iri("F")])["confirmed_absent"]


def test_positive_and_negative_conflict_not_filled_or_absent():
    selector = FactSelector(
        snapshot(
            entity("A"),
            entity("E"),
            relation("yes", "A", "E"),
            relation("no", "A", "E", polarity="negated"),
        )
    )
    selected = selector.select(iri("A"), ["urn:uses"], object_iris=[iri("E")])
    assert selected["conflict_assertion_ids"]
    assert not selected["qualified_object_iris"]
    assert not selected["confirmed_absent"]


def test_unpublished_unconfirmed_and_forged_positive_flag_fail_closed():
    with pytest.raises(ValueError, match="published"):
        FactSelector({"snapshot_id": None, "assertions": []})
    data = snapshot(entity("A"))
    data["assertions"][0]["candidate"]["review_status"] = "pending"
    with pytest.raises(ValueError, match="confirmed"):
        FactSelector(data)
    data = snapshot(entity("A"), entity("E"), relation("no", "A", "E", polarity="negated"))
    data["assertions"][-1]["positive_eligible"] = True
    assert FactSelector(data).select(iri("A"), ["urn:uses"])["objects"] == []


def test_distinct_values_preserved_and_scalar_cardinality_conflict():
    selector = FactSelector(
        snapshot(
            entity("A"),
            entity("E", "urn:Equipment"),
            relation("ae", "A", "E"),
            property_value("m1", "E"),
            property_value("m2", "E", value="glass"),
        ),
        schema={"urn:Equipment": {"properties": [{"iri": "urn:material", "max_count": 1}]}},
    )
    selected = selector.select(iri("A"), ["urn:uses"], required_properties=["urn:material"])
    assert selected["conflict_assertion_ids"]
    assert not selected["qualified_object_iris"]


def test_equivalent_normalized_values_do_not_create_cardinality_conflict():
    first = property_value("m1", "E").model_copy(
        update={
            "literal": LiteralValue(
                kind="number",
                raw_value="1.0 kg",
                normalized_value="1.0",
                canonical_unit="kg",
                datatype_iri="http://www.w3.org/2001/XMLSchema#decimal",
            )
        }
    )
    second = first.model_copy(
        update={
            "candidate_id": "m2",
            "literal": first.literal.model_copy(
                update={"raw_value": "1000 g", "normalized_value": "1", "raw_unit": "g"},
            ),
        }
    )
    selector = FactSelector(
        snapshot(
            entity("A"), entity("E", "urn:Equipment"), relation("ae", "A", "E"), first, second
        ),
        schema={"urn:Equipment": {"properties": [{"iri": "urn:material", "max_count": 1}]}},
    )
    assert not selector.conflicts()


def test_time_bound_negative_does_not_prove_unbounded_absence():
    negative = relation("no", "A", "E", polarity="negated").model_copy(
        update={"applicable_at": "2026-09-05"}
    )
    selector = FactSelector(snapshot(entity("A"), entity("E"), negative))
    assert not selector.select(iri("A"), ["urn:uses"], object_iris=[iri("E")])["confirmed_absent"]
    assert selector.select(
        iri("A"), ["urn:uses"], object_iris=[iri("E")], applicable_at="2026-09-05"
    )["confirmed_absent"]
