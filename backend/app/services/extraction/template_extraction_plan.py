"""Expand declarative template requirements into exact, versioned instance tasks."""

from collections import Counter

from app.schemas.evidence import Candidate
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.fact_selector import SELECTOR_VERSION, FactSelector, predicate_steps
from app.services.ontology_instance_writer import instance_iri
from app.services.reporting.ast_template import ReportTemplate, coverage_key


def discovery_revision(candidates, extraction_run=None):
    return evidence_hash(
        {
            "candidates": sorted(
                (c.model_dump(mode="json") for c in candidates), key=lambda c: c["candidate_id"]
            ),
            "run": {
                k: v
                for k, v in (extraction_run or {}).items()
                if k not in {"discovery_decisions", "gap_history", "checkpoint", "performance"}
            },
        }
    )


def build_instance_coverage(
    template: ReportTemplate,
    selector: FactSelector | None,
    *,
    candidates=(),
    template_version="",
    template_db_id=None,
    extraction_run=None,
    discovery_decisions=None,
):
    candidates = [
        c if isinstance(c, Candidate) else Candidate.model_validate(c) for c in candidates
    ]
    discovery = discovery_revision(candidates, extraction_run)
    tasks = []
    for section in template.sections:
        for index, binding in enumerate(section.coverage):
            target_id = stable_id(
                "target", [section.section_id, index, binding.model_dump(mode="json")]
            )
            base = {
                "target_id": target_id,
                "section_id": section.section_id,
                "coverage_key": coverage_key(binding),
                "label": binding.label or coverage_key(binding),
                "required": getattr(binding, "required", False),
            }
            if binding.kind != "ontology_relation":
                tasks.append(
                    {
                        **base,
                        "status": "incomplete",
                        "reason": "external_source_not_committed",
                        "subject_instance_iri": None,
                        "assertion_ids": [],
                    }
                )
                continue
            path = predicate_steps(binding.predicate_path or [binding.predicate_iri])
            base.update(
                root_class_iri=binding.doc_class_iri,
                range_class_iri=binding.range_class_iri,
                predicate_path=[s.model_dump() for s in path],
                required_properties=binding.required_properties,
                quantifier=binding.quantifier,
                min_count=binding.min_count,
                max_count=binding.max_count,
                applicable_at=binding.applicable_at,
                subject_root_class_iri=binding.subject_root_class_iri,
                subject_path=[s.model_dump() for s in binding.subject_path],
            )
            subjects = selector.instances(binding.doc_class_iri) if selector else []
            subject_support = {}
            if binding.subject_path:
                if selector:
                    for root in selector.instances(binding.subject_root_class_iri):
                        selected = selector.select(
                            root, binding.subject_path, range_class_iri=binding.doc_class_iri
                        )
                        if selected["conflict_assertion_ids"]:
                            continue
                        for obj in selected["objects"]:
                            subject_support.setdefault(obj["instance_iri"], set()).update(
                                obj["assertion_ids"]
                            )
                subjects = sorted(set(subjects) & subject_support.keys())
            if binding.subject_instance_iris:
                subjects = sorted(set(subjects) & set(binding.subject_instance_iris))
            root_candidates = [
                c
                for c in candidates
                if c.kind == "entity"
                and (
                    selector.class_matches(c.class_iri, binding.doc_class_iri)
                    if selector
                    else c.class_iri == binding.doc_class_iri
                )
                and c.review_status != "rejected"
                and (not binding.subject_path or instance_iri(c) in subject_support)
            ]
            for candidate in root_candidates:
                subject_iri = instance_iri(candidate)
                if subject_iri in subjects or (
                    binding.subject_instance_iris
                    and subject_iri not in binding.subject_instance_iris
                ):
                    continue
                tasks.append(
                    {
                        **base,
                        "subject_instance_iri": None,
                        "subject_candidate_ref": {
                            "candidate_id": candidate.candidate_id,
                            "revision": candidate.revision,
                        },
                        "status": "conflict"
                        if candidate.validation_status == "conflict"
                        else "pending_review"
                        if candidate.review_status == "pending"
                        else "incomplete",
                        "reason": "subject_not_published",
                        "assertion_ids": [],
                        "object_universe_status": "unresolved",
                    }
                )
            if not subjects and not any(t["target_id"] == target_id for t in tasks):
                tasks.append(
                    {
                        **base,
                        "subject_instance_iri": None,
                        "status": "incomplete",
                        "reason": "subject_unresolved",
                        "assertion_ids": [],
                        "object_universe_status": "unresolved",
                    }
                )
            for subject in subjects:
                result = selector.select(
                    subject,
                    path,
                    range_class_iri=binding.range_class_iri,
                    object_iris=binding.object_instance_iris,
                    required_properties=binding.required_properties,
                    applicable_at=binding.applicable_at,
                )
                result["assertion_ids"] = sorted(
                    set(result["assertion_ids"]) | subject_support.get(subject, set())
                )
                task_id = stable_id("coverage-task", [target_id, subject])
                closed = (discovery_decisions or {}).get(task_id, {})
                universe = (
                    "complete"
                    if binding.object_instance_iris is not None
                    or (
                        closed.get("discovery_revision") == discovery
                        and closed.get("snapshot_id") == selector.snapshot_id
                        and closed.get("actor")
                        and closed.get("reason")
                    )
                    else "open"
                )
                object_ids = {o["instance_iri"] for o in result["objects"]}
                expected = set(
                    binding.object_instance_iris or closed.get("object_iris", []) or object_ids
                )
                # Pending facts may block a conclusion, but can never satisfy it.
                published_refs = {
                    (r["candidate"]["candidate_id"], r["candidate"]["revision"])
                    for r in selector.records
                }
                reachable_refs = {
                    r["candidate"]["candidate_id"]
                    for r in selector.records
                    if r["subject_iri"] in {subject, *object_ids}
                }
                related = [
                    c
                    for c in candidates
                    if c.review_status != "rejected"
                    and (c.candidate_id, c.revision) not in published_refs
                    and (
                        (
                            c.subject
                            and c.subject.candidate_id in reachable_refs
                            and c.predicate_iri
                            in {*(p.predicate_iri for p in path), *binding.required_properties}
                        )
                        or (c.kind == "entity" and instance_iri(c) == subject)
                    )
                ]
                count = len(result["qualified_object_iris"])
                status, reason = "filled", "requirements_satisfied"
                if result["conflict_assertion_ids"] or any(
                    c.validation_status == "conflict" for c in related
                ):
                    status, reason = "conflict", "unresolved_fact_conflict"
                elif binding.max_count is not None and len(object_ids) > binding.max_count:
                    status, reason = "conflict", "max_count_exceeded"
                elif related:
                    status, reason = (
                        ("pending_review", "related_candidates_pending")
                        if any(c.review_status == "pending" for c in related)
                        else ("incomplete", "related_candidates_not_committed")
                    )
                elif result["confirmed_absent"]:
                    status, reason = "confirmed_absent", "scoped_committed_negative"
                elif (
                    binding.quantifier == "all" or binding.max_count is not None
                ) and universe != "complete":
                    status, reason = "incomplete", "object_universe_open"
                elif count < binding.min_count or (
                    binding.quantifier == "all"
                    and (not expected <= set(result["qualified_object_iris"]))
                ):
                    status, reason = "missing", "path_or_object_properties_missing"
                tasks.append(
                    {
                        **base,
                        **result,
                        "coverage_task_id": task_id,
                        "status": status,
                        "reason": reason,
                        "object_universe_status": universe,
                        "expected_object_iris": sorted(expected),
                        "candidate_refs": [
                            {"candidate_id": c.candidate_id, "revision": c.revision}
                            for c in related
                        ],
                    }
                )
    counts = Counter(t["status"] for t in tasks)
    payload = {
        "schema_version": 1,
        "template_id": template_db_id or template.template_id,
        "template_version": template_version or template.revision,
        "template_hash": evidence_hash(template),
        "snapshot_id": selector.snapshot_id if selector else None,
        "selector_version": SELECTOR_VERSION,
        "discovery_revision": discovery,
        "ontology_release": evidence_hash(selector.schema) if selector else None,
        "tasks": tasks,
        "counts": dict(counts),
        "required_gaps": sum(
            t["required"] and t["status"] not in {"filled", "not_applicable"} for t in tasks
        ),
        "completion": "complete"
        if tasks and all(t["status"] == "filled" or not t["required"] for t in tasks)
        else "incomplete",
        "diagnostics": [
            *template.diagnostics,
            *([] if tasks else ["template_coverage_undeclared"]),
        ],
    }
    return {"manifest_id": stable_id("coverage", payload), **payload}
