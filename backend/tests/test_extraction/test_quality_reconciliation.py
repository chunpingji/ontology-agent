"""Late ownership reconciliation is evidence-local and fails closed on paths."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.evaluation.quality_reconciliation import invalidate_late_competitors
from app.schemas.evidence import EvidenceRange, ExtractionTask

from .test_instance_registry import SCHEMA, entity, ref, relationship, scope


class Source:
    document_hash = "a" * 64

    def unit(self, evidence_id):
        return SimpleNamespace(text="source evidence")

    def resolve(self, anchor):
        if anchor.document_hash != self.document_hash:
            raise ValueError("wrong document")
        return self.unit(anchor.evidence_id).text


def setup(*, key="EQ999", iri="urn:late", in_scope=True, **late_updates):
    owner, late = entity("owner"), entity("late", key=key, iri=iri, **late_updates)
    target = entity("target", iri="urn:target", key="TARGET")
    accepted = scope(owner)
    if in_scope:
        accepted.ranges.append(EvidenceRange(evidence_id="e-late"))
    edge = relationship("edge", owner, target, task_id="original", scope=accepted)
    dependent = relationship("dependent", target, owner, dependency_refs=[ref(edge)])
    task = ExtractionTask(task_id="original", task_kind="relationship", subject=ref(owner),
                          predicate_iri="urn:uses", target_evidence_ids=["e-owner"])
    candidates = {c.candidate_id: c for c in (owner, late, target, edge, dependent)}
    return candidates, {"original": task}


class QualityReconciliationTests(unittest.TestCase):
    def test_unseen_same_scope_competitor_invalidates_ownership_and_dependents(self):
        candidates, tasks = setup()
        before = {cid: c.model_dump(mode="json") for cid, c in candidates.items()}
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.candidates["edge"].validation_status, "conflict")
        self.assertEqual(result.candidates["dependent"].validation_status, "conflict")
        self.assertEqual(result.candidates["edge"].revision, 2)
        self.assertEqual(result.candidates["dependent"].dependency_refs[0].revision, 2)
        self.assertEqual(len(result.invalidations), 2)
        self.assertEqual({cid: c.model_dump(mode="json") for cid, c in candidates.items()}, before)
        repeated = invalidate_late_competitors(result.candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(repeated.candidates, result.candidates)
        self.assertEqual(repeated.invalidations, [])

    def test_same_verified_instance_is_not_a_competitor_and_ids_are_preserved(self):
        candidates, tasks = setup(key="EQ123", iri="urn:instance:1")
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.candidates, candidates)
        self.assertEqual(result.invalidations, [])

    def test_same_name_or_unverified_iri_does_not_excuse_a_competitor(self):
        candidates, tasks = setup(iri="urn:instance:1", type_verification={
            "supported": True, "identity_supported": False, "reason": "unverified identity",
        })
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.candidates["edge"].validation_status, "conflict")

    def test_previously_known_competitor_is_not_late(self):
        candidates, tasks = setup()
        tasks["original"].competing_subjects = [ref(candidates["late"])]
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.invalidations, [])

    def test_other_scope_or_rejected_candidate_does_not_invalidate(self):
        for kwargs in ({"in_scope": False}, {"validation_status": "rejected"}):
            candidates, tasks = setup(**kwargs)
            result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
            self.assertEqual(result.invalidations, [])

    def test_document_metadata_root_is_not_an_ordinary_instance(self):
        candidates, tasks = setup()
        candidates["owner"].identity = {"document_root": Source.document_hash}
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.invalidations, [])

    def test_compatible_ancestor_but_not_sibling_types_compete(self):
        candidates, tasks = setup(cls="urn:Reactor")
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(len(result.invalidations), 2)
        candidates["owner"].class_iri = "urn:Centrifuge"
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.invalidations, [])

    def test_invalidated_path_root_invalidates_entity_and_its_outgoing_edge(self):
        candidates, tasks = setup()
        child = entity("child", iri="urn:child", key="CHILD", path_root=ref(candidates["edge"]))
        leaf = relationship("leaf", child, candidates["target"])
        candidates.update(child=child, leaf=leaf)
        result = invalidate_late_competitors(candidates, SCHEMA, Source(), ["late"], tasks)
        self.assertEqual(result.candidates["child"].validation_status, "conflict")
        self.assertEqual(result.candidates["leaf"].validation_status, "conflict")
        self.assertEqual(result.candidates["leaf"].subject.revision, 2)


if __name__ == "__main__":
    unittest.main()
