"""Frozen logical slots whose task models are created only when selected."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import SubjectRef


@dataclass(slots=True, eq=False)
class LogicalRecord:
    frontier: LogicalFrontier = field(repr=False)
    record_id: str
    phase: int
    section_node_id: str
    source_position: int
    arrival: int
    pending: bool = True
    ranking_epoch_seq: int | None = None
    pool_rank: int | None = None

    @property
    def subject(self):
        return self.frontier.subject

    @property
    def predicate_iri(self):
        return self.frontier.predicate_iri

    @property
    def predicate_kind(self):
        return self.frontier.predicate_kind

    @property
    def hop(self):
        return self.frontier.hop

    @property
    def dependency_hash(self):
        return self.frontier.dependency_hash

    @property
    def retry_kind(self):
        return None

    @property
    def task_id(self):
        return stable_id(
            "recognition-task",
            [self.subject.model_dump(mode="json"), self.predicate_iri, self.record_id,
             self.dependency_hash, None],
        )

    def materialize(self):
        from app.services.extraction.ontology_guided.scheduler import RecognitionTask

        task = RecognitionTask.create(
            subject=self.subject.model_copy(deep=True),
            predicate_iri=self.predicate_iri,
            predicate_kind=self.predicate_kind,
            record_id=self.record_id,
            phase=self.phase,
            hop=self.hop,
            dependency_hash=self.dependency_hash,
            section_node_id=self.section_node_id,
            source_position=self.source_position,
        )
        task.ranking_epoch_seq = self.ranking_epoch_seq
        task.pool_rank = self.pool_rank
        return task


@dataclass(slots=True, eq=False)
class LogicalFrontier:
    subject: SubjectRef
    predicate_iri: str
    predicate_kind: str
    hop: int
    dependency_hash: str
    root_branch: bool
    arrival_start: int
    template_priority: bool = False
    records: list[LogicalRecord] = field(default_factory=list)
    record_ids: set[str] = field(default_factory=set)
    content_hash: str = ""

    @property
    def key(self):
        return (self.subject.entity_id, self.subject.revision,
                self.predicate_iri, self.dependency_hash)

    @property
    def slot_key(self):
        return (self.subject.entity_id, self.subject.revision, self.predicate_iri)

    def freeze(self):
        self.record_ids = {record.record_id for record in self.records}
        if len(self.record_ids) != len(self.records):
            raise ValueError("logical frontier contains duplicate records")
        self.content_hash = evidence_hash(self.frozen_payload())

    def frozen_payload(self):
        return {
            "subject": self.subject.model_dump(mode="json"),
            "predicate_iri": self.predicate_iri,
            "predicate_kind": self.predicate_kind,
            "hop": self.hop,
            "dependency_hash": self.dependency_hash,
            "root_branch": self.root_branch,
            "arrival_start": self.arrival_start,
            "records": [
                [record.record_id, record.phase, record.section_node_id, record.source_position]
                for record in self.records
            ],
        }

    def snapshot(self):
        return {
            **self.frozen_payload(),
            "content_hash": self.content_hash,
            "template_priority": self.template_priority,
            "consumed": [index for index, record in enumerate(self.records) if not record.pending],
            "ranks": [
                [index, record.ranking_epoch_seq, record.pool_rank]
                for index, record in enumerate(self.records)
                if record.ranking_epoch_seq is not None
            ],
        }

    @classmethod
    def from_snapshot(cls, raw):
        frontier = cls(
            subject=SubjectRef.model_validate(raw["subject"]),
            predicate_iri=raw["predicate_iri"],
            predicate_kind=raw["predicate_kind"],
            hop=raw["hop"],
            dependency_hash=raw["dependency_hash"],
            root_branch=raw["root_branch"],
            arrival_start=raw["arrival_start"],
            template_priority=raw.get("template_priority", False),
        )
        for index, values in enumerate(raw["records"]):
            record_id, phase, section_node_id, source_position = values
            if not record_id or phase not in (1, 2) or source_position < 0:
                raise ValueError("invalid logical frontier record")
            frontier.records.append(LogicalRecord(
                frontier, record_id, phase, section_node_id, source_position,
                frontier.arrival_start + index,
            ))
        frontier.freeze()
        if frontier.content_hash != raw["content_hash"]:
            raise ValueError("logical frontier frozen content hash mismatch")
        consumed = raw.get("consumed", [])
        if len(consumed) != len(set(consumed)):
            raise ValueError("duplicate logical frontier consumed positions")
        ranks = raw.get("ranks", [])
        if len(ranks) != len({row[0] for row in ranks}):
            raise ValueError("duplicate logical frontier record ranks")
        for index in [*consumed, *(row[0] for row in ranks)]:
            if not isinstance(index, int) or index < 0 or index >= len(frontier.records):
                raise ValueError("logical frontier position outside frozen records")
        for index in consumed:
            frontier.records[index].pending = False
        for index, epoch, rank in ranks:
            if not isinstance(epoch, int) or epoch < 1 or not isinstance(rank, int) or rank < 1:
                raise ValueError("invalid logical frontier rank")
            frontier.records[index].ranking_epoch_seq = epoch
            frontier.records[index].pool_rank = rank
        return frontier
