from __future__ import annotations

from collections import defaultdict

import pytest

from app.services.extraction.ontology_guided.contracts import SubjectRef
from app.services.extraction.ontology_guided.scheduler import FrontierScheduler, RecognitionTask


def task(
    subject="root",
    predicate="urn:p",
    phase=1,
    record="record",
    position=0,
    section="section",
    kind="relationship",
    retry=None,
):
    return RecognitionTask.create(
        subject=SubjectRef(
            entity_id=subject, revision=1, class_iri="urn:Class", is_document_root=subject == "root"
        ),
        predicate_iri=predicate,
        predicate_kind=kind,
        record_id=record,
        phase=phase,
        hop=0 if subject == "root" else 1,
        dependency_hash="proof-input-only",
        section_node_id=section,
        source_position=position,
        retry_kind=retry,
    )


def enqueue(scheduler, item):
    scheduler.enqueue(item, root_branch=item.subject.is_document_root)


def test_each_slot_gets_actual_phase2_on_its_second_opportunity_with_multiple_subjects():
    scheduler = FrontierScheduler(max_tasks=200)
    for subject in ("root", "product-a", "product-b"):
        for predicate, kind in (
            ("urn:r1", "relationship"),
            ("urn:r2", "relationship"),
            ("urn:property", "property"),
        ):
            for phase in (1, 2):
                for number in range(6):
                    enqueue(
                        scheduler,
                        task(
                            subject,
                            predicate,
                            phase,
                            f"{phase}:{number}",
                            number,
                            str(number % 2),
                            kind,
                        ),
                    )
    phases = defaultdict(list)
    # Budget is sufficient for each slot's two cold-start opportunities.
    for _ in range(36):
        item = scheduler.next_task()
        phases[(item.subject.entity_id, item.predicate_iri)].append(item.phase)
    assert len(phases) == 9
    assert all(values[:2] == [1, 2] for values in phases.values())


def test_phase_ratio_section_rotation_and_tail_exploration_have_dispatch_witnesses():
    scheduler = FrontierScheduler(max_tasks=100)
    for phase in (1, 2):
        for number in range(15):
            enqueue(
                scheduler,
                task(phase=phase, record=f"{phase}:{number}", position=number, section="one"),
            )
    subject = scheduler.peek_task().subject
    scheduler.reorder_slot(
        subject,
        "urn:p",
        [f"{phase}:{number}" for phase in (1, 2) for number in reversed(range(15))],
        epoch_seq=1,
    )
    dispatched = [scheduler.next_task() for _ in range(8)]
    assert [item.phase for item in dispatched] == [1, 2, 1, 1, 1, 1, 2, 1]
    assert dispatched[5].record_id == "1:0"  # fifth phase-1 turn is oldest-record exploration
    assert dispatched[0].record_id == "1:14"


def test_peek_does_not_advance_state_and_resume_keeps_all_rotation_counters():
    scheduler = FrontierScheduler(max_tasks=100)
    for number in range(20):
        enqueue(
            scheduler,
            task(
                predicate=f"urn:{number % 2}",
                phase=1 + (number % 3 == 0),
                record=str(number),
                position=number,
                section=str(number % 3),
            ),
        )
    initial = scheduler.snapshot()
    for _ in range(4):
        assert scheduler.peek_slot() is not None
        assert scheduler.peek_task() is not None
    assert scheduler.snapshot() == initial
    for _ in range(7):
        scheduler.next_task()
    restored = FrontierScheduler.from_snapshot(scheduler.snapshot(), max_tasks=100)
    original_remaining = [scheduler.next_task() for _ in range(13)]
    restored_remaining = [restored.next_task() for _ in range(13)]
    assert original_remaining == restored_remaining
    assert scheduler.snapshot() == restored.snapshot()


def test_old_committed_pool_precedes_new_pool_but_not_phase_or_chapter_fairness():
    scheduler = FrontierScheduler()
    for number in range(6):
        enqueue(
            scheduler,
            task(
                record=str(number),
                position=number,
                phase=2 if number == 5 else 1,
                section="other" if number == 4 else "one",
            ),
        )
    subject = scheduler.peek_task().subject
    scheduler.reorder_slot(subject, "urn:p", ["2", "1"], epoch_seq=1)
    scheduler.reorder_slot(subject, "urn:p", ["3", "4", "5"], epoch_seq=2)
    assert [scheduler.next_task().record_id for _ in range(4)] == ["2", "5", "4", "1"]
    with pytest.raises(ValueError, match="cannot be overwritten"):
        scheduler.reorder_slot(subject, "urn:p", ["3"], epoch_seq=3)


