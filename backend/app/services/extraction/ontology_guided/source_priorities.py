"""Source-bound scheduling hints; none confer semantic or identity authority."""

import re

from app.services.extraction.ontology_guided.field_bindings import contains, field_bindings


def _label(value):
    return re.sub(r"\s+", "", value).strip("：:").casefold()


def explicit_attribute_sources(index, record_id, predicate, owner_refs):
    if predicate.kind != "property" or not owner_refs:
        return []
    sources = []
    for binding in field_bindings(index, record_id):
        if binding.mapping_status != "structural_candidate":
            continue
        if not any(_label(index.ir.resolve(ref)) == _label(predicate.label)
                   for ref in binding.label_refs):
            continue
        # Exact physical ownership, including a recorded merged cell; repeated
        # text in another row never qualifies. Full verification still follows.
        local = [*binding.target_value_refs, *binding.owner_candidate_refs]
        if not any(contains(source, owner) for source in local for owner in owner_refs):
            continue
        if not any(index.ir.resolve(ref).strip() not in {"", "—", "-", "N/A"}
                   for ref in binding.target_value_refs):
            continue
        sources.append({"field_binding_id": binding.field_binding_id,
                        "label_refs": [r.model_dump(mode="json") for r in binding.label_refs],
                        "owner_refs": [r.model_dump(mode="json") for r in owner_refs
                                       if any(contains(s, r) for s in local)]})
    return sources
