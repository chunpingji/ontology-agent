"""Identity-only canonicalization keeps evidence and versioned references intact."""

from __future__ import annotations

import json
import unittest

from app.evaluation.instance_registry import canonicalize_candidates
from app.schemas.evidence import Candidate, CandidateRef, EvidenceScope

SCHEMA = {
    "urn:Equipment": {"parents": [], "properties": [
        {"iri": "urn:equipmentID", "identity_key": True},
        {"iri": "urn:modelSpecification"},
    ]},
    "urn:Reactor": {"parents": ["urn:Equipment"], "properties": []},
    "urn:Centrifuge": {"parents": ["urn:Equipment"], "properties": []},
}


def entity(cid, *, iri="urn:instance:1", key="EQ123", cls="urn:Equipment", **updates):
    identity = {}
    if iri is not None:
        identity["instance_iri"] = iri
    if key is not None:
        identity.update(key_predicate="urn:equipmentID", key_value=key)
    data = {
        "candidate_id": cid, "kind": "entity", "class_iri": cls, "text": "设备",
        "validation_status": "passed", "identity": identity,
        "type_verification": {"supported": True, "identity_supported": True,
                              "reason": "verified explicit record identifier"},
        "provenance": [{"kind": "document", "anchors": [{
            "document_hash": "a" * 64, "structure_hash": "b" * 64,
            "parser_version": "v1", "evidence_id": f"e-{cid}",
            "section_node_id": "section", "block_id": f"b-{cid}",
        }], "excerpts": [f"设备 {key}"]}],
    }
    data.update(updates)
    return Candidate.model_validate(data)


def ref(candidate, **updates):
    return CandidateRef(candidate_id=candidate.candidate_id, revision=candidate.revision,
                        class_iri=candidate.class_iri,
                        instance_iri=candidate.identity.get("instance_iri")).model_copy(update=updates)


def scope(candidate, *, scope_id="scope", parent=None):
    return EvidenceScope(scope_id=scope_id, document_hash="a" * 64, subject=ref(candidate),
                         ranges=[{"evidence_id": f"e-{candidate.candidate_id}"}],
                         parent_scope_id=parent)


def relationship(cid, subject, obj, **updates):
    data = {
        "candidate_id": cid, "kind": "relationship", "subject": ref(subject),
        "object": ref(obj), "predicate_iri": "urn:uses", "validation_status": "passed",
        "provenance": subject.provenance,
        "bindings": [{"method": "explicit_assertion", "subject_candidate_id": subject.candidate_id,
                      "predicate_iri": "urn:uses", "object_candidate_id": obj.candidate_id,
                      "provenance_indexes": [0]}],
    }
    data.update(updates)
    return Candidate.model_validate(data)


