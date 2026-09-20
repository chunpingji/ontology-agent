"""Pure work-unit contracts: shared transport never expands member permissions."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    SchemaCard,
    VerificationEnvelope,
    VerificationTargetSpec,
    compile_stage_schema,
)
from app.services.extraction.ontology_guided.context import TaskContext
from app.services.extraction.ontology_guided.scheduler import RecognitionTask


class RecognitionBatchPolicy(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["predicate-batch-v1"] = "predicate-batch-v1"
    max_members: int = Field(default=4, ge=1, le=4)


def compatible_members(
    seed: RecognitionTask, candidate: RecognitionTask, *,
    readiness: Mapping[str, bool] | Callable[[RecognitionTask], bool] | None = None,
) -> bool:
    """Dependency validity is checked per member; predicate hashes need not match."""
    if readiness is not None:
        ready = (readiness if callable(readiness)
                 else lambda task: readiness.get(task.task_id, False))
        if not ready(seed) or not ready(candidate):
            return False
    return (
        seed.task_id != candidate.task_id
        and seed.predicate_iri != candidate.predicate_iri
        and seed.subject == candidate.subject
        and seed.record_id == candidate.record_id
        and seed.scope == candidate.scope
        and seed.retry_kind is None
        and candidate.retry_kind is None
    )


class RecognitionWorkUnit(EvidenceModel):
    work_unit_id: str = Field(min_length=1)
    members: list[RecognitionTask] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def compatible_unique_members(self):
        seed = self.members[0]
        if len({member.task_id for member in self.members}) != len(self.members):
            raise ValueError("work_unit_duplicate_task")
        if len({member.predicate_iri for member in self.members}) != len(self.members):
            raise ValueError("work_unit_duplicate_predicate")
        if any(not compatible_members(seed, member) for member in self.members[1:]):
            raise ValueError("work_unit_incompatible_member")
        return self

    @classmethod
    def create(
        cls, *, run_fingerprint: str, policy: RecognitionBatchPolicy,
        members: list[RecognitionTask],
    ) -> RecognitionWorkUnit:
        if not run_fingerprint or not members or len(members) > policy.max_members:
            raise ValueError("work_unit_invalid_identity_or_capacity")
        return cls(
            work_unit_id=stable_id("recognition-work-unit", {
                "run_fingerprint": run_fingerprint,
                "policy": policy.model_dump(mode="json"),
                "members": [[member.task_id, member.dependency_hash] for member in members],
            }),
            members=[member.model_copy(deep=True) for member in members],
        )


class MemberContext(EvidenceModel):
    task_id: str = Field(min_length=1)
    context: TaskContext
    card: SchemaCard

    @model_validator(mode="after")
    def exact_member_identity(self):
        target = self.context.target
        if (
            self.task_id != target.task_id
            or self.card.subject_ref.id != target.subject_ref.entity_id
            or self.card.subject_ref.revision != target.subject_ref.revision
            or len(self.card.predicates) != 1
            or self.card.predicates[0].iri != target.predicate_iri
        ):
            raise ValueError("batch_member_context_identity_mismatch")
        return self


class RecognitionBatchContext(EvidenceModel):
    work_unit_id: str = Field(min_length=1)
    members: list[MemberContext] = Field(min_length=1, max_length=4)
    protocol_state: dict = Field(default_factory=dict, exclude=True)
    protocol_results: dict = Field(default_factory=dict, exclude=True)
    remaining_model_calls_by_member: dict[str, int] = Field(default_factory=dict, exclude=True)
    _before_model_call: Callable[[str, int], None] | None = PrivateAttr(default=None)
    _protocol_hook: Callable[[dict], None] | None = PrivateAttr(default=None)
    _actual_calls: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def unique_members(self):
        if len({member.task_id for member in self.members}) != len(self.members):
            raise ValueError("batch_context_duplicate_member")
        if any(value < 0 for value in self.remaining_model_calls_by_member.values()):
            raise ValueError("batch_context_negative_budget")
        return self

    def bind_protocol_hook(self, callback: Callable[[dict], None]) -> None:
        self._protocol_hook = callback

    def save_protocol(self, state: dict, *, result_changes: dict | None = None) -> None:
        if self._protocol_hook is not None:
            self._protocol_hook({**state, "result_changes": result_changes}
                                if result_changes else state)
        self.protocol_state = state

    @property
    def before_model_call(self) -> Callable[[str, int], None] | None:
        return self._before_model_call

    def bind_model_call_hook(self, callback: Callable[[str, int], None]) -> None:
        self._before_model_call = callback


class MemberDiscoveryAnswer(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str
    result: DiscoveryEnvelope


class BatchDiscoveryEnvelope(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    members: list[MemberDiscoveryAnswer]


class MemberVerificationAnswer(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str
    result: VerificationEnvelope


class BatchVerificationEnvelope(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    members: list[MemberVerificationAnswer]


@dataclass(frozen=True)
class MemberStageResults:
    answers: dict[str, DiscoveryEnvelope | VerificationEnvelope]
    errors: dict[str, str]
    feedback: dict[str, dict] = field(default_factory=dict)


def answer_validation_feedback(previous_answer, error):
    """Keep exact member output and actionable errors, without sibling reasoning."""
    cause = error.__cause__ if error.__cause__ is not None else error
    if isinstance(cause, ValidationError):
        issues = [
            {"field_path": ".".join(map(str, item["loc"])),
             "reason_code": item["type"], "message": item["msg"]}
            for item in cause.errors(include_input=False, include_url=False)
        ]
    else:
        issues = [{"field_path": "", "reason_code": str(cause), "message": str(cause)}]
    return {"previous_answer": previous_answer, "issues": issues}


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("batch_answer_duplicate_json_key")
        value[key] = item
    return value


def _parse_batch(raw, *, member_task_ids: Sequence[str], response_type) -> MemberStageResults:
    expected = set(member_task_ids)
    if not expected or len(expected) != len(member_task_ids):
        raise ValueError("batch_expected_members_invalid")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (ValueError, TypeError) as error:
            raise ValueError("batch_answer_invalid") from error
    if (not isinstance(raw, dict) or set(raw) != {"members"}
            or not isinstance(raw["members"], list)):
        raise ValueError("batch_answer_invalid")
    located = {}
    for item in raw["members"]:
        if not isinstance(item, dict) or not isinstance(item.get("task_id"), str):
            raise ValueError("batch_answer_invalid")
        task_id = item["task_id"]
        if task_id not in expected:
            raise ValueError("batch_member_unknown")
        if task_id in located:
            raise ValueError("batch_member_duplicate")
        located[task_id] = item
    answers, errors, feedback = {}, {}, {}
    for task_id in member_task_ids:
        item = located.get(task_id)
        if item is None:
            errors[task_id] = "member_answer_missing"
            feedback[task_id] = answer_validation_feedback(None, ValueError(errors[task_id]))
            continue
        try:
            if set(item) != {"task_id", "result"}:
                raise ValueError("member_answer_invalid")
            answers[task_id] = response_type.model_validate(item["result"], strict=True)
        except (ValidationError, ValueError, TypeError) as exc:
            errors[task_id] = "member_answer_invalid"
            feedback[task_id] = answer_validation_feedback(item.get("result"), exc)
    return MemberStageResults(answers, errors, feedback)


def parse_batch_discovery(raw, *, member_task_ids: Sequence[str]) -> MemberStageResults:
    return _parse_batch(raw, member_task_ids=member_task_ids, response_type=DiscoveryEnvelope)


def parse_batch_verification(raw, *, member_task_ids: Sequence[str]) -> MemberStageResults:
    return _parse_batch(raw, member_task_ids=member_task_ids, response_type=VerificationEnvelope)


def compile_batch_stage_schema(
    stage: Literal["discovery", "verification"], member_inputs: Sequence[MemberContext],
    targets_by_member: Mapping[str, list[VerificationTargetSpec]] | None = None, *,
    reference_resolution: bool = False,
    relation_bridges_by_member: Mapping[str, list] | None = None,
) -> dict:
    """Reuse one atomic schema; member-specific authority stays in freeze/verification."""
    members = list(member_inputs)
    if not members or len({member.task_id for member in members}) != len(members):
        raise ValueError("batch_schema_members_invalid")
    card = members[0].card.model_copy(update={
        "class_iris": sorted({iri for member in members for iri in member.card.class_iris}),
        "predicates": [predicate for member in members for predicate in member.card.predicates],
        "identity_keys": [key for member in members for key in member.card.identity_keys],
    })
    bridges = None if relation_bridges_by_member is None else list(dict.fromkeys(
        bridge for member in members
        for bridge in relation_bridges_by_member.get(member.task_id, [])
    ))
    schema = compile_stage_schema(
        stage, card=card,
        evidence_ids=[fragment.anchor.evidence_id for member in members
                      for fragment in member.context.fragments],
        targets=[target for member in members
                 for target in (targets_by_member or {}).get(member.task_id, [])],
        reference_resolution=reference_resolution, relation_bridges=bridges,
    )
    definitions = schema.pop("$defs")
    name = "BatchMemberResult"
    definitions[name] = schema
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"members": {
            "type": "array", "minItems": len(members), "maxItems": len(members),
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string", "enum": [m.task_id for m in members]},
                    "result": {"$ref": f"#/$defs/{name}"},
                },
                "required": ["task_id", "result"],
            },
        }},
        "required": ["members"], "$defs": definitions,
    }


def pack_work_unit(
    candidates: Sequence[RecognitionTask], member_inputs: Mapping[str, MemberContext], *,
    policy: RecognitionBatchPolicy, measure_request: Callable[[list[MemberContext]], bool],
    readiness: Mapping[str, bool] | Callable[[RecognitionTask], bool] | None = None,
) -> list[str]:
    """Try complete members in stable order using the caller's full wire measurement.

    A false measurement excludes a candidate without consuming it. If the seed
    does not fit, no sibling silently replaces its scheduling opportunity.
    """
    if not candidates:
        return []
    seed = candidates[0]
    selected = []
    predicates = set()
    for candidate in candidates:
        if len(selected) >= policy.max_members:
            break
        if candidate.task_id in selected:
            continue
        member = member_inputs.get(candidate.task_id)
        ready = (readiness(candidate) if callable(readiness)
                 else readiness.get(candidate.task_id, False) if readiness is not None else True)
        if (member is None or not ready or member.context.budget_status != "within_budget"
                or member.context.omitted_refs or member.context.remaining_model_calls == 0):
            if not selected:
                return []
            continue
        if candidate.task_id != seed.task_id and (
            candidate.predicate_iri in predicates
            or not compatible_members(seed, candidate, readiness=readiness)
        ):
            continue
        if measure_request([member_inputs[task_id] for task_id in selected] + [member]):
            selected.append(candidate.task_id)
            predicates.add(candidate.predicate_iri)
        elif not selected:
            return []
    return selected
