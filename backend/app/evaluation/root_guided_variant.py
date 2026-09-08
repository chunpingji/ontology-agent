"""Evaluation-only, instance-first extraction using the production fact verifier.

Chapter metadata selects retrieval work, never establishes a fact or widens a
validated subject's evidence scope. Unselected chapters remain explicit coverage
gaps. No reference answers or domain-specific classes participate in scheduling.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from copy import deepcopy
from time import perf_counter

from app.evaluation.hierarchy_variant import _score, build_plan
from app.schemas.evidence import (
    Candidate,
    DocumentProvenance,
    EvidenceRange,
    ExtractionTask,
    ValidationIssue,
)
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.evidence_scope import (
    build_scope,
    candidate_ref,
    document_anchors,
    scope_contains,
    scope_intervals,
)
from app.services.extraction.extraction_diagnostics import MODEL_FAILURES
from app.services.extraction.extraction_tasks import (
    SYSTEM,
    ExtractionRun,
    GenericExtractionRunner,
    PartialTaskFailure,
    SharedBindingDecision,
    round_robin,
)
from app.services.extraction.hierarchical_context import (
    TokenizationUnavailable,
    build_context,
    split_windows,
)
from app.services.extraction.model_protocol import TRANSPORT_VERSION
from app.services.llm.model_runtime import ModelCancelled

POLICY_VERSION = "evaluation-root-guided-v1"


class RootGuidedRunner(GenericExtractionRunner):
    """Discover a relation's direct range, verify the edge, then expand its object."""

    def __init__(self, base_runner, ir, plan, *, max_sections_per_predicate=3):
        if max_sections_per_predicate < 1:
            raise ValueError("max_sections_per_predicate must be positive")
        super().__init__(
            deepcopy(base_runner.schema),
            base_runner.tokenizer,
            base_runner.model_call,
            model_identity=base_runner.model_identity,
            budget=base_runner.budget.model_copy(deep=True),
            compact_identifiers=base_runner.compact_identifiers,
            priority_paths=deepcopy(base_runner.priority_paths),
        )
        self.ontology_release = base_runner.ontology_release
        self.trace_fn = base_runner.trace_fn
        self.assert_owner_fn = base_runner.assert_owner_fn
        self.plan = plan
        self.max_sections_per_predicate = max_sections_per_predicate
        self.variant_statistics = Counter(planning_wall_seconds=plan["planning_wall_seconds"])
        self.coverage = []

    def input_id(self, ir, effective_class="", *, scheduler_version=None):
        base = super().input_id(ir, effective_class, scheduler_version=POLICY_VERSION)
        return stable_id("root-guided", [base, self.plan["dependency_hash"]])

    def direct_range_classes(self, predicate):
        declared = set(predicate.get("range", []))
        return sorted(
            iri
            for iri, definition in self.schema.items()
            if iri in declared or declared.intersection(definition.get("parents", []))
        )

    def _invoke(self, task, envelope, response_type, stage, candidate=None):
        if stage != "recall" and envelope.serialized_input:
            payload = json.loads(envelope.serialized_input)
            definition = payload["task"].get("predicate_definition", {})
            if "discovery_relation" in definition:
                definition.pop("discovery_relation")
                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                envelope = envelope.model_copy(
                    update={
                        "serialized_input": serialized,
                        "context_hash": evidence_hash([envelope.context_hash, serialized]),
                    }
                )
        return super()._invoke(task, envelope, response_type, stage, candidate)

    def predicate_regions(self, ir, scope, predicate, *, kind):
        """Rank chapters for one predicate inside the existing accepted scope."""
        intervals = scope_intervals(scope, ir)
        groups = {}
        for unit in ir.evidence_units:
            if unit.evidence_id not in intervals:
                continue
            group = groups.setdefault(unit.section_node_id, [])
            group.extend(
                EvidenceRange(evidence_id=unit.evidence_id, start=left, end=right)
                for left, right in intervals[unit.evidence_id]
                if left < right
            )
        definitions = [predicate]
        if kind == "relationship":
            definitions.extend(
                self.schema[iri] for iri in predicate.get("range", []) if iri in self.schema
            )
        scores = {}
        for node_id in groups:
            scores[node_id] = sum(
                (0.5 ** cue["distance"])
                * max(
                    (
                        _score(cue["heading"], definition) + _score(cue["summary"], definition)
                        for definition in definitions
                    ),
                    default=0,
                )
                for cue in self.plan["nodes"].get(node_id, {}).get("cues", [])
            )
        ordered = sorted(groups, key=lambda node_id: -scores[node_id])
        selected = ordered[: self.max_sections_per_predicate]
        deferred = ordered[self.max_sections_per_predicate :]
        return [region for node_id in selected for region in groups[node_id]], {
            "selected_node_ids": selected,
            "deferred_node_ids": deferred,
            "node_scores": scores,
            "fallback_without_matching_cue": not any(scores.values()),
            "available_regions": sum(len(group) for group in groups.values()),
            "selected_regions": sum(len(groups[node_id]) for node_id in selected),
            "scope_id": scope.scope_id,
        }

    def _run(
        self,
        ir,
        *,
        effective_class="",
        checkpoint=None,
        should_pause=None,
        checkpoint_fn=None,
        snapshot_fn=None,
        retry_failed=False,
        pause_after=None,
    ):
        started = perf_counter()
        input_id = self.input_id(ir, effective_class)
        result = ExtractionRun(input_id=input_id, scheduler_version=POLICY_VERSION)
        self._active_result = result
        current = self._active_candidates = {}
        self.coverage = []
        if ir.document_role not in {"analysis_source", "default_source"}:
            result.diagnostics.append("non_production_source")
            return result
        if not effective_class or effective_class not in self.schema:
            result.diagnostics.append("root_guided_requires_explicit_document_class")
            return result
        title = next((unit for unit in ir.evidence_units if unit.text.strip()), None)
        if title is None:
            result.diagnostics.append("root_guided_empty_source")
            return result
        root = Candidate(
            candidate_id=stable_id("document-root", [ir.analysis_id, effective_class]),
            kind="entity",
            class_iri=effective_class,
            text=title.text,
            identity={"document_root": ir.document_hash, "classification_source": "job_metadata"},
            provenance=[
                DocumentProvenance(
                    document_role=ir.document_role,
                    anchors=[ir.anchor(title.evidence_id)],
                    excerpts=[title.text],
                )
            ],
            validation_status="passed",
            ontology_release=self.ontology_release,
            extractor_version="document-metadata-v1",
        )
        current[root.candidate_id] = root
        result.candidates = [root]
        if self.model_call is None or self.tokenizer is None:
            result.diagnostics.append(
                "model_unavailable" if self.model_call is None else "tokenizer_unavailable"
            )
            return result
        if pause_after is not None and pause_after < 1:
            raise ValueError("pause_after must be positive")
        previous = (
            deepcopy(checkpoint) if checkpoint and checkpoint.get("input_id") == input_id else {}
        )
        completed = previous.get("completed", {})
        failures = previous.get("failures", {})
        outcomes = previous.get("task_outcomes", {})
        joint_completed = previous.get("joint_completed", {})
        consumed = previous.get("attempt_count", 0)
        self._calls = previous.get("model_calls", 0)
        self._restorable_tasks = set(completed) | (set(failures) if not retry_failed else set())
        stop_after = (
            min(self.budget.max_tasks, consumed + pause_after)
            if pause_after
            else self.budget.max_tasks
        )
        stopped = False
        statistics = defaultdict(Counter)
        published = set()
        whole_unit_tasks = set()
        visited = set()
        task_registry = {}
        invalidations = []

        def stable_graph():
            safe = {
                c.candidate_id: c
                for c in current.values()
                if c.kind == "entity"
                and c.positive_eligible
                and (c.candidate_id == root.candidate_id or c.task_id in whole_unit_tasks)
            }
            pending = [
                c
                for c in current.values()
                if c.kind == "relationship"
                and c.validation_status == "passed"
                and c.task_id in whole_unit_tasks
            ]
            while pending:
                ready = [
                    c
                    for c in pending
                    if all(
                        ref.candidate_id in safe
                        for ref in [c.subject, c.object, *c.dependency_refs]
                        if ref is not None
                    )
                ]
                if not ready:
                    break
                safe.update((c.candidate_id, c) for c in ready)
                pending = [c for c in pending if c.candidate_id not in safe]
            return list(safe.values())

        def save():
            self.plan["coverage"] = deepcopy(self.coverage)
            self.plan["invalidations"] = deepcopy(invalidations)
            result.stage_statistics = {stage: dict(values) for stage, values in statistics.items()}
            result.performance = self._performance.snapshot()
            result.checkpoint = {
                "input_id": input_id,
                "scheduler_version": POLICY_VERSION,
                "transport_version": TRANSPORT_VERSION,
                "completed": completed,
                "failures": failures,
                "attempt_count": consumed,
                "model_calls": self._calls,
                "candidate_ids": list(current),
                "task_outcomes": outcomes,
                "joint_completed": joint_completed,
                "coverage": deepcopy(self.coverage),
                "invalidations": deepcopy(invalidations),
                "stage_statistics": result.stage_statistics,
            }
            if checkpoint_fn:
                checkpoint_fn(result.checkpoint)
            if snapshot_fn:
                safe = stable_graph()
                if any(c.candidate_id not in published for c in safe):
                    snapshot_fn(result.model_copy(update={"candidates": safe, "checkpoint": {}}))
                    published.update(c.candidate_id for c in safe)

        def make_task(kind, regions, **kwargs):
            payload = {
                "task_kind": kind,
                "target_evidence_ids": [r.evidence_id for r in regions],
                "target_ranges": regions,
                "ontology_release": self.ontology_release,
                "budget": self.budget,
                **kwargs,
            }
            return ExtractionTask(
                task_id=stable_id("root-guided-task", [input_id, payload]), **payload
            )

        def execute(task, coverage):
            nonlocal consumed, stopped
            if stopped:
                return []
            if any(
                ref.candidate_id not in current
                or current[ref.candidate_id].revision != ref.revision
                or not current[ref.candidate_id].positive_eligible
                for ref in task.dependency_refs
            ):
                coverage["status"] = "invalidated_dependency"
                return []
            task_registry[task.task_id] = task
            if all(
                r.start == 0 and r.end == len(ir.unit(r.evidence_id).text)
                for r in task.target_ranges
            ):
                whole_unit_tasks.add(task.task_id)
            restored = False
            if task.task_id in completed:
                values = [Candidate.model_validate(c) for c in completed[task.task_id]]
                status, restored = "restored", True
            elif (
                task.task_id in failures
                and not retry_failed
                and not failures[task.task_id].get("interrupted")
            ):
                failure = failures[task.task_id]
                values = [Candidate.model_validate(c) for c in failure.get("candidates", [])]
                result.diagnostics.extend(failure.get("issues", [failure["reason"]]))
                status, restored = "incomplete", True
            else:
                if consumed >= stop_after or (should_pause and should_pause()):
                    stopped = True
                    coverage["status"] = "interrupted"
                    result.diagnostics.append("task_budget_or_pause")
                    return []
                consumed += 1
                try:
                    values = self.execute_task(task, ir, current, effective_class)
                    completed[task.task_id] = [c.model_dump(mode="json") for c in values]
                    failures.pop(task.task_id, None)
                    status = "complete"
                except ModelCancelled as exc:
                    consumed -= 1
                    values, status, stopped = exc.candidates, "interrupted", True
                    failures[task.task_id] = {
                        "reason": "model_cancelled",
                        "interrupted": True,
                        "candidates": [c.model_dump(mode="json") for c in values],
                    }
                    result.diagnostics.append("model_cancelled")
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    values = exc.candidates if isinstance(exc, PartialTaskFailure) else []
                    issues = exc.issues if isinstance(exc, PartialTaskFailure) else [str(exc)]
                    if not isinstance(exc, PartialTaskFailure):
                        self._record_issue(exc)
                    failures[task.task_id] = {
                        "reason": str(exc),
                        "issues": issues,
                        "candidates": [c.model_dump(mode="json") for c in values],
                    }
                    result.diagnostics.extend(issues)
                    status = "incomplete"
                    stopped = isinstance(exc, TokenizationUnavailable) or str(
                        exc
                    ) in MODEL_FAILURES | {
                        "model_unavailable",
                        "model_call_budget_exceeded",
                    }
                outcomes[task.task_id] = list(self._task_events)
            new_entities = [
                c
                for c in values
                if c.kind == "entity" and c.positive_eligible and c.candidate_id not in current
            ]
            current.update((c.candidate_id, c) for c in values)
            # A later branch can reveal a competitor absent from an earlier
            # ownership decision. Invalidate its assertion and dependent paths.
            for entity in new_entities:
                invalidated_ids = set()

                def invalidate(candidate, code):
                    current[candidate.candidate_id] = candidate.model_copy(
                        update={
                            "validation_status": "conflict",
                            "validation_issues": [ValidationIssue(code=code)],
                        }
                    )
                    invalidated_ids.add(candidate.candidate_id)
                    invalidations.append(
                        {
                            "candidate_id": candidate.candidate_id,
                            "source_task_id": candidate.task_id,
                            "competitor_id": entity.candidate_id,
                            "code": code,
                        }
                    )
                    statistics["scheduler"]["invalidated"] += 1
                    result.diagnostics.append(code)

                for candidate in list(current.values()):
                    if (
                        candidate.kind not in {"property", "relationship"}
                        or not candidate.positive_eligible
                    ):
                        continue
                    owner = current[candidate.subject.candidate_id]
                    original = task_registry.get(candidate.task_id)
                    if (
                        owner.candidate_id in {root.candidate_id, entity.candidate_id}
                        or original is None
                        or candidate.scope is None
                        or any(
                            ref.candidate_id == entity.candidate_id
                            for ref in original.competing_subjects
                        )
                    ):
                        continue
                    if owner.identity.get("instance_iri") and owner.identity.get(
                        "instance_iri"
                    ) == entity.identity.get("instance_iri"):
                        continue
                    compatible = (
                        owner.class_iri == entity.class_iri
                        or owner.class_iri in self.schema[entity.class_iri].get("parents", [])
                        or entity.class_iri in self.schema[owner.class_iri].get("parents", [])
                    )
                    if compatible and any(
                        scope_contains(candidate.scope, a, ir) for a in document_anchors(entity)
                    ):
                        invalidate(candidate, "root_guided_late_competing_subject")
                while True:
                    dependents = [
                        c
                        for c in current.values()
                        if c.positive_eligible
                        and any(ref.candidate_id in invalidated_ids for ref in c.dependency_refs)
                    ]
                    if not dependents:
                        break
                    for candidate in dependents:
                        invalidate(candidate, "root_guided_invalidated_dependency")
            event = {
                "task": task.model_dump(mode="json"),
                "status": status,
                "restored": restored,
                "outcomes": outcomes.get(task.task_id, []),
            }
            if task.task_id in failures:
                event["reason"] = failures[task.task_id]["reason"]
            result.tasks.append(event)
            for outcome in event["outcomes"]:
                statistics[outcome["stage"]][outcome["category"]] += 1
            coverage["task_ids"].append(task.task_id)
            coverage["validated_candidates"] += sum(c.positive_eligible for c in values)
            if status in {"incomplete", "interrupted"}:
                coverage["status"] = status
            save()
            return values

        def subjects():
            return [c for c in current.values() if c.kind == "entity" and c.positive_eligible]

        def subject_context(state):
            subject = current[state["subject_id"]]
            competitors = [
                c
                for c in subjects()
                if c.candidate_id != subject.candidate_id
                and (
                    c.class_iri == subject.class_iri
                    or c.class_iri in self.schema[subject.class_iri].get("parents", [])
                    or subject.class_iri in self.schema[c.class_iri].get("parents", [])
                )
            ]
            if subject.identity.get("document_root") == ir.document_hash:
                competitors = []
            scope = build_scope(
                ir,
                subject,
                competitors,
                bound_relationships=[
                    current[ref.candidate_id]
                    for ref in state["dependencies"]
                    if ref.candidate_id in current
                    and current[ref.candidate_id].revision == ref.revision
                ],
            )
            intervals = scope_intervals(scope, ir)
            competitors = [
                c
                for c in competitors
                if any(
                    scope_contains(scope, anchor, ir, intervals=intervals)
                    for anchor in document_anchors(c)
                )
            ]
            return subject, scope, competitors

        def batches(regions):
            windows = (
                EvidenceRange(evidence_id=r.evidence_id, start=r.start + left, end=r.start + right)
                for r in regions
                for left, right in split_windows(
                    ir.unit(r.evidence_id).text[r.start : r.end],
                    self.tokenizer,
                    max(1, self.budget.max_input_tokens // 4),
                )
            )
            return list(self.pack_regions(ir, windows))

        frontier = [
            {
                "subject_id": root.candidate_id,
                "path": [],
                "dependencies": [],
                "ancestors": {root.candidate_id},
            }
        ]
        root_work, child_work = deque(), deque()
        turns = 0
        save()
        while (frontier or root_work or child_work) and not stopped:
            for state in frontier:
                if any(
                    not current[ref.candidate_id].positive_eligible for ref in state["dependencies"]
                ):
                    self.coverage.append(
                        {
                            "subject_id": state["subject_id"],
                            "kind": "expansion",
                            "relationship_path": state["path"],
                            "status": "invalidated_dependency",
                        }
                    )
                    continue
                subject, scope, _ = subject_context(state)
                key = evidence_hash(
                    [candidate_ref(subject), state["path"], state["dependencies"], scope]
                )
                if key in visited:
                    continue
                visited.add(key)
                definition = self.schema[subject.class_iri]
                predicates = list(
                    round_robin(
                        [
                            (("relationship", p) for p in definition.get("relationships", [])),
                            (("property", p) for p in definition.get("properties", [])),
                        ]
                    )
                )
                work = []
                for kind, predicate in predicates:
                    regions, route = self.predicate_regions(ir, scope, predicate, kind=kind)
                    coverage = {
                        "subject_id": subject.candidate_id,
                        "subject_class_iri": subject.class_iri,
                        "predicate_iri": predicate["iri"],
                        "kind": kind,
                        "relationship_path": state["path"],
                        "depth": len(state["path"]),
                        **route,
                        "status": "pending",
                        "task_ids": [],
                        "validated_candidates": 0,
                    }
                    self.coverage.append(coverage)
                    if kind == "relationship" and len(state["path"]) >= self.budget.max_hops:
                        coverage["status"] = "depth_limited"
                        continue
                    work.append((state, kind, predicate, coverage))
                work.sort(
                    key=lambda item: (
                        not any(
                            priority[: len(state["path"]) + 1] == (*state["path"], item[2]["iri"])
                            for priority in self.priority_paths
                        ),
                        -max(item[3]["node_scores"].values(), default=0),
                    )
                )
                (child_work if state["path"] else root_work).extend(work)
            frontier = []
            queue = child_work if child_work and (turns % 3 != 2 or not root_work) else root_work
            if not queue:
                continue
            state, kind, predicate, coverage = queue.popleft()
            turns += 1
            if any(
                not current[ref.candidate_id].positive_eligible for ref in state["dependencies"]
            ):
                coverage["status"] = "invalidated_dependency"
                continue
            # Complete this relation's selected discovery scope before binding it;
            # other predicates and deeper classes are not part of its entity menu.
            if kind == "relationship":
                subject, scope, competitors = subject_context(state)
                classes = self.direct_range_classes(predicate)
                coverage["range_class_iris"] = classes
                if not classes:
                    coverage["status"] = "unsupported_range"
                    continue
                regions, _ = self.predicate_regions(ir, scope, predicate, kind=kind)
                for menu in self.pack_classes(classes):
                    self.variant_statistics["max_entity_menu_classes"] = max(
                        self.variant_statistics["max_entity_menu_classes"], len(menu)
                    )
                    for batch in batches(regions):
                        menu_payload = self.entity_menu(menu)
                        menu_payload["predicate_definition"]["discovery_relation"] = predicate
                        task = make_task(
                            "entity",
                            batch,
                            **menu_payload,
                            subject=candidate_ref(subject),
                            scope=scope,
                            competing_subjects=[candidate_ref(c) for c in competitors],
                            path_root=candidate_ref(root),
                            relationship_path=state["path"],
                            dependency_refs=state["dependencies"],
                        )
                        execute(task, coverage)
                        if stopped:
                            break
                    if stopped:
                        break
            if stopped:
                coverage["status"] = "interrupted"
                break
            subject, scope, competitors = subject_context(state)
            regions, route = self.predicate_regions(ir, scope, predicate, kind=kind)
            coverage.update(route)
            objects = (
                [c for c in subjects() if c.class_iri in self.direct_range_classes(predicate)]
                if kind == "relationship"
                else []
            )
            size = self.budget.max_objects_per_task
            object_batches = (
                [objects[i : i + size] for i in range(0, len(objects), size)]
                if kind == "relationship"
                else [[]]
            )
            edges = []
            for batch in batches(regions):
                for object_batch in object_batches:
                    task = make_task(
                        kind,
                        batch,
                        subject=candidate_ref(subject),
                        scope=scope,
                        predicate_iri=predicate["iri"],
                        predicate_definition=predicate,
                        competing_subjects=[candidate_ref(c) for c in competitors],
                        object_candidates=[candidate_ref(c) for c in object_batch],
                        path_root=candidate_ref(root),
                        dependency_refs=state["dependencies"],
                        relationship_path=[*state["path"], predicate["iri"]]
                        if kind == "relationship"
                        else state["path"],
                    )
                    for fitted in self.fit_assertion_task(task, ir, current, effective_class):
                        values = execute(fitted, coverage)
                        edges.extend(
                            c for c in values if c.kind == "relationship" and c.positive_eligible
                        )
                        if stopped:
                            break
                    if stopped:
                        break
                if stopped:
                    break
            if coverage["status"] == "pending":
                coverage["status"] = (
                    "examined" if coverage["validated_candidates"] else "no_evidence"
                )
            for edge in edges:
                if edge.object.candidate_id in state["ancestors"]:
                    self.variant_statistics["cycle_expansions_stopped"] += 1
                    continue
                refs = [*edge.dependency_refs, edge.subject, candidate_ref(edge)]
                frontier.append(
                    {
                        "subject_id": edge.object.candidate_id,
                        "path": edge.relationship_path,
                        "dependencies": list(
                            {(r.candidate_id, r.revision): r for r in refs}.values()
                        ),
                        "ancestors": state["ancestors"] | {edge.object.candidate_id},
                    }
                )
        if stopped:
            for _, _, _, coverage in [*root_work, *child_work]:
                if coverage["status"] == "pending":
                    coverage["status"] = "interrupted"
            for state in frontier:
                self.coverage.append(
                    {
                        "subject_id": state["subject_id"],
                        "kind": "expansion",
                        "relationship_path": state["path"],
                        "status": "interrupted",
                    }
                )
        self._verify_shared_properties(
            ir, current, effective_class, make_task, joint_completed, result, stopped
        )
        if any(c.get("deferred_node_ids") for c in self.coverage):
            result.diagnostics.append("root_guided_unsearched_sections")
        if any(
            c["status"]
            in {
                "depth_limited",
                "unsupported_range",
                "pending",
                "interrupted",
                "invalidated_dependency",
            }
            for c in self.coverage
        ):
            result.diagnostics.append("root_guided_unexpanded_work")
        result.diagnostics = list(dict.fromkeys(result.diagnostics))
        result.candidates = list(current.values())
        for coverage in self.coverage:
            task_ids = set(coverage.get("task_ids", []))
            coverage["currently_validated_candidates"] = sum(
                c.positive_eligible and c.task_id in task_ids for c in current.values()
            )
            coverage["invalidated_candidate_ids"] = [
                item["candidate_id"] for item in invalidations if item["source_task_id"] in task_ids
            ]
        result.completion = "incomplete" if result.diagnostics else "complete"
        self.variant_statistics.update(
            {
                "coverage_records": len(self.coverage),
                "deferred_sections": sum(
                    len(c.get("deferred_node_ids", [])) for c in self.coverage
                ),
                "subjects_expanded": len(visited),
                "root_guided_wall_seconds": perf_counter() - started,
            }
        )
        save()
        return result

    def _verify_shared_properties(
        self,
        ir,
        current,
        effective_class,
        make_task,
        joint_completed,
        result,
        interrupted,
    ):
        """Retain the production shared-value ownership check after all waves."""
        from app.services.ontology_instance_writer import instance_iri

        groups = {}
        for candidate in current.values():
            if candidate.kind == "property" and candidate.validation_status == "passed":
                key = evidence_hash(
                    [
                        candidate.predicate_iri,
                        document_anchors(candidate),
                        candidate.literal,
                        candidate.assertion_status,
                        candidate.applicable_at,
                    ]
                )
                groups.setdefault(key, []).append(candidate)
        for group in groups.values():
            if len({instance_iri(current[c.subject.candidate_id]) for c in group}) == 1:
                continue
            subject_ids = {c.subject.candidate_id for c in group}
            if len(subject_ids) < 2:
                continue
            key = evidence_hash(group)
            if key in joint_completed:
                current.update(
                    (c.candidate_id, c)
                    for c in (Candidate.model_validate(value) for value in joint_completed[key])
                )
                continue
            first = group[0]
            task = make_task(
                "property",
                [
                    EvidenceRange(evidence_id=a.evidence_id, start=a.span_start, end=a.span_end)
                    for a in document_anchors(first)
                ],
                subject=first.subject,
                scope=first.scope,
                predicate_iri=first.predicate_iri,
                competing_subjects=[
                    candidate_ref(current[identity])
                    for identity in sorted(subject_ids)
                    if identity != first.subject.candidate_id
                ],
            )
            try:
                if interrupted:
                    raise ModelCancelled()
                envelope = build_context(
                    task,
                    ir,
                    current,
                    self.tokenizer,
                    model=self.model_identity,
                    effective_class=effective_class,
                    system_prompt=SYSTEM,
                    response_schema=SharedBindingDecision.model_json_schema(),
                    compact_identifiers=self.compact_identifiers,
                )
                decision = self._invoke(
                    task,
                    envelope,
                    SharedBindingDecision,
                    "verify_shared_binding",
                    {
                        "shared_candidates": [c.model_dump(mode="json") for c in group],
                        "instruction": (
                            "同一值重复归属多个主体，必须明确共享断言支持所有主体，否则拒答"
                        ),
                    },
                )
                anchors = [
                    self._anchor(span, ir, envelope.allowed_binding_regions)
                    for span in decision.assertion_spans
                ]
                shared = (
                    not decision.refusal_reason
                    and bool(anchors)
                    and set(decision.supported_subject_ids) == subject_ids
                )
            except ModelCancelled:
                shared, anchors, interrupted = False, [], True
                result.diagnostics.append("model_cancelled")
            except (ValueError, RuntimeError, TimeoutError) as exc:
                shared, anchors = False, []
                result.diagnostics.append(str(exc))
                interrupted = str(exc) in MODEL_FAILURES
            checked = []
            for candidate in group:
                if shared:
                    binding = candidate.bindings[0].model_copy(
                        update={
                            "anchors": [*candidate.bindings[0].anchors, *anchors],
                            "record_mapping": {
                                **candidate.bindings[0].record_mapping,
                                "shared_subject_ids": sorted(subject_ids),
                            },
                        }
                    )
                    candidate = candidate.model_copy(
                        update={"bindings": [*candidate.bindings, binding]}
                    )
                else:
                    candidate = candidate.model_copy(
                        update={
                            "validation_status": "conflict",
                            "validation_issues": [
                                ValidationIssue(
                                    code="shared_verification_interrupted"
                                    if interrupted
                                    else "ambiguous_shared_value"
                                )
                            ],
                        }
                    )
                checked.append(candidate)
            if not interrupted:
                joint_completed[key] = [c.model_dump(mode="json") for c in checked]
            current.update((c.candidate_id, c) for c in checked)


def build_root_guided_variant(
    base_runner,
    ir,
    structure=None,
    *,
    metadata=None,
    max_sections_per_predicate=3,
):
    # Empty schema avoids constructing the experiment's old global class rankings;
    # only validated chapter cues are reused from its metadata hygiene layer.
    plan = build_plan({}, ir, structure, mode="structure_summary", metadata=metadata)
    plan.update(
        {
            "policy_version": POLICY_VERSION,
            "mode": "root_guided",
            "ontology_release": base_runner.ontology_release,
            "max_sections_per_predicate": max_sections_per_predicate,
            "all_source_regions_and_classes_preserved": False,
            "planner": "current_predicate_chapter_routing_with_explicit_coverage_gaps",
        }
    )
    plan["dependency_hash"] = evidence_hash(
        {
            key: value
            for key, value in plan.items()
            if key not in {"dependency_hash", "planning_wall_seconds"}
        }
    )
    return RootGuidedRunner(
        base_runner, ir, plan, max_sections_per_predicate=max_sections_per_predicate
    )