class InstanceRegistryTests(unittest.TestCase):
    def test_verified_iri_and_key_merge_with_original_mentions_and_no_input_mutation(self):
        a, b = entity("a"), entity("b", text="反应设备")
        a.scope = scope(a)
        b.scope = scope(b, scope_id="b-scope")
        before = [c.model_dump(mode="json") for c in (a, b)]
        result = canonicalize_candidates([b, a], SCHEMA)
        self.assertEqual(list(result.candidates), ["a"])
        self.assertEqual(result.alias_map, {"b": "a"})
        self.assertEqual(result.aliases["a"], ["设备", "反应设备"])
        self.assertEqual(result.mentions["a"], before)
        self.assertEqual(len(result.candidates["a"].provenance), 2)
        self.assertEqual(result.candidates["a"].revision, 2)
        self.assertIsNone(result.candidates["a"].scope)
        self.assertEqual(result.scope_rebuild_ids, ["a"])
        self.assertEqual(result.invalidated_scope_ids, ["b-scope", "scope"])
        self.assertEqual([c.model_dump(mode="json") for c in (a, b)], before)
        json.dumps(result.to_dict())
        repeated = canonicalize_candidates(result.candidates, SCHEMA)
        self.assertEqual(repeated.candidates, result.candidates)
        self.assertEqual(repeated.changed_candidate_ids, [])

    def test_exact_legal_key_without_iri_is_sufficient_but_does_not_invent_iri(self):
        result = canonicalize_candidates([entity("a", iri=None), entity("b", iri=None)], SCHEMA)
        self.assertEqual(result.alias_map, {"b": "a"})
        self.assertNotIn("instance_iri", result.candidates["a"].identity)

    def test_verified_iri_without_key_is_sufficient(self):
        result = canonicalize_candidates([entity("a", key=None), entity("b", key=None)], SCHEMA)
        self.assertEqual(result.alias_map, {"b": "a"})

    def test_names_and_model_specifications_never_establish_identity(self):
        a, b = entity("a", iri=None, key=None), entity("b", iri=None, key=None)
        self.assertEqual(canonicalize_candidates([a, b], SCHEMA).alias_map, {})
        for candidate in (a, b):
            candidate.identity = {"key_predicate": "urn:modelSpecification", "key_value": "500L"}
        result = canonicalize_candidates([a, b], SCHEMA)
        self.assertEqual(result.alias_map, {})
        self.assertEqual({c["code"] for c in result.conflicts}, {"invalid_identity_key"})

    def test_alternative_identifiers_block_even_equal_verified_iris(self):
        for key in ("PF64216或PF64616", "A or B", "A/B", "PF64216-PF64616", "A、B"):
            with self.subTest(key=key):
                result = canonicalize_candidates(
                    [entity("a", key=key), entity("b", key=key)], SCHEMA,
                )
                self.assertEqual(result.alias_map, {})
                self.assertEqual({c["code"] for c in result.conflicts},
                                 {"ambiguous_identity_value"})

    def test_rejected_or_unverified_identity_is_not_merged(self):
        for updates in (
            {"validation_status": "rejected"}, {"review_status": "rejected"},
            {"type_verification": None},
            {"type_verification": {"supported": True, "identity_supported": False, "reason": "no"}},
            {"assertion_status": "uncertain"},
        ):
            with self.subTest(updates=updates):
                result = canonicalize_candidates([entity("a"), entity("b", **updates)], SCHEMA)
                self.assertEqual(result.alias_map, {})

    def test_conflicting_identity_components_cannot_merge_through_a_bridge(self):
        a, b, bridge = entity("a"), entity("b", iri="urn:other"), entity("c", iri=None)
        result = canonicalize_candidates([a, bridge, b], SCHEMA)
        self.assertEqual(result.alias_map, {})
        self.assertEqual(result.conflicts[0]["code"], "conflicting_instance_iris")
        self.assertEqual(result.conflicts[0]["candidate_ids"], ["a", "b", "c"])
        result = canonicalize_candidates([a, entity("b", key="EQ999")], SCHEMA)
        self.assertEqual(result.alias_map, {})
        self.assertEqual(result.conflicts[0]["code"], "conflicting_identity_keys")

    def test_compatible_ancestor_types_preserve_most_specific_verified_type(self):
        result = canonicalize_candidates([entity("a"), entity("b", cls="urn:Reactor")], SCHEMA)
        self.assertEqual(result.candidates["a"].class_iri, "urn:Reactor")
        self.assertEqual(result.reference_map[("a", 1)].class_iri, "urn:Reactor")
        siblings = canonicalize_candidates([
            entity("a", cls="urn:Reactor"), entity("b", cls="urn:Centrifuge"),
        ], SCHEMA)
        self.assertEqual(siblings.alias_map, {})
        self.assertEqual(siblings.conflicts[0]["code"], "incompatible_identity_types")

    def test_all_endpoint_path_dependency_and_scope_references_are_rewritten(self):
        a, b = entity("a"), entity("b", revision=3)
        root = entity("root", iri="urn:root", key="ROOT")
        edge = relationship("edge", b, root, path_root=ref(b), dependency_refs=[ref(b)],
                            scope=scope(b, scope_id="old-scope"))
        dependent = relationship("dependent", root, b, dependency_refs=[ref(edge)])
        result = canonicalize_candidates([a, b, root, edge, dependent], SCHEMA)
        canonical = result.candidates["a"]
        rewritten = result.candidates["edge"]
        self.assertEqual(canonical.revision, 4)
        for endpoint in (rewritten.subject, rewritten.path_root, rewritten.dependency_refs[0],
                         rewritten.scope.subject):
            self.assertEqual(endpoint, ref(canonical))
        self.assertEqual(rewritten.revision, 2)
        self.assertEqual(rewritten.bindings[0].subject_candidate_id, "a")
        self.assertEqual(rewritten.bindings[0].provenance_indexes, [0])
        self.assertEqual(rewritten.provenance, edge.provenance)
        self.assertEqual(rewritten.scope.ranges, edge.scope.ranges)
        self.assertEqual(rewritten.scope.revision, 2)
        self.assertNotEqual(rewritten.scope.scope_id, "old-scope")
        last = result.candidates["dependent"]
        self.assertEqual(last.object, ref(canonical))
        self.assertEqual(last.bindings[0].object_candidate_id, "a")
        self.assertEqual(last.dependency_refs, [ref(rewritten)])
        self.assertEqual(result.reference_map[("b", 3)], ref(canonical))
        self.assertEqual(canonicalize_candidates(result.candidates, SCHEMA).candidates,
                         result.candidates)

    def test_stale_missing_or_mismatching_references_fail_closed_and_invalidate_paths(self):
        for bad_ref in (CandidateRef(candidate_id="b", revision=99),
                        CandidateRef(candidate_id="missing", revision=1),
                        CandidateRef(candidate_id="b", revision=1, class_iri="urn:Wrong")):
            with self.subTest(ref=bad_ref):
                a, b, root = entity("a"), entity("b"), entity("root", iri=None, key="ROOT")
                edge = relationship("edge", b, root, path_root=bad_ref)
                dependent = relationship("dependent", root, b, dependency_refs=[ref(edge)])
                result = canonicalize_candidates([a, b, root, edge, dependent], SCHEMA)
                self.assertEqual(result.candidates["edge"].path_root, bad_ref)
                self.assertEqual(result.candidates["edge"].validation_status, "conflict")
                self.assertEqual(result.candidates["dependent"].validation_status, "conflict")
                self.assertEqual(canonicalize_candidates(result.candidates, SCHEMA).candidates,
                                 result.candidates)

    def test_cycles_terminate_and_bump_only_once(self):
        a, b = entity("a"), entity("b")
        root = entity("root", iri=None, key="ROOT")
        edge = relationship("edge", b, root)
        other = relationship("other", root, b, dependency_refs=[ref(edge)])
        edge.dependency_refs = [ref(other)]
        result = canonicalize_candidates([a, b, root, edge, other], SCHEMA)
        self.assertEqual(result.candidates["edge"].revision, 2)
        self.assertEqual(result.candidates["other"].revision, 2)
        self.assertEqual(result.candidates["edge"].dependency_refs[0].revision, 2)

    def test_scope_parent_invalidation_revises_owner_and_downstream_references(self):
        a, b, root = entity("a"), entity("b"), entity("root", iri="urn:root", key="ROOT")
        b.scope = scope(b, scope_id="merged-parent")
        edge = relationship("edge", root, root,
                            scope=scope(root, scope_id="child", parent="merged-parent"))
        dependent = relationship("dependent", root, root, dependency_refs=[ref(edge)])
        result = canonicalize_candidates([a, b, root, edge, dependent], SCHEMA)
        rewritten = result.candidates["edge"]
        self.assertIsNone(rewritten.scope.parent_scope_id)
        self.assertEqual(rewritten.scope.revision, 2)
        self.assertEqual(rewritten.scope.ranges, edge.scope.ranges)
        self.assertEqual(rewritten.revision, 2)
        self.assertEqual(result.candidates["dependent"].dependency_refs[0].revision, 2)
        self.assertEqual(canonicalize_candidates(result.candidates, SCHEMA).candidates,
                         result.candidates)

    def test_scope_parent_alias_is_rewritten_without_widening_child(self):
        a, b, root = entity("a"), entity("b"), entity("root", iri="urn:root", key="ROOT")
        parent = relationship("parent", b, root, scope=scope(b, scope_id="parent-scope"))
        child = relationship("child", root, root,
                             scope=scope(root, scope_id="child-scope", parent="parent-scope"))
        result = canonicalize_candidates([a, b, root, parent, child], SCHEMA)
        self.assertEqual(result.candidates["child"].scope.parent_scope_id,
                         result.candidates["parent"].scope.scope_id)
        self.assertEqual(result.candidates["child"].revision, 2)
        self.assertEqual(result.candidates["child"].scope.ranges, child.scope.ranges)

    def test_preferred_existing_id_is_stable_and_input_order_does_not_matter(self):
        a, z = entity("a"), entity("z")
        first = canonicalize_candidates([a, z], SCHEMA, preferred_ids=["z"])
        second = canonicalize_candidates([z, a], SCHEMA, preferred_ids=["z"])
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.alias_map, {"a": "z"})

    def test_duplicate_current_ids_are_an_error_not_silent_overwrite(self):
        with self.assertRaisesRegex(ValueError, "candidate IDs must be unique"):
            canonicalize_candidates([entity("a"), entity("a", revision=2)], SCHEMA)


if __name__ == "__main__":
    unittest.main()