def test_ranking_has_no_effect_on_semantic_task_identity_and_retry_yields():
    scheduler = FrontierScheduler()
    first = task(record="first")
    enqueue(scheduler, first)
    enqueue(scheduler, task(record="second"))
    enqueue(scheduler, task(record="failed", retry="technical_once"))
    scheduler.reorder_slot(first.subject, "urn:p", ["second", "first"], epoch_seq=1)
    items = [scheduler.next_task() for _ in range(3)]
    assert [item.record_id for item in items] == ["second", "failed", "first"]
    assert items[-1].task_id == first.task_id
    assert items[-1].claim_lineage_id == first.claim_lineage_id
    assert items[-1].dependency_hash == first.dependency_hash


def test_budget_stop_retains_unexplored_obligations():
    scheduler = FrontierScheduler(max_tasks=1)
    enqueue(scheduler, task(phase=1, record="first"))
    enqueue(scheduler, task(phase=2, record="second"))
    assert scheduler.next_task().record_id == "first"
    assert scheduler.next_task() is None
    assert scheduler.unexplored_frontier == [
        {
            "task_id": task(phase=2, record="second").task_id,
            "reason": "max_tasks",
        }
    ]


def test_registered_phase_ablation_keeps_slot_fairness_but_uses_phase1_first():
    scheduler = FrontierScheduler(phase_interleaving=False)
    for predicate in ("urn:first", "urn:second"):
        for phase, number in ((1, 1), (1, 2), (2, 3)):
            enqueue(scheduler, task(predicate=predicate, phase=phase, record=str(number)))
    dispatched = [scheduler.next_task() for _ in range(4)]
    assert [item.predicate_iri for item in dispatched] == ["urn:first", "urn:second"] * 2
    assert [item.phase for item in dispatched] == [1, 1, 1, 1]


def test_scoring_slot_yields_to_ready_slot_without_consuming_its_cold_start():
    scheduler = FrontierScheduler()
    for predicate in ("urn:scoring", "urn:ready"):
        enqueue(scheduler, task(predicate=predicate, phase=1, record="first"))
        enqueue(scheduler, task(predicate=predicate, phase=2, record="second"))
    excluded = {("root", 1, "urn:scoring")}
    initial = scheduler.snapshot()
    assert scheduler.peek_task(excluded).predicate_iri == "urn:ready"
    assert scheduler.snapshot() == initial
    assert scheduler.next_task(excluded).predicate_iri == "urn:ready"
    assert scheduler.next_task().predicate_iri == "urn:scoring"
    assert scheduler.next_task().phase == 2
    blocked = {("root", 1, "urn:scoring"), ("root", 1, "urn:ready")}
    before_block = scheduler.snapshot()
    assert scheduler.next_task(blocked) is None
    assert scheduler.snapshot() == before_block


def test_discarded_subject_keeps_dedup_and_rotation_budget():
    scheduler = FrontierScheduler()
    first = task(record="first")
    enqueue(scheduler, first)
    enqueue(scheduler, task(record="remaining", phase=2))
    enqueue(scheduler, task(record="retry", retry="technical_once"))
    scheduler.next_task()
    before = scheduler.snapshot()
    assert len(scheduler.discard_subject(first.subject)) == 2
    after = scheduler.snapshot()
    for field in ("seen", "phase_turns", "phase_counts", "dispatched", "turn"):
        assert after[field] == before[field]
    assert not scheduler.enqueue(first, root_branch=True)
    assert scheduler.pending == 0


def test_required_evidence_recheck_runs_while_ordinary_slot_is_scoring():
    scheduler = FrontierScheduler()
    enqueue(scheduler, task(record="new-record"))
    recheck = task(record="disputed-record", retry="evidence_recheck")
    enqueue(scheduler, recheck)
    excluded = {("root", 1, "urn:p")}
    assert scheduler.peek_task(excluded) == recheck
    assert scheduler.next_task(excluded) == recheck
    assert scheduler.next_task(excluded) is None
    assert scheduler.pending == 1


def test_fresh_slot_probe_is_read_only_and_identifies_actual_exploration_due():
    scheduler = FrontierScheduler()
    for number in range(6):
        enqueue(scheduler, task(record=str(number), position=number))
    subject = scheduler.peek_task().subject
    for _ in range(4):
        scheduler.next_task()
    enqueue(scheduler, task(record="required-recheck", retry="evidence_recheck"))
    before = scheduler.snapshot()
    upcoming = scheduler.peek_fresh_slot(subject, "urn:p")
    assert upcoming.record_id == "4"
    assert scheduler.is_exploration_due(upcoming)
    assert scheduler.snapshot() == before
    assert not scheduler.is_exploration_due(scheduler.peek_task())
