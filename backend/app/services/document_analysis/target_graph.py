"""Pure display projection of frozen ontology targets and current evidence.

This module never schedules work or writes facts. Unknown multi-value cardinality
stays incomplete: searched candidate records do not establish a complete list.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from app.schemas.document_analysis import (
    EntityRef,
    GraphArtifactResponse,
    GraphAssertion,
    GraphEntity,
    GraphProperty,
    GraphRelationship,
    GraphRelationshipGroup,
    RevisionRef,
)
from app.schemas.document_target_graph import (
    DocumentTargetGraphResponse,
    GraphTarget,
    TargetCounts,
    TargetCoverage,
    TargetRangeType,
    TargetRoot,
    TargetSummary,
)
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu


def _ref_key(ref: EntityRef | RevisionRef) -> tuple[str, int]:
    return (ref.entity_id if isinstance(ref, EntityRef) else ref.id, ref.revision)


def _scope_id(item) -> str | None:
    return item.scope.scope_id if item.scope is not None and item.scope.members else None


def _assertion_ref(item: GraphAssertion) -> RevisionRef:
    return RevisionRef(id=item.candidate_id, revision=item.revision)


def _member_keys(item: GraphAssertion) -> set[str]:
    if isinstance(item, GraphProperty):
        value = item.normalized_value if item.normalized_value is not None else item.raw_value
        return {json.dumps([value, item.datatype_iri, item.unit], sort_keys=True)}
    if isinstance(item, GraphRelationshipGroup):
        keys = {f"{ref.entity_id}@{ref.revision}" for ref in item.object_refs}
        # Alternatives are one qualified group, not separately affirmed members.
        return keys if item.selection == "all" else {json.dumps(sorted(keys))}
    return {f"{item.object_ref.entity_id}@{item.object_ref.revision}"}


def project_target_graph(
    *,
    graph: GraphArtifactResponse,
    ontology: OntologySnapshot | None,
    document_hash: str,
    root_class_iri: str,
    root_class_label: str,
    filename: str,
    phase: Literal["candidate_graph", "evidence_review", "evidence_verification"] = (
        "evidence_verification"
    ),
) -> DocumentTargetGraphResponse:
    candidate_phase = phase == "candidate_graph"
    review_phase = phase == "evidence_review"
    root_id = stable_id(
        "document-root", [str(graph.recognition_run_id), document_hash, root_class_iri],
    )
    root_ref = (graph.graph_snapshot.root_ref if graph.graph_snapshot else
                EntityRef(entity_id=root_id, revision=1))
    root = TargetRoot(**root_ref.model_dump(), class_iri=root_class_iri, label=filename)
    invalid = {_ref_key(ref) for ref in graph.invalidated_refs}
    entities: dict[tuple[str, int], GraphEntity] = {}
    for entity in graph.entities:
        key = (entity.entity_id, entity.revision)
        if entity.independent_review == "rejected" or key in invalid:
            continue
        refs = [entity.type_decision_ref, entity.referent_decision_ref]
        if entity.grounding_kind == "record":
            refs.append(entity.composition_decision_ref)
        if not candidate_phase and key != _ref_key(root_ref) and (
            not all(refs) or entity.identity_state == "undetermined"
            or not entity.source_selection_refs
            or any(_ref_key(ref) in invalid for ref in refs if ref is not None)
        ):
            continue
        entities[key] = entity
    # The user-selected root exists before recognition; it is not extraction success.
    entities.setdefault(_ref_key(root_ref), GraphEntity(
        **root_ref.model_dump(), class_iri=root_class_iri,
        class_label=root_class_label or root_class_iri, label=filename,
        seed_origin="user_selected", grounding_kind="document_root",
    ))
    assertions = [*graph.relationships, *graph.relationship_groups, *graph.properties]
    if candidate_phase:
        # Registered entities are admitted independently of relationship proofs.
        # Only existing document candidates create entity-to-entity edges.
        assertions = [item for item in assertions if not isinstance(item, GraphProperty)
                      and not item.invalidated and item.independent_review != "rejected"
                      and (item.candidate_id, item.revision) not in invalid
                      and _ref_key(item.subject_ref) in entities
                      and all(_ref_key(ref) in entities for ref in (
                          item.object_refs if isinstance(item, GraphRelationshipGroup)
                          else [item.object_ref]
                      ))]
    assertion_index = {(item.candidate_id, item.revision): item for item in assertions}
    scope_index = {scope.scope_id: scope for scope in graph.scope_resolutions}

    def qualified(item: GraphAssertion, ancestors: frozenset = frozenset()) -> bool:
        key = (item.candidate_id, item.revision)
        if key in ancestors:
            return False
        refs = [item.proof_ref, *item.decision_refs, *item.dependency_refs]
        if review_phase:
            # The evidence review decides whether the original assertion is
            # supported. Normalization/SHACL diagnostics do not veto it, and an
            # unrelated upstream assertion cannot gate this local evidence.
            endpoints = (item.object_refs if isinstance(item, GraphRelationshipGroup)
                         else [item.object_ref] if isinstance(item, GraphRelationship) else [])
            return bool(
                item.decision_status == "supported" and item.proof_ref is not None
                and item.decision_refs and not item.invalidated
                and item.independent_review != "rejected" and key not in invalid
                and _ref_key(item.subject_ref) in entities
                and all(_ref_key(ref) in entities for ref in endpoints)
                and any(item.source_selection_refs.model_dump().values())
                and all(_ref_key(ref) not in invalid for ref in refs if ref is not None)
            )
        if not (
            item.structural_valid and item.model_supported and item.policy_eligible
            and item.proof_ref is not None and item.decision_refs
            and not item.invalidated and item.independent_review != "rejected"
            and key not in invalid and _ref_key(item.subject_ref) in entities
            and any(item.source_selection_refs.model_dump().values())
            and all(_ref_key(ref) not in invalid for ref in refs if ref is not None)
        ):
            return False
        if isinstance(item, GraphRelationship) and _ref_key(item.object_ref) not in entities:
            return False
        if isinstance(item, GraphRelationshipGroup) and (
            item.selection == "undetermined"
            or any(_ref_key(ref) not in entities for ref in item.object_refs)
        ):
            return False
        if graph.extraction_protocol == "ontology-tool-extraction-v1" and (
            item.scope is None or item.modality is None
        ):
            return False
        if item.scope is not None and item.scope.members:
            resolution = scope_index.get(item.scope.scope_id)
            if resolution is None or [
                (step.relation_ref, step.member_ref) for step in resolution.steps
            ] != [(step.relation_ref, step.member_ref) for step in item.scope.members]:
                return False
            for member in item.scope.members:
                parent = assertion_index.get(_ref_key(member.relation_ref))
                # Keep the current tool graph's scope/frontier admission rule:
                # a proved negative is displayable, but cannot grant exploration.
                if (parent is None or parent.polarity != "affirmed"
                        or parent.modality == "unspecified"
                        or not qualified(parent, ancestors | {key})):
                    return False
                endpoints = (parent.object_refs if isinstance(parent, GraphRelationshipGroup)
                             else [parent.object_ref] if isinstance(parent, GraphRelationship)
                             else [])
                if member.member_ref not in endpoints:
                    return False
        return True

    proven = (set() if candidate_phase else {
        key for key, item in assertion_index.items()
        if qualified(item) and item.polarity != "uncertain"
    })
    reached = set(entities) if candidate_phase or review_phase else {_ref_key(root_ref)}
    while True:
        previous = len(reached)
        for item in [*graph.relationships, *graph.relationship_groups]:
            if ((item.candidate_id, item.revision) not in proven
                    or item.polarity != "affirmed" or item.modality == "unspecified"
                    or _ref_key(item.subject_ref) not in reached):
                continue
            objects = (item.object_refs if isinstance(item, GraphRelationshipGroup)
                       else [item.object_ref])
            reached.update(_ref_key(ref) for ref in objects)
        if previous == len(reached):
            break

    by_slot: dict[tuple, list] = defaultdict(list)
    coverage: dict[tuple, list] = defaultdict(list)
    pending_attributes: set[tuple] = set()
    for candidate in graph.attribute_candidates:
        if candidate.status == "pending":
            pending_attributes.update(
                (*_ref_key(option.subject_ref), option.predicate_iri)
                for option in candidate.options
            )
    for item in assertions:
        kind = "property" if isinstance(item, GraphProperty) else "relationship"
        by_slot[(*_ref_key(item.subject_ref), kind, item.predicate_iri)].append(item)
    for item in graph.coverage.subjects:
        coverage[(*_ref_key(item.subject_ref), item.predicate_iri)].append(item)

    targets: list[GraphTarget] = []
    pending_expansion = 0
    if ontology is not None:
        for key, entity in entities.items():
            if entity.class_iri not in ontology.classes:
                pending_expansion += 1
                continue
            menu = compile_local_menu(ontology, SubjectRef(
                **EntityRef(entity_id=key[0], revision=key[1]).model_dump(),
                class_iri=entity.class_iri, is_document_root=key == _ref_key(root_ref),
            ))
            predicates = (menu.relationships if candidate_phase else
                          [*menu.properties, *(menu.relationships if key in reached else [])])
            if key not in reached and menu.relationships:
                pending_expansion += 1
            for spec in predicates:
                candidates = by_slot[(*key, spec.kind, spec.iri)]
                slots = [] if candidate_phase else coverage[(*key, spec.iri)]
                scopes = {_scope_id(item) for item in [*candidates, *slots]} or {None}
                for scope_id in sorted(scopes, key=lambda value: value or ""):
                    scoped = [item for item in candidates if _scope_id(item) == scope_id]
                    if candidate_phase:
                        targets.append(GraphTarget(
                            target_id=stable_id("graph-target", [
                                ontology.snapshot_id, *key, spec.kind, spec.iri, scope_id,
                            ]), subject_ref=EntityRef(entity_id=key[0], revision=key[1]),
                            subject_label=entity.label, subject_class_iri=entity.class_iri,
                            kind="relationship", predicate_iri=spec.iri,
                            predicate_label=spec.label,
                            range_types=[TargetRangeType(iri=iri, label=(
                                ontology.classes[iri].label if iri in ontology.classes else iri
                            )) for iri in spec.range_class_iris],
                            multiplicity=spec.multiplicity,
                            state="undetermined" if scoped else "pending",
                            assertion_refs=[_assertion_ref(item) for item in scoped],
                            scope_id=scope_id,
                            reason=("原文关系候选，尚未核验；虚线不表示关系成立。" if scoped else
                                    "本体允许的关系目标，尚未发现原文关系候选。"),
                        ))
                        continue
                    checked = [item for item in slots if _scope_id(item) == scope_id]
                    counts = {name: sum(getattr(item, name) for item in checked) for name in (
                        "records_planned", "records_examined", "records_incomplete",
                        "records_unattempted", "pending_frontiers",
                    )}
                    scope_checked = bool(counts["records_planned"] and
                                         not counts["records_incomplete"] and
                                         not counts["records_unattempted"] and
                                         not counts["pending_frontiers"])
                    accepted = [item for item in scoped
                                if (item.candidate_id, item.revision) in proven]
                    positive = [item for item in accepted if item.polarity != "negated"]
                    negative = [item for item in accepted if item.polarity == "negated"]
                    members = set().union(*(_member_keys(item) for item in positive))
                    unresolved = [item for item in scoped if item not in accepted
                                  and item.independent_review != "rejected"
                                  and not item.invalidated]
                    pending_value = (spec.kind == "property"
                                     and (*key, spec.iri) in pending_attributes)
                    unresolved_choice = any(
                        isinstance(item, GraphRelationshipGroup) and item.selection != "all"
                        for item in positive
                    )
                    exact_count = (1 if spec.multiplicity == "single" or spec.max_count == 1
                                   else spec.max_count if spec.min_count is not None
                                   and spec.min_count == spec.max_count else None)
                    completed = bool(members and scope_checked and not unresolved and
                                     not pending_value and not unresolved_choice
                                     and not negative and spec.constraint_status == "resolved"
                                     and exact_count is not None and len(members) == exact_count)
                    if completed:
                        state, reason = "supported", "实证通过，当前必要范围已核对。"
                    elif positive:
                        state, reason = "partial", (
                            "已有实证；成员总数未明确，不能据此认定全部识别完成。"
                            if exact_count is None else "已有实证；范围核对或候选冲突仍未完成。"
                        )
                    elif negative:
                        state, reason = "negated", "存在已实证否定陈述，不计为正向关系成立。"
                    elif scoped or pending_value:
                        rejected = bool(scoped) and not pending_value and all(
                            item.independent_review == "rejected" or (review_phase and (
                                item.decision_status == "unsupported" or item.invalidated
                                or (item.candidate_id, item.revision) in invalid
                            )) for item in scoped
                        )
                        state = "rejected" if rejected else "undetermined"
                        reason = ("候选已拒绝。" if rejected else
                                  "存在候选，尚未满足证据与身份核验条件。")
                        if review_phase:
                            reasons = list(dict.fromkeys(
                                item.reason or item.reason_code for item in scoped
                                if item.reason or item.reason_code
                            ))
                            reason = "；".join(reasons) or (
                                "原文候选未采信；可展开查看原因。" if rejected else
                                "原文候选待定，尚未取得有效采信证明。"
                            )
                    elif scope_checked:
                        state, reason = (
                            "not_found", "已检查当前候选范围，未找到证据；不代表全文否定。",
                        )
                    else:
                        state, reason = "pending", "等待识别或范围核对。"
                    ranges = [TargetRangeType(iri=iri, label=(
                        ontology.classes[iri].label if iri in ontology.classes else iri
                    )) for iri in getattr(spec, "range_class_iris", [])]
                    targets.append(GraphTarget(
                        target_id=stable_id("graph-target", [
                            ontology.snapshot_id, *key, spec.kind, spec.iri, scope_id,
                        ]), subject_ref=EntityRef(entity_id=key[0], revision=key[1]),
                        subject_label=entity.label, subject_class_iri=entity.class_iri,
                        kind=spec.kind, predicate_iri=spec.iri, predicate_label=spec.label,
                        range_types=ranges, datatype_iris=getattr(spec, "datatype_iris", []),
                        multiplicity=spec.multiplicity, state=state,
                        supported_count=len(members), negated_count=len(negative),
                        completed=completed, assertion_refs=[_assertion_ref(x) for x in scoped],
                        supported_assertion_refs=[_assertion_ref(x) for x in accepted],
                        coverage=TargetCoverage(**counts, scope_checked=scope_checked),
                        scope_id=scope_id, reason=reason,
                    ))
                    if spec.kind == "relationship" and not positive:
                        pending_expansion += 1

    def summarize(kind: str) -> TargetCounts:
        selected = [item for item in targets if item.kind == kind]
        if candidate_phase:
            return TargetCounts(total=len(selected))
        completed = sum(item.completed for item in selected)
        return TargetCounts(total=len(selected), supported=sum(
            item.supported_count > 0 for item in selected
        ), completed=completed,
            percent=round(completed / len(selected) * 100, 2) if selected else None)

    notes = [
        "分母是冻结本体在当前主体与范围上的识别目标，不表示每个字段在文档中必填。",
        "完整度按完成目标数计算；候选记录覆盖不等于开放文档召回率。",
        "未知多值成员总数保持未完成；待展开分支不计为已完成。",
        "否定、条件、计划及备选关系保留原图标签；虚拟目标不是事实。",
    ]
    if candidate_phase:
        notes = [
            "当前仅发现实体与原文关系候选；关系全部以虚线显示，尚未核验。",
            "本体关系目标不是原文事实，未连接的实体也可选择并展开。",
            "本阶段不识别或校验属性，也不计算关系或属性的实证完整度。",
            "候选的否定、条件、计划及备选成员保留原文标签。",
        ]
    elif review_phase:
        notes = [
            "实线表示原文证据已采信；虚线表示待定候选或尚无证据的本体目标。",
            "未采信候选可切换显示；显示筛选不改变本体关系和属性目标总数。",
            "Schema 语义对齐与原文证据决定采信；规范化结果和 SHACL 告警单独展示。",
            "采信目标数不表示全文信息已穷尽；否定、条件及备选成员保留限定。",
        ]
    if ontology is None:
        notes.append("冻结本体尚不可用，暂不能生成识别目标。")
        pending_expansion = 1
    return DocumentTargetGraphResponse(
        **{name: getattr(graph, name) for name in (
            "recognition_run_id", "run_revision", "event_head", "artifact_revision",
        )}, phase=phase, availability=graph.availability,
        ontology_snapshot_id=ontology.snapshot_id if ontology else None,
        root=root, graph=graph, targets=targets,
        summary=TargetSummary(
            relationships=summarize("relationship"), properties=summarize("property"),
            pending_expansion_count=pending_expansion, notes=notes,
        ),
    )
