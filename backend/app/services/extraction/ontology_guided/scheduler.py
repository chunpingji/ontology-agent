"""Fair instance-frontier scheduler over direct local menus."""

from __future__ import annotations

from collections import deque

from pydantic import Field

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import SubjectRef
from app.services.extraction.ontology_guided.lazy_frontier import LogicalFrontier, LogicalRecord


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
        self, *, max_hops: int = 4, max_tasks: int = 2048, phase_interleaving: bool = True,
        template_interleaving: bool = False,
    ):
        self.max_hops = max_hops
        self.max_tasks = max_tasks
        self.phase_interleaving = phase_interleaving
        self.template_interleaving = template_interleaving
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
        self._logical_frontiers: dict[tuple, LogicalFrontier] = {}
        self._lazy_format = False
        self._next_arrival = 0
        self._template_turns: dict[str, int] = {}
        self._raw_priority_slots: set[tuple] = set()
        self._source_priority_tasks: dict[str, list[dict]] = {}
        self._source_turns: dict[str, int] = {}
        self._branch_buckets: dict[str, dict[tuple, dict[int, object]]] = {
            "root": {}, "child": {},
        }

    def _bucket_key(self, task):
        return (*self._slot_key(task), task.predicate_kind)

    def _index_add(self, task, branch):
        bucket = self._branch_buckets[branch].setdefault(self._bucket_key(task), {})
        bucket[self._arrival_of(task)] = task

    def _index_remove(self, task, branch):
        key = self._bucket_key(task)
        bucket = self._branch_buckets[branch][key]
        del bucket[self._arrival_of(task)]
        if not bucket:
            del self._branch_buckets[branch][key]

    def enqueue_plan(
        self, plan, *, hop: int, dependency_hash: str, source_positions: dict[str, int],
        root_branch: bool = False, template_priority: bool = False,
    ) -> int:
        """Freeze all opportunities without constructing per-record task models.

        The complete retrieval ledger stays with the caller. This logical window
        materializes one selected task at a time, including during restoration.
        Template priority is a scheduling hint admitted by the caller's proof gate.
        """
        if hop < 0 or not dependency_hash:
            raise ValueError("logical frontier requires a valid hop and dependency hash")
        self._lazy_format = True
        key = (plan.subject.entity_id, plan.subject.revision, plan.predicate_iri, dependency_hash)
        if key in self._logical_frontiers:
            return 0
        frontier = LogicalFrontier(
            subject=plan.subject.model_copy(deep=True),
            predicate_iri=plan.predicate_iri,
            predicate_kind=plan.predicate_kind,
            hop=hop,
            dependency_hash=dependency_hash,
            root_branch=root_branch,
            arrival_start=self._next_arrival,
            template_priority=template_priority,
        )
        for position, record in enumerate(plan.records):
            frontier.records.append(LogicalRecord(
                frontier, record.record_id, record.phase, record.section_node_id,
                source_positions[record.record_id], self._next_arrival + position,
            ))
        frontier.freeze()
        self._logical_frontiers[key] = frontier
        self._next_arrival += len(frontier.records)
        queue = self._root if root_branch else self._child
        admitted = 0
        for record in frontier.records:
            if self._dedup_key(record) in self._seen or hop > self.max_hops:
                record.pending = False
                continue
            queue.append(record)
            self._index_add(record, "root" if root_branch else "child")
            admitted += 1
        if hop > self.max_hops and frontier.records:
            self.unexplored_frontier.append({
                "slot": list(frontier.slot_key), "reason": "max_hops", "hop": hop,
                "logical_records": len(frontier.records),
            })
        return admitted

    def prioritize_slot(self, subject: SubjectRef, predicate_iri: str) -> None:
        """Admit an already proved template successor; never infer proof here."""
        key = (subject.entity_id, subject.revision, predicate_iri)
        if self._raw_priority_slots:
            self._raw_priority_slots.add(key)
        for frontier in self._logical_frontiers.values():
            if frontier.slot_key == key:
                frontier.template_priority = True

    def prioritize_source(self, task, sources):
        if task.predicate_kind != "property" or task.subject.is_document_root or not sources:
            raise ValueError("source priority requires a proved local subject and attribute field")
        self._source_priority_tasks[task.task_id] = sources
        self._lazy_format = True

    def _arrival_of(self, task):
        return task.arrival if isinstance(task, LogicalRecord) else self._arrival[task.task_id]

    @staticmethod
    def _materialize(task, *, detach=False):
        if isinstance(task, LogicalRecord):
            return task.materialize()
        return task.model_copy(deep=True) if detach else task

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

    def enqueue(
        self, task: RecognitionTask, *, root_branch: bool = False, template_priority: bool = False,
    ) -> bool:
        if template_priority:
            self._raw_priority_slots.add(self._slot_key(task))
            self._lazy_format = True  # v2 persists fairness turns for admitted tasks too.
        if task.hop > self.max_hops:
            self.unexplored_frontier.append(
                {"task_id": task.task_id, "reason": "max_hops", "hop": task.hop}
            )
            return False
        key = self._dedup_key(task)
        frontier = self._logical_frontiers.get((key[0], key[1], key[2], key[4]))
        if key in self._seen or (
            task.retry_kind is None and frontier and task.record_id in frontier.record_ids
        ):
            return False
        self._seen.add(key)
        self._arrival[task.task_id] = self._next_arrival
        self._next_arrival += 1
        if task.retry_kind:
            self._retries.append(task)
        elif root_branch:
            self._root.append(task)
            self._index_add(task, "root")
        else:
            self._child.append(task)
            self._index_add(task, "child")
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

    def _select_fresh(self, queue, branch, excluded_slots, *, commit=True):
        indexed = ((queue is self._root or queue is self._child)
                   and not self.template_interleaving and not self._source_priority_tasks)
        if indexed:
            # Subject/kind/predicate rotation needs only each bucket's first
            # arrival. Expand records only after choosing the actual slot.
            available = sorted(
                (next(iter(bucket.values()))
                 for key, bucket in self._branch_buckets[branch].items()
                 if key[:3] not in excluded_slots),
                key=self._arrival_of,
            )
        else:
            available = [task for task in queue if self._slot_key(task) not in excluded_slots]
        if self._source_priority_tasks:
            priority = [task for task in available if task.task_id in self._source_priority_tasks]
            ordinary = [task for task in available
                        if task.task_id not in self._source_priority_tasks]
            turn = self._source_turns.get(branch, 0)
            if priority and ordinary:
                available = priority if turn % 3 < 2 else ordinary
            if commit and priority and ordinary:
                self._source_turns[branch] = turn + 1
        if self.template_interleaving:
            def preferred(task):
                return (isinstance(task, LogicalRecord) and task.frontier.template_priority
                        or self._slot_key(task) in self._raw_priority_slots)

            priority = [task for task in available
                        if preferred(task)]
            ordinary = [
                task for task in available if not preferred(task)
            ]
            turn = self._template_turns.get(branch, 0)
            if priority and ordinary:
                available = priority if turn % 3 < 2 else ordinary
            if commit:
                self._template_turns[branch] = turn + 1
        subject = self._rotate(
            [task.subject.entity_id for task in available], self._subject_turns.get(branch)
        )
        if commit:
            self._subject_turns[branch] = subject
        candidates = [task for task in available if task.subject.entity_id == subject]
        kind = self._rotate(
            [task.predicate_kind for task in candidates], self._kind_turns.get(subject)
        )
        if commit:
            self._kind_turns[subject] = kind
        candidates = [task for task in candidates if task.predicate_kind == kind]
        kind_key = repr((subject, kind))
        predicate = self._rotate(
            [task.predicate_iri for task in candidates], self._predicate_turns.get(kind_key)
        )
        if commit:
            self._predicate_turns[kind_key] = predicate
        candidates = [task for task in candidates if task.predicate_iri == predicate]
        if indexed:
            candidates = sorted(
                (task for head in candidates
                 for task in self._branch_buckets[branch][self._bucket_key(head)].values()),
                key=self._arrival_of,
            )
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
        if commit:
            self._phase_turns[slot_key] = turns + 1
        candidates = [task for task in candidates if task.phase == phase]
        phase_key = repr((subject, predicate, phase))
        count = self._phase_counts.get(phase_key, 0)
        if commit:
            self._phase_counts[phase_key] = count + 1
        if count % 5 == 4:
            # Exploration uses frozen source order, never the latest pool ranks.
            task = min(
                candidates,
                key=lambda item: (
                    item.source_position,
                    self._arrival_of(item),
                    item.record_id,
                ),
            )
        else:
            section = self._rotate(
                [task.section_node_id for task in candidates],
                self._section_turns.get(phase_key),
            )
            if commit:
                self._section_turns[phase_key] = section
            candidates = [task for task in candidates if task.section_node_id == section]
            task = min(
                candidates,
                key=lambda item: (
                    item.ranking_epoch_seq if item.ranking_epoch_seq is not None else float("inf"),
                    item.pool_rank if item.pool_rank is not None else float("inf"),
                    self._arrival_of(item),
                    item.record_id,
                ),
            )
        if commit:
            queue.remove(task)
            self._index_remove(task, branch)
            if isinstance(task, LogicalRecord):
                task.pending = False
        return task

    def peek_task(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> RecognitionTask | None:
        """Inspect the next opportunity without advancing any scheduler counter."""
        if self.dispatched >= self.max_tasks:
            return None
        task = self._choose_task(excluded_slots or set(), commit=False)
        return self._materialize(task, detach=True) if task is not None else None

    def peek_slot(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> tuple[SubjectRef, str] | None:
        if self.dispatched >= self.max_tasks:
            return None
        task = self._choose_task(excluded_slots or set(), commit=False)
        return (task.subject.model_copy(deep=True), task.predicate_iri) if task else None

    def peek_fresh_slot(self, subject: SubjectRef, predicate_iri: str) -> RecognitionTask | None:
        """Inspect one slot's next phase/section opportunity, excluding continuations."""
        if self.dispatched >= self.max_tasks:
            return None
        key = (subject.entity_id, subject.revision, predicate_iri)
        queue = deque(sorted(
            (task for buckets in self._branch_buckets.values()
             for bucket_key, bucket in buckets.items() if bucket_key[:3] == key
             for task in bucket.values()),
            key=self._arrival_of,
        ))
        if not queue:
            return None
        task = self._select_fresh(
            queue, "root" if subject.is_document_root else "child", set(), commit=False,
        )
        return self._materialize(task, detach=True)

    def is_exploration_due(self, task: RecognitionTask) -> bool:
        if task.retry_kind:
            return False
        key = repr((task.subject.entity_id, task.predicate_iri, task.phase))
        return self._phase_counts.get(key, 0) % 5 == 4

    def discard_subject(self, subject: SubjectRef) -> list[RecognitionTask | LogicalRecord]:
        """Remove stale obligations without resetting lineage fairness or budgets."""
        discarded = []
        for queue in (self._root, self._child, self._retries):
            for task in list(queue):
                if (task.subject.entity_id, task.subject.revision) == (
                    subject.entity_id,
                    subject.revision,
                ):
                    queue.remove(task)
                    if task.retry_kind is None:
                        self._index_remove(task, "root" if queue is self._root else "child")
                    if isinstance(task, LogicalRecord):
                        task.pending = False
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
                    if isinstance(task, LogicalRecord):
                        task.ranking_epoch_seq = sequence
                        task.pool_rank = ranks[task.record_id]
                    else:
                        queue[position] = task.model_copy(
                            update={
                                "ranking_epoch_seq": sequence,
                                "pool_rank": ranks[task.record_id],
                            }
                        )
                        self._index_add(
                            queue[position], "root" if queue is self._root else "child",
                        )

    def next_task(
        self, excluded_slots: set[tuple[str, int, str]] | None = None
    ) -> RecognitionTask | None:
        if self.dispatched >= self.max_tasks:
            blocked = {}
            for queue in (self._root, self._child, self._retries):
                for task in queue:
                    if isinstance(task, LogicalRecord):
                        key = task.frontier.slot_key
                        blocked[key] = blocked.get(key, 0) + 1
                        task.pending = False
                    else:
                        self.unexplored_frontier.append({
                            "task_id": task.task_id, "reason": "max_tasks",
                        })
                queue.clear()
            for buckets in self._branch_buckets.values():
                buckets.clear()
            self.unexplored_frontier.extend(
                {"slot": list(key), "reason": "max_tasks", "logical_records": count}
                for key, count in blocked.items()
            )
            return None
        task = self._choose_task(excluded_slots or set(), commit=True)
        return self._materialize(task) if task is not None else None

    def _choose_task(self, excluded_slots, *, commit):
        task = None
        root_available = any(key[:3] not in excluded_slots for key in self._branch_buckets["root"])
        child_available = any(
            key[:3] not in excluded_slots for key in self._branch_buckets["child"]
        )
        # Required evidence rechecks and bounded continuations are not ordinary
        # retrieval opportunities; scoring a fresh pool cannot block them.
        retry = self._retries[0] if self._retries else None
        if retry and (self._fresh_since_retry or not (root_available or child_available)):
            task = retry
            if commit:
                self._retries.remove(task)
                self._fresh_since_retry = False
        elif child_available and (self._turn % 3 in (0, 1) or not root_available):
            task = self._select_fresh(self._child, "child", excluded_slots, commit=commit)
        elif root_available:
            task = self._select_fresh(self._root, "root", excluded_slots, commit=commit)
        elif child_available:
            task = self._select_fresh(self._child, "child", excluded_slots, commit=commit)
        if task is not None and commit:
            if task.retry_kind is None:
                self._turn += 1
                self._fresh_since_retry = True
            self.dispatched += 1
        return task

    @property
    def pending(self) -> int:
        return len(self._root) + len(self._child) + len(self._retries)

    @property
    def pending_slots(self) -> set[tuple[str, int, str]]:
        return {key[:3] for buckets in self._branch_buckets.values() for key in buckets} | {
            self._slot_key(task) for task in self._retries
        }

    def snapshot(self) -> dict:
        payload = {
            "root": [item.model_dump(mode="json") for item in self._root
                     if not isinstance(item, LogicalRecord)],
            "child": [item.model_dump(mode="json") for item in self._child
                      if not isinstance(item, LogicalRecord)],
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
        if self._lazy_format:
            payload.update({
                "schema_version": 2,
                "logical_frontiers": [value.snapshot()
                                      for value in self._logical_frontiers.values()],
                "next_arrival": self._next_arrival,
                "template_interleaving": self.template_interleaving,
                "template_turns": dict(self._template_turns),
                **({"raw_priority_slots": [list(key) for key in sorted(self._raw_priority_slots)]}
                   if self._raw_priority_slots else {}),
                **({"source_priority_tasks": self._source_priority_tasks,
                    "source_turns": dict(self._source_turns)}
                   if self._source_priority_tasks else {}),
            })
        return payload

    @classmethod
    def from_snapshot(cls, raw: dict, *, max_hops=4, max_tasks=2048):
        version = raw.get("schema_version", 1)
        if version not in (1, 2):
            raise ValueError("unsupported scheduler snapshot version")
        scheduler = cls(
            max_hops=max_hops,
            max_tasks=max_tasks,
            phase_interleaving=raw.get("phase_interleaving", True),
            template_interleaving=raw.get("template_interleaving", False),
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
        scheduler._next_arrival = max(scheduler._arrival.values(), default=-1) + 1
        if version == 2:
            scheduler._lazy_format = True
            scheduler._next_arrival = raw["next_arrival"]
            scheduler._template_turns = dict(raw.get("template_turns", {}))
            scheduler._source_priority_tasks = dict(raw.get("source_priority_tasks", {}))
            scheduler._source_turns = dict(raw.get("source_turns", {}))
            scheduler._raw_priority_slots = {
                tuple(key) for key in raw.get("raw_priority_slots", [])
            }
            for item in raw["logical_frontiers"]:
                frontier = LogicalFrontier.from_snapshot(item)
                if frontier.key in scheduler._logical_frontiers:
                    raise ValueError("duplicate logical frontier slot")
                scheduler._logical_frontiers[frontier.key] = frontier
                queue = scheduler._root if frontier.root_branch else scheduler._child
                queue.extend(record for record in frontier.records if record.pending)
            for name in ("root", "child"):
                queue = getattr(scheduler, f"_{name}")
                setattr(scheduler, f"_{name}", deque(sorted(queue, key=scheduler._arrival_of)))
            all_arrivals = [*scheduler._arrival.values(),
                            *(record.arrival
                              for frontier in scheduler._logical_frontiers.values()
                              for record in frontier.records)]
            if (len(all_arrivals) != len(set(all_arrivals)) or any(
                value < 0 or value >= scheduler._next_arrival for value in all_arrivals
            )):
                raise ValueError("invalid logical frontier arrival cursor")
        for branch in ("root", "child"):
            for task in getattr(scheduler, f"_{branch}"):
                scheduler._index_add(task, branch)
        return scheduler
