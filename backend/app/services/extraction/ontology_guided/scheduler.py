"""Fair instance-frontier scheduler over direct local menus."""

from __future__ import annotations

import copy
from collections import deque

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import SubjectRef


class RecognitionTask(EvidenceModel):
    task_id: str = Field(min_length=1)
    claim_lineage_id: str = Field(min_length=1)
    subject: SubjectRef
    predicate_iri: str = Field(min_length=1)
    predicate_kind: str
    record_id: str = Field(min_length=1)
    phase: int = Field(ge=1, le=2)
    hop: int = Field(ge=0)
    dependency_hash: str = Field(min_length=1)
    retry_kind: str | None = None
    section_node_id: str = ""
    source_position: int = Field(default=0, ge=0)
    ranking_epoch_seq: int | None = Field(default=None, ge=1)
    pool_rank: int | None = Field(default=None, ge=1)

    @classmethod
    def create(
        cls,
        *,
        subject: SubjectRef,
        predicate_iri: str,
        predicate_kind: str,
        record_id: str,
        phase: int,
        hop: int,
        dependency_hash: str,
        claim_lineage_id: str | None = None,
        retry_kind: str | None = None,
        section_node_id: str = "",
        source_position: int = 0,
    ) -> "RecognitionTask":
        lineage = claim_lineage_id or stable_id(
            "claim-lineage",
            [subject.entity_id, predicate_iri, record_id],
        )
        identity = [
            subject.model_dump(mode="json"),
            predicate_iri,
            record_id,
            dependency_hash,
            retry_kind,
        ]
        return cls(
            task_id=stable_id("recognition-task", identity),
            claim_lineage_id=lineage,
            subject=subject,
            predicate_iri=predicate_iri,
            predicate_kind=predicate_kind,
            record_id=record_id,
            phase=phase,
            hop=hop,
            dependency_hash=dependency_hash,
            retry_kind=retry_kind,
            section_node_id=section_node_id,
            source_position=source_position,
        )


