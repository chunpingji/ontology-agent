"""External candidates require explicit sources and keep query coverage separate."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.schemas.evidence import ExternalRecordProvenance
from app.services.extraction.external_records import (
    FrozenInstanceReader,
    IdentityKeyComponent,
    InstanceQuery,
    InstanceSearchResult,
    ResolvedRecord,
    build_instance_query,
)
from app.services.extraction.ontology_guided.claim_protocol import (
    IdentifierProposal,
    IdentityKeySpec,
    Quote,
)


def quote(text, evidence_id="name"):
    return Quote(evidence_id=evidence_id, text=text, context_text=None)


def record(key, **fields):
    return ResolvedRecord(
        system="mock",
        dataset="items",
        key=key,
        version="v1",
        class_iri="urn:Item",
        label_field="name",
        fields={"name": "Shared name", "code": key, **fields},
        field_predicates={"code": "urn:code", "region": "urn:region", "mass": "urn:mass"},
    )


def query(*, names=("Shared name",), keys=(), sources=("source",), limit=8):
    return InstanceQuery(
        source_ids=list(sources),
        class_iri="urn:Item",
        name_quotes=[quote(name) for name in names],
        key_components=list(keys),
        limit=limit,
    )


def test_generic_reader_has_no_default_source_or_business_identifier_rule():
    result = FrozenInstanceReader({}, name_fields={}).search(query())
    assert result.candidates == []
    assert result.searched_sources == []
    assert result.incomplete_sources == ["source"]
    assert result.excluded_count is None
    unusual = record("中文/№:1", aliases=["Alias"], mass=7, note="archive note")
    reader = FrozenInstanceReader(
        {"source": [unusual]},
        name_fields={"source": ["name"]},
        alias_fields={"source": ["aliases"]},
    )
    candidate = reader.search(query(names=("Alias",))).candidates[0]
    assert candidate.record_key == "中文/№:1"
    assert candidate.matches[0].match_kind == "alias"
    assert {field.predicate_iri for field in candidate.mapped_fields} == {"urn:code", "urn:mass"}
    assert (
        next(
            field for field in candidate.mapped_fields if field.predicate_iri == "urn:mass"
        ).raw_value
        == "7"
    )
    assert "note" in {field.name for field in candidate.metadata}
    assert not hasattr(candidate, "identity_status")  # status belongs to handler's envelope


def test_equal_names_recall_multiple_records_and_report_exclusions():
    reader = FrozenInstanceReader(
        {"source": [record("two"), record("one")]},
        name_fields={"source": ["name"]},
    )
    result = reader.search(query(limit=1))
    assert [candidate.record_key for candidate in result.candidates] == ["one"]
    assert result.excluded_count == 1
    assert result.incomplete_sources == []
    complete = reader.search(query(limit=2))
    assert len(complete.candidates) == 2
    assert complete.excluded_count == 0
    assert complete.candidates[0].candidate_id == result.candidates[0].candidate_id


def test_unknown_count_never_becomes_complete_empty_match():
    reader = FrozenInstanceReader(
        {"source": []},
        name_fields={},
        incomplete_sources=["source"],
    )
    result = reader.search(query())
    assert result.excluded_count is None
    assert result.searched_sources == result.incomplete_sources == ["source"]
    with pytest.raises(ValidationError, match="unknown_excluded_count"):
        InstanceSearchResult(
            candidates=[],
            searched_sources=["source"],
            incomplete_sources=[],
            excluded_count=None,
        )


def test_only_requested_sources_and_exact_class_are_searched():
    reader = FrozenInstanceReader(
        {
            "source": [record("one"), replace(record("wrong"), class_iri="urn:Other")],
            "other": [replace(record("private"), dataset="other")],
        },
        name_fields={"source": ["name"], "other": ["name"]},
    )
    result = reader.search(query())
    assert [item.record_key for item in result.candidates] == ["one"]
    assert result.searched_sources == ["source"]
    assert result.excluded_count == 0


def test_composite_key_is_conjunctive_for_one_record_and_partial_components_are_not_hits():
    reader = FrozenInstanceReader(
        {
            "source": [
                record("A", region="north"),
                record("B", region="south"),
            ]
        },
        name_fields={},
    )
    keys = [
        IdentityKeyComponent(predicate_iri="urn:code", quote=quote("A", "key"), value="A"),
        IdentityKeyComponent(
            predicate_iri="urn:region", quote=quote("south", "region"), value="south"
        ),
    ]
    assert reader.search(query(names=(), keys=keys)).candidates == []
    keys[1] = IdentityKeyComponent(
        predicate_iri="urn:region",
        quote=quote("north", "region"),
        value="north",
    )
    result = reader.search(query(names=(), keys=keys))
    assert [item.record_key for item in result.candidates] == ["A"]
    assert [match.match_kind for match in result.candidates[0].matches] == [
        "key_component",
        "key_component",
    ]


def test_query_builder_requires_declared_complete_key_and_same_owner_scope():
    key = IdentityKeySpec(
        class_iri="urn:Item",
        property_iris=["urn:code", "urn:region"],
        namespace="items",
        scope="dataset",
        declaration_ref="profile-v1",
    )
    identifiers = [
        IdentifierProposal(predicate_iri="urn:code", value_quote=quote("A", "key")),
        IdentifierProposal(predicate_iri="urn:region", value_quote=quote("north", "region")),
    ]
    args = dict(
        source_ids=["source"],
        class_iri="urn:Item",
        name_quotes=[quote("Shared name")],
        identifier_quotes=identifiers,
        identity_key=key,
        owner_scope_by_evidence={"key": "owner1", "region": "owner1", "name": "owner1"},
        limit=8,
    )
    result = build_instance_query(**args)
    assert [item.value for item in result.key_components] == ["A", "north"]
    assert (
        build_instance_query(**(args | {"identifier_quotes": identifiers[:1]})).key_components == []
    )
    assert build_instance_query(**(args | {"identity_key": None})).key_components == []
    for scopes in ({"key": "one", "region": "two"}, {"key": "one"}):
        with pytest.raises(ValueError, match="owner_scope_unproven"):
            build_instance_query(**(args | {"owner_scope_by_evidence": scopes}))
    with pytest.raises(ValidationError, match="identity_key_value_not_quoted"):
        IdentityKeyComponent(predicate_iri="urn:code", quote=quote("A"), value="invented")


def test_frozen_records_and_mappings_cannot_be_mutated_through_resolve_or_constructor():
    original = record("one", mass="12", note="metadata")
    fields = {"source": ["name"]}
    reader = FrozenInstanceReader({"source": [original]}, name_fields=fields)
    provenance = ExternalRecordProvenance(
        system="mock",
        dataset="items",
        record_key="one",
        record_version="v1",
        field_path="mass",
        value="12",
        record_snapshot={"forged": "must not be authority"},
    )
    original.fields["mass"] = "99"
    original.field_predicates["note"] = "urn:forged"
    fields["source"].clear()
    resolved = reader.resolve(provenance)
    assert resolved.fields["mass"] == "12"
    assert "note" not in resolved.field_predicates
    resolved.fields["mass"] = "changed"
    assert reader.resolve(provenance).fields["mass"] == "12"
    assert len(reader.search(query()).candidates) == 1
    for update, expected in (
        ({"record_version": "v0"}, "version_mismatch"),
        ({"value": "99"}, "field_mismatch"),
        ({"system": "elsewhere"}, "unavailable"),
    ):
        with pytest.raises(ValueError, match=expected):
            reader.resolve(provenance.model_copy(update=update))


def test_record_identity_collisions_are_rejected_without_merging():
    with pytest.raises(ValueError, match="record_key_duplicate"):
        FrozenInstanceReader({"source": [record("one"), record("one")]}, name_fields={})
    with pytest.raises(ValueError, match="dataset_ambiguous"):
        FrozenInstanceReader({"source": [record("one")], "other": [record("two")]}, name_fields={})
    with pytest.raises(ValueError, match="configuration_unknown"):
        FrozenInstanceReader({}, name_fields={"implicit": ["name"]})


def test_empty_complete_source_reports_known_zero_and_preserves_partial_source_candidates():
    reader = FrozenInstanceReader(
        {
            "source": [record("one")],
            "empty": [],
        },
        name_fields={"source": ["name"]},
        incomplete_sources=["source"],
    )
    empty = reader.search(query(sources=("empty",)))
    assert empty.excluded_count == 0 and empty.incomplete_sources == []
    partial = reader.search(query(sources=("source", "missing")))
    assert len(partial.candidates) == 1
    assert partial.incomplete_sources == ["source", "missing"]
    assert partial.excluded_count is None
