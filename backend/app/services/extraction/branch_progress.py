"""Projection of root-branch execution, separate from semantic graph evidence."""

from app.services.extraction.relationship_priority import range_classes


def project_branches(schema, root_class, candidates, ledger, *, complete=False):
    result = {}
    roots = {c.candidate_id for c in candidates if c.identity.get("document_root")
             and c.class_iri == root_class}
    for predicate in schema.get(root_class, {}).get("relationships", []):
        iri = predicate["iri"]
        classes = set(range_classes(schema, predicate))
        discoveries = [v for v in ledger.values() if v["kind"] == "entity"
                       and v.get("attempted", True)
                       and classes.intersection(v.get("classes", []))]
        relations = [v for v in ledger.values() if v["kind"] == "relationship"
                     and v.get("attempted", True)
                     and v.get("predicate") == iri and v.get("root")]
        objects = [c for c in candidates if c.kind == "entity" and c.class_iri in classes
                   and c.positive_eligible]
        rejected_objects = [c for c in candidates if c.kind == "entity" and c.class_iri in classes
                            and c.validation_status in {"rejected", "conflict"}]
        edges = [c for c in candidates if c.kind == "relationship" and c.predicate_iri == iri
                 and c.subject and c.subject.candidate_id in roots]
        positive = [c for c in edges if c.positive_eligible]
        linked_objects = {edge.object.candidate_id for edge in positive if edge.object}
        attributes = [value for value in ledger.values() if value["kind"] == "property"
                      and value.get("subject") in linked_objects and value.get("attempted", True)]
        positive_properties = [candidate for candidate in candidates
                               if candidate.kind == "property" and candidate.subject
                               and candidate.subject.candidate_id in linked_objects
                               and candidate.positive_eligible]
        property_failures = [value for value in attributes if value["status"] == "incomplete"]
        has_property_menu = any(schema.get(candidate.class_iri, {}).get("properties")
                                for candidate in objects
                                if candidate.candidate_id in linked_objects)
        property_status = (
            "not_applicable" if linked_objects and not has_property_menu
            else "extracting" if any(value["status"] == "running" for value in attributes)
            else "incomplete" if property_failures
            else "complete" if complete and linked_objects
            else "partial" if attributes else "queued"
        )
        failures = [v for v in [*discoveries, *relations] if v["status"] == "incomplete"]
        reasons = sorted({reason for v in failures for reason in v.get("reasons", [])})
        if not objects:
            reasons = sorted(set(reasons) | {issue.code for c in rejected_objects
                                             for issue in c.validation_issues})
        if positive:
            status = "identified"
        elif any(v["status"] == "running" for v in relations):
            status = "extracting_relation"
        elif any(v["status"] == "running" for v in discoveries):
            status = "extracting_entities"
        elif any(v["status"] == "incomplete" for v in relations):
            status = "relation_failed"
        elif any(c.validation_status != "passed" for c in edges):
            status = "relation_failed"
        elif not objects and (failures or rejected_objects):
            status = "entities_failed"
        elif complete:
            status = "no_match"
        elif any(v["status"] == "complete" for v in relations):
            status = "relation_checked"
        elif objects:
            status = "awaiting_relation"
        elif discoveries:
            status = "searching_entities"
        else:
            status = "queued"
        result[iri] = {
            "status": status, "reason_codes": reasons,
            "discovery_tasks": len(discoveries), "relationship_tasks": len(relations),
            "failed_tasks": len(failures), "positive_count": len(positive),
            "coverage_complete": complete,
            "property_status": property_status, "property_tasks": len(attributes),
            "positive_property_count": len(positive_properties),
            "failed_property_tasks": len(property_failures),
        }
    return result