class FrontierScheduler:
    """Bounded branch/subject/kind/predicate/phase/section opportunity rotation."""

    def __init__(
        self, *, max_hops: int = 4, max_tasks: int = 2048, phase_interleaving: bool = True
    ):
        self.max_hops = max_hops
        self.max_tasks = max_tasks
        self.phase_interleaving = phase_interleaving
        self._root: deque[RecognitionTask] = deque()
        self._child: deque[RecognitionTask] = deque()
        self._retries: deque[RecognitionTask] = deque()
        self._seen: set[tuple] = set()
        self._turn = 0
        self.dispatched = 0
        self.unexplored_frontier: list[dict] = []
        self._subject_turns: dict[str, str] = {}
        self._kind_turns: dict[str, str] = {}
        self._predicate_turns: dict[str, str] = {}
        self._section_turns: dict[str, str] = {}
        self._phase_turns: dict[str, int] = {}
        self._phase_counts: dict[str, int] = {}
        self._arrival: dict[str, int] = {}
        self._fresh_since_retry = False
        self._ranking_sequence = 0

    @staticmethod
    def _dedup_key(task: RecognitionTask) -> tuple:
        return (
            task.subject.entity_id,
            task.subject.revision,
            task.predicate_iri,
            task.record_id,
            task.dependency_hash,
            task.retry_kind,
        )

    def enqueue(self, task: RecognitionTask, *, root_branch: bool = False) -> bool:
        if task.hop > self.max_hops:
            self.unexplored_frontier.append(
                {"task_id": task.task_id, "reason": "max_hops", "hop": task.hop}
            )
            return False
        key = self._dedup_key(task)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._arrival[task.task_id] = len(self._arrival)
        if task.retry_kind:
            self._retries.append(task)
        elif root_branch:
            self._root.append(task)
        else:
            self._child.append(task)
        return True

    @staticmethod
    def _rotate(values, previous):
        ordered = list(dict.fromkeys(values))
        if previous in ordered:
            return ordered[(ordered.index(previous) + 1) % len(ordered)]
        return ordered[0]

    @staticmethod
    def _slot_key(task):
        return (task.subject.entity_id, task.subject.revision, task.predicate_iri)

    def _select_fresh(self, queue, branch, excluded_slots):
        available = [task for task in queue if self._slot_key(task) not in excluded_slots]
        subject = self._rotate(
            [task.subject.entity_id for task in available], self._subject_turns.get(branch)
        )
        self._subject_turns[branch] = subject
        candidates = [task for task in available if task.subject.entity_id == subject]
        kind = self._rotate(
            [task.predicate_kind for task in candidates], self._kind_turns.get(subject)
        )
        self._kind_turns[subject] = kind
        candidates = [task for task in candidates if task.predicate_kind == kind]
        kind_key = repr((subject, kind))
        predicate = self._rotate(
            [task.predicate_iri for task in candidates], self._predicate_turns.get(kind_key)
        )
        self._predicate_turns[kind_key] = predicate
        candidates = [task for task in candidates if task.predicate_iri == predicate]
        slot_key = repr((subject, predicate))
        turns = self._phase_turns.get(slot_key, 0)
        # Both first opportunities are fixed; subsequent opportunities follow 4:1.
        desired = 1 if turns == 0 else 2 if turns == 1 else 2 if (turns - 2) % 5 == 4 else 1
        phases = {task.phase for task in candidates}
        phase = (
            (desired if desired in phases else min(phases))
            if self.phase_interleaving
            else (min(phases))
        )
        self._phase_turns[slot_key] = turns + 1
        candidates = [task for task in candidates if task.phase == phase]
        phase_key = repr((subject, predicate, phase))
        count = self._phase_counts.get(phase_key, 0)
        self._phase_counts[phase_key] = count + 1
        if count % 5 == 4:
            # Exploration uses frozen source order, never the latest pool ranks.
            task = min(
                candidates,
                key=lambda item: (
                    item.source_position,
                    self._arrival[item.task_id],
                    item.record_id,
                ),
            )
        else:
            section = self._rotate(
                [task.section_node_id for task in candidates],
                self._section_turns.get(phase_key),
            )
            self._section_turns[phase_key] = section
            candidates = [task for task in candidates if task.section_node_id == section]
            task = min(
                candidates,
                key=lambda item: (
                    item.ranking_epoch_seq if item.ranking_epoch_seq is not None else float("inf"),
                    item.pool_rank if item.pool_rank is not None else float("inf"),
                    self._arrival[item.task_id],
                    item.record_id,
                ),
            )
        queue.remove(task)
        return task

    def peek_task(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> RecognitionTask | None:
        """Inspect the next opportunity without advancing any scheduler counter."""
        return copy.deepcopy(self).next_task(excluded_slots=excluded_slots)

    def peek_slot(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> tuple[SubjectRef, str] | None:
        task = self.peek_task(excluded_slots=excluded_slots)
        return (task.subject, task.predicate_iri) if task else None

    def peek_fresh_slot(self, subject: SubjectRef, predicate_iri: str) -> RecognitionTask | None:
        """Inspect one slot's next phase/section opportunity, excluding continuations."""
        if self.dispatched >= self.max_tasks:
            return None
        key = (subject.entity_id, subject.revision, predicate_iri)
        queue = deque(task for task in (*self._root, *self._child) if self._slot_key(task) == key)
        if not queue:
            return None
        probe = copy.copy(self)
        for name in (
            "subject_turns",
            "kind_turns",
            "predicate_turns",
            "section_turns",
            "phase_turns",
            "phase_counts",
        ):
            setattr(probe, f"_{name}", dict(getattr(self, f"_{name}")))
        return probe._select_fresh(queue, "root" if subject.is_document_root else "child", set())

    def is_exploration_due(self, task: RecognitionTask) -> bool:
        if task.retry_kind:
            return False
        key = repr((task.subject.entity_id, task.predicate_iri, task.phase))
        return self._phase_counts.get(key, 0) % 5 == 4

    def discard_subject(self, subject: SubjectRef) -> list[RecognitionTask]:
        """Remove stale obligations without resetting lineage fairness or budgets."""
        discarded = []
        for queue in (self._root, self._child, self._retries):
            for task in list(queue):
                if (task.subject.entity_id, task.subject.revision) == (
                    subject.entity_id,
                    subject.revision,
                ):
                    queue.remove(task)
                    discarded.append(task)
        return discarded

    def reorder_slot(
        self,
        subject: SubjectRef,
        predicate_iri: str,
        record_ids: list[str],
        *,
        epoch_seq: int | None = None,
    ) -> None:
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("ranking order contains duplicate records")
        self._ranking_sequence = max(self._ranking_sequence + 1, epoch_seq or 0)
        sequence = epoch_seq or self._ranking_sequence
        ranks = {rid: rank for rank, rid in enumerate(record_ids, 1)}
        for queue in (self._root, self._child):
            for position, task in enumerate(queue):
                if (
                    task.subject == subject
                    and task.predicate_iri == predicate_iri
                    and task.record_id in ranks
                ):
                    if task.ranking_epoch_seq is not None:
                        if (
                            task.ranking_epoch_seq != sequence
                            or task.pool_rank != ranks[task.record_id]
                        ):
                            raise ValueError("committed record rank cannot be overwritten")
                        continue
                    queue[position] = task.model_copy(
                        update={
                            "ranking_epoch_seq": sequence,
                            "pool_rank": ranks[task.record_id],
                        }
                    )

    def next_task(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> RecognitionTask | None:
        if self.dispatched >= self.max_tasks:
            for queue in (self._root, self._child, self._retries):
                self.unexplored_frontier.extend(
                    {"task_id": task.task_id, "reason": "max_tasks"} for task in queue
                )
                queue.clear()
            return None
        task = None
        excluded_slots = excluded_slots or set()
        root_available = any(self._slot_key(item) not in excluded_slots for item in self._root)
        child_available = any(self._slot_key(item) not in excluded_slots for item in self._child)
        # Required evidence rechecks and bounded continuations are not ordinary
        # retrieval opportunities; scoring a fresh pool cannot block them.
        retry = self._retries[0] if self._retries else None
        if retry and (self._fresh_since_retry or not (root_available or child_available)):
            task = retry
            self._retries.remove(task)
            self._fresh_since_retry = False
        elif child_available and (self._turn % 3 in (0, 1) or not root_available):
            task = self._select_fresh(self._child, "child", excluded_slots)
        elif root_available:
            task = self._select_fresh(self._root, "root", excluded_slots)
        elif child_available:
            task = self._select_fresh(self._child, "child", excluded_slots)
        if task is not None:
            if task.retry_kind is None:
                self._turn += 1
                self._fresh_since_retry = True
            self.dispatched += 1
        return task

    @property
    def pending(self) -> int:
        return len(self._root) + len(self._child) + len(self._retries)

    def snapshot(self) -> dict:
        return {
            "root": [item.model_dump(mode="json") for item in self._root],
            "child": [item.model_dump(mode="json") for item in self._child],
            "retries": [item.model_dump(mode="json") for item in self._retries],
            "seen": [list(item) for item in sorted(self._seen, key=repr)],
            "turn": self._turn,
            "dispatched": self.dispatched,
            "unexplored_frontier": list(self.unexplored_frontier),
            "subject_turns": dict(self._subject_turns),
            "kind_turns": dict(self._kind_turns),
            "predicate_turns": dict(self._predicate_turns),
            "section_turns": dict(self._section_turns),
            "phase_turns": dict(self._phase_turns),
            "phase_counts": dict(self._phase_counts),
            "arrival": dict(self._arrival),
            "fresh_since_retry": self._fresh_since_retry,
            "ranking_sequence": self._ranking_sequence,
            "phase_interleaving": self.phase_interleaving,
        }

    @classmethod
    def from_snapshot(cls, raw: dict, *, max_hops=4, max_tasks=2048):
        scheduler = cls(
            max_hops=max_hops,
            max_tasks=max_tasks,
            phase_interleaving=raw.get("phase_interleaving", True),
        )
        for name in ("root", "child", "retries"):
            setattr(
                scheduler,
                f"_{name}",
                deque(RecognitionTask.model_validate(item) for item in raw[name]),
            )
        scheduler._seen = {tuple(item) for item in raw["seen"]}
        scheduler._turn = raw["turn"]
        scheduler.dispatched = raw["dispatched"]
        scheduler.unexplored_frontier = list(raw["unexplored_frontier"])
        for name in (
            "subject_turns",
            "kind_turns",
            "predicate_turns",
            "section_turns",
            "phase_turns",
            "phase_counts",
            "arrival",
        ):
            setattr(scheduler, f"_{name}", dict(raw.get(name, {})))
        scheduler._fresh_since_retry = raw.get("fresh_since_retry", False)
        scheduler._ranking_sequence = raw.get("ranking_sequence", 0)
        return scheduler
