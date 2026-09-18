"""027 graph contracts preserve frozen legacy payloads and group/scope meaning."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    GraphEdge,
    GraphNode,
    GraphProperty,
    GraphRelationshipGroup,
    GraphSnapshot,
    LocalReferent,
    PredicateEvidence,
    ScopeMember,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)


def ref(identifier: str, revision: int = 1) -> VersionedRef:
    return VersionedRef(id=identifier, revision=revision)


def anchor() -> EvidenceAnchor:
    return EvidenceAnchor(
        document_hash="a" * 64, parser_version="parser-v1", structure_hash="b" * 64,
        evidence_id="ev", section_node_id="section", block_id="block", span_start=0,
        span_end=4,
    )


def target_values() -> dict:
    return {
        "run_fingerprint": "run",
        "claim_ref": ref("e"),
        "task_id": "task",
        "check_kind": "predicate_entailment",
        "document_context": DocumentContext(
            document_hash="a" * 64, document_class_iri="urn:Document", root_ref=ref("subject"),
        ),
        "subject_ref": SubjectRef(entity_id="subject", revision=1, class_iri="urn:Subject"),
        "predicate_iri": "urn:related",
        "object_ref": ref("object"),
        "ontology_hash": "ontology",
        "source_scope_hash": "source-scope",
        "context_hash": "context",
    }


def legacy_objects() -> dict:
    node = GraphNode(
        entity_id="subject", revision=1, class_iri="urn:Subject", class_label="主体",
        label="S", root=True, root_origin="user_specified",
    )
    return {
        "referent": LocalReferent(referent_id="ref", mention_refs=["mention"]),
        "node": node,
        "property": GraphProperty(
            candidate_id="p", revision=1, subject_ref=ref("subject"), predicate_iri="urn:value",
            predicate_label="值", raw_value="1", decision_status="undetermined",
        ),
        "edge": GraphEdge(
            candidate_id="e", revision=1, subject_ref=ref("subject"), object_ref=ref("object"),
            predicate_iri="urn:related", predicate_label="关联", decision_status="undetermined",
        ),
        "proof": PredicateEvidence(
            proof_id="proof", target_id="target", predicate_iri="urn:related",
            subject_role_refs=[ref("subject")], bridge_kind="explicit_assertion",
            verdict="not_checked",
        ),
        "target": VerificationTarget.create(**target_values()),
        "snapshot": GraphSnapshot.empty_root(recognition_run_id="run", root=node),
    }


@pytest.mark.parametrize(("name", "frozen_hash"), [
    ("referent", "0abb952d2ccafbd072b05ea851b3fe6be9f2ba985cf4c710731e6e50801593e8"),
    ("node", "a7b5dbf660e75d8081e393c589dee86f6f13f8cf6e3640cd38fac30a554e2a31"),
    ("property", "966f2d4e392b1025ceb9825c5a527f483af73b7f40187d502fde756dc935435c"),
    ("edge", "35e3e0c9ae28babe64178cf0d1b09f29811bda25ba7183c6d27dc7855c58a36a"),
    ("proof", "4bd64108dfd9e5ae55a56d3643db54d7ae7b547f4afb4ae1c633eedec08a5f96"),
    ("target", "d7161a2f4a10d935ef445d4e7f2db0ca9107bd3768823e697065ddee084a360e"),
    ("snapshot", "cf7c6721b0d8a54d9db3af8cee5b4c58addb4c5a423b1bed878d7b261c269e90"),
])
def test_legacy_payload_and_identity_survive_cold_roundtrip(name, frozen_hash):
    # Captured from the contracts before adding 027 fields, not recomputed expectations.
    original = legacy_objects()[name]
    serialized = original.model_dump_json()
    restored = type(original).model_validate_json(serialized)
    assert evidence_hash(original) == frozen_hash
    assert evidence_hash(restored) == frozen_hash
    assert restored.model_dump_json() == serialized


def test_old_target_factory_keeps_frozen_id():
    target = VerificationTarget.create(**target_values())
    assert target.target_id == "cf10977b32e05f55d87af39d3f6a6392c9de9ae1c2241058f840dc475e7879ef"
    assert "object_refs" not in target.model_dump()
    assert "selection" not in target.model_dump()


def test_scope_is_canonical_ordered_and_bound_to_exact_versions():
    first = ScopeMember(relation_ref=ref("group-a"), member_ref=ref("a"))
    second = ScopeMember(relation_ref=ref("group-b"), member_ref=ref("b"))
    ordered = TraversalScope.create([first, second])
    reversed_input = TraversalScope(scope_id=ordered.scope_id, members=[second, first])
    assert reversed_input.model_dump() == ordered.model_dump()
    assert ordered.scope_id == evidence_hash([first, second])
    changed = TraversalScope.create([
        ScopeMember(relation_ref=ref("group-a", 2), member_ref=ref("a")), second,
    ])
    assert changed.scope_id != ordered.scope_id
    assert TraversalScope.create().scope_id == evidence_hash([])


def test_scope_rejects_duplicate_members_and_forged_or_stale_hash():
    member = ScopeMember(relation_ref=ref("group"), member_ref=ref("a"))
    with pytest.raises(ValidationError, match="repeat"):
        TraversalScope.create([member, member])
    with pytest.raises(ValidationError, match="canonical members"):
        TraversalScope(scope_id="a" * 64, members=[member])
    scope = TraversalScope.create([member])
    payload = scope.model_dump(mode="json")
    payload["members"][0]["member_ref"]["revision"] = 2
    with pytest.raises(ValidationError, match="canonical members"):
        TraversalScope.model_validate(payload)


@pytest.mark.parametrize("name", ["edge", "property"])
def test_new_statement_fields_roundtrip_even_when_explicit_defaults(name):
    original = legacy_objects()[name]
    scope = TraversalScope.create([
        ScopeMember(relation_ref=ref("group"), member_ref=ref("subject")),
    ])
    statement = type(original).model_validate({
        **original.model_dump(), "modality": "required", "scope": scope,
    })
    restored = type(statement).model_validate_json(statement.model_dump_json())
    assert restored.modality == "required"
    assert restored.scope == scope
    assert evidence_hash(restored) != evidence_hash(original)
    explicit = type(original).model_validate({
        **original.model_dump(), "modality": "unspecified", "scope": TraversalScope.create(),
    })
    assert explicit.model_dump()["modality"] == "unspecified"
    assert explicit.model_dump()["scope"]["members"] == []


def group_values() -> dict:
    return {
        "candidate_id": "group", "revision": 1, "subject_ref": ref("subject"),
        "object_refs": [ref("a"), ref("b")], "selection": "one_of",
        "selection_evidence_refs": [anchor()], "predicate_iri": "urn:related",
        "predicate_label": "关联", "decision_status": "supported", "modality": "asserted",
    }


@pytest.mark.parametrize("selection", ["all", "one_of", "alternatives"])
def test_group_preserves_selection_and_members_without_single_edge(selection):
    group = GraphRelationshipGroup(**{**group_values(), "selection": selection})
    payload = group.model_dump(mode="json")
    assert "object_ref" not in payload
    assert payload["selection"] == selection
    assert payload["object_refs"] == [{"id": "a", "revision": 1}, {"id": "b", "revision": 1}]
    assert GraphRelationshipGroup.model_validate_json(group.model_dump_json()) == group
    with pytest.raises(ValidationError):
        GraphEdge.model_validate(payload)


@pytest.mark.parametrize("change", [
    {"object_refs": [ref("a")]},
    {"object_refs": [ref("a"), ref("a")]},
    {"object_refs": [ref("a"), ref("a", 2)]},
    {"object_ref": ref("a")},
    {"selection_evidence_refs": []},
    {"selection": "undetermined"},
])
def test_group_rejects_wrong_cardinality_duplicate_objects_and_unproven_selection(change):
    with pytest.raises(ValidationError):
        GraphRelationshipGroup.model_validate({**group_values(), **change})


def test_unresolved_group_remains_available_for_diagnostic_output():
    group = GraphRelationshipGroup(**{
        **group_values(), "selection": "undetermined", "selection_evidence_refs": [],
        "decision_status": "undetermined",
    })
    assert group.selection == "undetermined"
    assert not group.policy_eligible
    with pytest.raises(ValidationError, match="proven selection"):
        GraphRelationshipGroup.model_validate({**group.model_dump(), "policy_eligible": True})


def test_group_target_binds_selection_all_members_and_exact_revisions():
    values = {
        **target_values(), "object_ref": None, "object_refs": [ref("a"), ref("b")],
        "selection": "one_of", "check_kind": "selection",
    }
    base = VerificationTarget.create(**values)
    targets = [
        base,
        VerificationTarget.create(**{**values, "selection": "alternatives"}),
        VerificationTarget.create(**{**values, "object_refs": [ref("a"), ref("c")]}),
        VerificationTarget.create(**{**values, "object_refs": [ref("a"), ref("b", 2)]}),
    ]
    assert len({target.target_id for target in targets}) == 4
    assert VerificationTarget.model_validate_json(base.model_dump_json()) == base


@pytest.mark.parametrize("change", [
    {"object_refs": [ref("a")]},
    {"object_refs": [ref("a"), ref("a", 2)]},
    {"object_ref": ref("a")},
    {"selection": None},
    {"object_refs": []},
])
def test_group_target_rejects_ambiguous_endpoint_shape(change):
    values = {
        **target_values(), "object_ref": None, "object_refs": [ref("a"), ref("b")],
        "selection": "all", "check_kind": "selection",
    }
    with pytest.raises(ValidationError):
        VerificationTarget.create(**{**values, **change})


def test_record_referent_and_node_have_composition_without_fabricated_mentions():
    referent = LocalReferent(
        referent_id="record-referent", kind="record", record_view_refs=["record-view"],
        composition_decision_ref=ref("composition-decision"),
    )
    node = GraphNode(
        entity_id="entity", revision=1, class_iri="urn:Record", class_label="记录", label="记录1",
        grounding_kind="record", referent_ref=ref("record-referent"),
        type_decision_ref=ref("type-decision"), referent_decision_ref=ref("referent-decision"),
        composition_decision_ref=ref("composition-decision"), dependency_refs=[ref("record-view")],
    )
    assert referent.mention_refs == []
    assert LocalReferent.model_validate_json(referent.model_dump_json()) == referent
    assert GraphNode.model_validate_json(node.model_dump_json()) == node
    for model in (referent, node):
        with pytest.raises(ValidationError, match="composition decision"):
            type(model).model_validate({**model.model_dump(), "composition_decision_ref": None})
    with pytest.raises(ValidationError, match="physical mention"):
        LocalReferent(referent_id="empty", kind="mention", mention_refs=[])


def test_root_grounding_cannot_be_attached_to_an_ordinary_node():
    root = legacy_objects()["node"]
    explicit = GraphNode.model_validate({**root.model_dump(), "grounding_kind": "document_root"})
    assert explicit.root_origin == "user_specified"
    with pytest.raises(ValidationError, match="root node"):
        GraphNode.model_validate({**explicit.model_dump(), "root": False})
    with pytest.raises(ValidationError, match="non-root referent"):
        GraphNode.model_validate({**root.model_dump(), "grounding_kind": "mention"})


def test_selection_modality_proof_and_group_snapshot_survive_roundtrip():
    proof = PredicateEvidence.model_validate({
        **legacy_objects()["proof"].model_dump(),
        "selection_support_refs": [anchor()], "modality_support_refs": [],
    })
    assert "modality_support_refs" in proof.model_dump()
    assert PredicateEvidence.model_validate_json(proof.model_dump_json()) == proof
    snapshot = GraphSnapshot.model_validate({
        **legacy_objects()["snapshot"].model_dump(),
        "projection": "verified", "relationship_groups": [GraphRelationshipGroup(**group_values())],
    })
    restored = GraphSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.relationship_groups[0].selection == "one_of"
    assert restored.edges == []
    assert restored.projection == "verified"
