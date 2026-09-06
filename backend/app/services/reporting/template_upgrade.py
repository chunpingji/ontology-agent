"""Versioned declarative template migration, independent of extraction semantics."""

from copy import deepcopy

from app.services.extraction.evidence_identity import evidence_hash
from app.services.fact_selector import FactSelector
from app.services.reporting.ast_template import ReportTemplate


def prepare_template_upgrade(original, plan, schema):
    if evidence_hash(original) != plan["expected_schema_hash"]:
        raise ValueError("template changed since migration review; rebuild the plan")
    result = deepcopy(original)
    result["revision"] = plan["to_version"]
    result["diagnostics"] = plan.get("diagnostics", [])
    slots = plan["slot_sources"]
    seen = set()
    sections = set()
    for section in result["sections"]:
        sections.add(section["section_id"])
        if section["section_id"] in plan["coverage_by_section"]:
            section["coverage"] = deepcopy(plan["coverage_by_section"][section["section_id"]])
        for group in section["groups"]:
            for slot in group["slots"]:
                key = slot["slot_id"]
                seen.add(key)
                slot["source"] = deepcopy(slots.get(key, plan["default_unmapped_source"]))
    if slots.keys() - seen or plan["coverage_by_section"].keys() - sections:
        raise ValueError("migration references missing stable template IDs")
    template = ReportTemplate.model_validate(result)
    matcher = FactSelector({"snapshot_id": "schema-validation", "assertions": []}, schema=schema)
    for section in template.sections:
        for binding in section.coverage:
            if binding.kind != "ontology_relation":
                continue
            frontier = {binding.subject_root_class_iri or binding.doc_class_iri}
            for step in [*binding.subject_path, *binding.predicate_path]:
                if step.direction != "forward":
                    raise ValueError("upgrade plan currently requires forward schema paths")
                frontier = {
                    target
                    for cls in frontier
                    for prop in schema.get(cls, {}).get("relationships", [])
                    if prop["iri"] == step.predicate_iri
                    for target in prop.get("range", [])
                }
                if not frontier:
                    raise ValueError(f"unknown/undeclared ontology path: {step.predicate_iri}")
            if binding.predicate_path and not any(
                matcher.class_matches(cls, binding.range_class_iri) for cls in frontier
            ):
                raise ValueError("coverage range does not match the complete path")
            properties = {
                p["iri"] for p in schema.get(binding.range_class_iri, {}).get("properties", [])
            }
            if set(binding.required_properties) - properties:
                raise ValueError("coverage references an unknown endpoint property")
        for group in section.groups:
            for slot in group.slots:
                if slot.source.kind != "snapshot":
                    continue
                source = slot.source
                if not any(
                    c.kind == "ontology_relation"
                    and c.doc_class_iri == source.root_class_iri
                    and c.predicate_path == source.predicate_path
                    and c.range_class_iri == source.range_class_iri
                    for c in section.coverage
                ):
                    raise ValueError("snapshot slot has no matching section coverage path")
                known = {
                    p["iri"] for p in schema.get(source.range_class_iri, {}).get("properties", [])
                }
                if set(source.data_property_iris) - known:
                    raise ValueError("snapshot slot references unknown property")
    # Preserve all original prompts, origins and other metadata verbatim.
    return result
