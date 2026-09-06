"""Bounded instance-directed gap tasks; new assertions always return to review."""

from app.schemas.evidence import EvidenceRange, ExtractionTask, ScopeExpansion
from app.services.extraction.evidence_identity import stable_id
from app.services.extraction.evidence_scope import (
    build_scope,
    candidate_ref,
    document_anchors,
    expand_scope,
    scope_intervals,
)
from app.services.extraction.extraction_tasks import PartialTaskFailure
from app.services.extraction.hierarchical_context import split_windows
from app.services.ontology_instance_writer import instance_iri


def expanded_gap_scope(ir, subject, candidates, max_expansions):
    scope = build_scope(ir, subject, candidates)
    if not max_expansions:
        return scope
    included = {r.evidence_id for r in scope.ranges}
    nodes = {n["node_id"]: n for n in ir.nodes}
    subject_sections = {a.section_node_id for a in document_anchors(subject)}
    parent_sections = {
        nodes[section].get("parent_id") for section in subject_sections if section in nodes
    }
    for section in [*sorted(subject_sections), *sorted(s for s in parent_sections if s)]:
        heading = next(
            (u for u in ir.evidence_units if u.kind == "heading" and u.section_node_id == section),
            None,
        )
        additions = [
            EvidenceRange(evidence_id=u.evidence_id)
            for u in ir.evidence_units
            if u.section_node_id == section and u.text and u.evidence_id not in included
        ]
        if heading and additions:
            return expand_scope(
                scope,
                ScopeExpansion(
                    reason="coverage_gap_enclosing_section",
                    added_ranges=additions,
                    evidence=[ir.anchor(heading.evidence_id)],
                ),
                ir,
                max_expansions,
            )
    return scope


def run_gap_round(ir, inputs, candidates, selector, runner):
    if runner.model_call is None or runner.tokenizer is None:
        return [], "model_unavailable", []
    mapping = {c.candidate_id: c for c in candidates}
    instances = {
        instance_iri(c): c for c in candidates if c.kind == "entity" and c.positive_eligible
    }
    results, history, scheduled = [], [], set()
    for target in inputs["tasks"]:
        if target["status"] not in {"missing", "incomplete"}:
            continue
        if target["reason"] == "object_universe_open" and not any(
            obj.get("missing_properties") for obj in target.get("objects", [])
        ):
            continue  # only an explicit discovery decision can close the universe
        root = instances.get(target.get("subject_instance_iri"))
        if root is None:
            continue
        specs = []
        for obj in target.get("objects", []):
            subject = instances.get(obj["instance_iri"])
            if subject:
                specs.extend(
                    ("property", subject, predicate, None)
                    for predicate in obj["missing_properties"]
                )
        if not specs and not target.get("objects"):
            # Resolve the first missing hop; never jump to a same-type downstream object.
            frontier = [root]
            path = target["predicate_path"]
            for step in path:
                next_frontier = []
                for subject in frontier:
                    found = selector.select(instance_iri(subject), [step])
                    if found["objects"]:
                        next_frontier.extend(
                            instances[o["instance_iri"]]
                            for o in found["objects"]
                            if o["instance_iri"] in instances
                        )
                    elif step["direction"] == "forward":
                        specs.append(
                            (
                                "relationship",
                                subject,
                                step["predicate_iri"],
                                target["range_class_iri"],
                            )
                        )
                if specs:
                    break
                frontier = next_frontier
        for kind, subject, predicate, target_class in specs:
            definition = runner.schema.get(subject.class_iri, {})
            menu = definition.get("properties" if kind == "property" else "relationships", [])
            spec = next((p for p in menu if p["iri"] == predicate), None)
            if spec is None:
                continue
            objects = [
                c
                for c in candidates
                if c.kind == "entity"
                and c.positive_eligible
                and any(selector.class_matches(c.class_iri, cls) for cls in spec.get("range", []))
            ]
            # Missing endpoints are recalled with the same generic entity task.
            task_kind = "entity" if kind == "relationship" and not objects else kind
            scope = expanded_gap_scope(ir, subject, candidates, runner.budget.max_scope_expansions)
            regions = (
                EvidenceRange(evidence_id=identity, start=left + start, end=left + end)
                for identity, intervals in scope_intervals(scope, ir).items()
                for left, right in intervals
                for start, end in split_windows(
                    ir.unit(identity).text[left:right],
                    runner.tokenizer,
                    max(1, runner.budget.max_input_tokens // 4),
                )
            )
            for batch in runner.pack_regions(ir, regions):
                payload = {
                    "task_kind": task_kind,
                    "target_ranges": batch,
                    "target_evidence_ids": [r.evidence_id for r in batch],
                    "trigger": "coverage_gap",
                    "budget": runner.budget,
                    "ontology_release": runner.ontology_release,
                }
                if task_kind == "entity":
                    classes = [cls for cls in spec.get("range", []) if cls in runner.schema]
                    payload.update(
                        target_class_iris=classes,
                        predicate_definition={
                            "classes": {
                                cls: {
                                    key: value
                                    for key, value in runner.schema[cls].items()
                                    if key in {"iri", "label", "description", "parents"}
                                }
                                for cls in classes
                            }
                        },
                    )
                else:
                    payload.update(
                        subject=candidate_ref(subject),
                        scope=scope,
                        predicate_iri=predicate,
                        predicate_definition=spec,
                        object_candidates=[candidate_ref(c) for c in objects]
                        if kind == "relationship"
                        else [],
                        competing_subjects=[
                            candidate_ref(c)
                            for c in candidates
                            if c.kind == "entity"
                            and c.candidate_id != subject.candidate_id
                            and c.class_iri == subject.class_iri
                        ],
                    )
                identity = stable_id(
                    "gap-task", [runner.input_id(ir, inputs["input_class_iri"]), payload]
                )
                if identity in scheduled:
                    continue
                if len(scheduled) >= runner.budget.max_tasks:
                    return results, "task_budget_exhausted", history
                scheduled.add(identity)
                task = ExtractionTask(task_id=identity, **payload)
                try:
                    values = runner.execute_task(task, ir, mapping, inputs["input_class_iri"])
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    history.append(
                        {"task_id": identity, "status": "incomplete", "reason": str(exc)}
                    )
                    if isinstance(exc, PartialTaskFailure):
                        new = [c for c in exc.candidates if c.candidate_id not in mapping]
                        if new:
                            return new, "pending_review", history
                    if str(exc) == "model_unavailable":
                        return results, "model_unavailable", history
                    continue
                history.append(
                    {
                        "task_id": identity,
                        "status": "complete",
                        "scope": scope.model_dump(mode="json"),
                    }
                )
                new = [c for c in values if c.candidate_id not in mapping]
                if new:
                    # No silent review/commit, nor another round on unreviewed subjects.
                    return new, "pending_review", history
    return results, "no_new_evidence", history
