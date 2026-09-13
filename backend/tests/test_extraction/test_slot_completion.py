"""Expert review cannot confer proof; reopening is versioned and slot-local."""

from types import SimpleNamespace

import pytest

from app.services.extraction.ontology_guided.contracts import (
    GraphProperty,
    SlotSpec,
    SubjectRef,
    VersionedRef,
)
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.slot_completion import (
    SlotCompletionIndex,
    single_value_candidate,
)


def candidate():
    # The actual closure tests provide real source anchors; this small pure gate
    # fixture deliberately has none so approval alone must never qualify it.
    return GraphProperty(
        candidate_id="value", revision=1, subject_ref=VersionedRef(id="subject", revision=1),
        predicate_iri="urn:field", predicate_label="字段", raw_value="1",
        decision_status="supported", independent_review="accepted",
        structural_valid=True, model_supported=True, policy_eligible=True,
        proof_ref=VersionedRef(id="proof", revision=1),
        decision_refs=[VersionedRef(id="decision", revision=1)],
    )


def test_expert_approval_without_source_does_not_create_a_completion_receipt():
    subject = SubjectRef(entity_id="subject", revision=1, class_iri="urn:Class")
    assert single_value_candidate(
        SlotSpec(iri="urn:field", label="字段", max_count=1), subject,
        [candidate()], DependencyIndex(),
    ) is None


def test_rejected_version_reopens_only_its_slot_and_preserves_other_receipts():
    completion = SlotCompletionIndex(SimpleNamespace(records=[], record_positions={}))
    first, second = ("subject", 1, "urn:first"), ("subject", 1, "urn:second")
    old_revision = ("subject", 2, "urn:first")
    for slot in (first, second, old_revision):
        completion.receipts[completion.key(slot)] = {"slot": list(slot), "candidate_revision": 1}
    before = completion.snapshot()
    removed = completion.reopen(first)
    assert removed["slot"] == list(first)
    assert completion.get(first) is None
    assert completion.get(second) == before["receipts"][completion.key(second)]
    assert completion.get(old_revision) == before["receipts"][completion.key(old_revision)]
    assert completion.reopen(first) is None


def test_unchecked_conflict_source_cannot_be_made_complete_by_a_stop_request():
    completion = SlotCompletionIndex(SimpleNamespace(
        records=["row"], record_positions={"row": 0},
    ))
    plan = SimpleNamespace(ledger={"row": SimpleNamespace(coverage_state="unattempted")})
    with pytest.raises(ValueError, match="unchecked conflict"):
        completion.close(("subject", 1, "urn:field"), plan=plan, candidate=candidate(),
                         checked_record_ids=["row"], after_outcomes=1)
