"""Fair instance-frontier scheduler over direct local menus."""

from __future__ import annotations

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
        )


class FrontierScheduler:
    """Two child turns followed by one root turn, with retry yielding."""

    def __init__(self, *, max_hops: int = 4, max_tasks: int = 2048):
        self.max_hops = max_hops
        self.max_tasks = max_tasks
        self._root: deque[RecognitionTask] = deque()
        self._child: deque[RecognitionTask] = deque()
        self._retries: deque[RecognitionTask] = deque()
        self._seen: set[tuple] = set()
        self._turn = 0
        self.dispatched = 0
        self.unexplored_frontier: list[dict] = []

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
        if task.retry_kind:
            self._retries.append(task)
        elif root_branch:
            self._root.append(task)
        else:
            self._child.append(task)
        return True

    def next_task(self) -> RecognitionTask | None:
        if self.dispatched >= self.max_tasks:
            for queue in (self._root, self._child, self._retries):
                self.unexplored_frontier.extend(
                    {"task_id": task.task_id, "reason": "max_tasks"} for task in queue
                )
                queue.clear()
            return None
        # Retrying cannot starve an unattempted record.  Dispatch a retry only
        # when both fresh queues are empty.
        task = None
        if self._child and (self._turn % 3 in (0, 1) or not self._root):
            task = self._child.popleft()
        elif self._root:
            task = self._root.popleft()
        elif self._child:
            task = self._child.popleft()
        elif self._retries:
            task = self._retries.popleft()
        if task is not None:
            self._turn += 1
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
        }
