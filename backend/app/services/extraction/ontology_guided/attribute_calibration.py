"""Source observations awaiting calibration; these never grant graph facts."""

from app.schemas.attribute_calibration import AttributeCalibrationCandidate
from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from app.services.extraction.ontology_guided.value_observation import parse_attribute_value


def field_candidate(task, field, options, *, reason, parsed=None, checks=None, card=None):
    """Keep a known physical field even before a subject can be assigned."""
    datatypes = {kind for cls in card.class_cards for slot in cls.properties
                 for kind in slot.datatype_iris} if card else set()
    return AttributeCalibrationCandidate(
        candidate_id=stable_id("attribute-observation", [task.claim_lineage_id, field["field_id"]]),
        record_id=field["record_id"], field_id=field["field_id"],
        field_label=field["label"], raw_value=field["value"],
        parsed_value=parsed or parse_attribute_value(
            field["value"],
            expected_datatype=next(iter(datatypes)) if len(datatypes) == 1 else None,
        ),
        label_refs=[EvidenceAnchor.model_validate(ref) for ref in field["label_refs"]],
        value_refs=[EvidenceAnchor.model_validate(ref) for ref in field["value_refs"]],
        options=options, status="pending", reason_codes=[reason] if reason else [],
        checks=checks or {"binding": "not_checked", "metric": "not_checked",
                          "shacl": "not_checked"},
    )


def collect_candidates(task, context, card, frozen, checks, outcome, *, feedback):
    """Retain only authentic property sources rejected by the final fact gate."""
    accepted = {prop.candidate_id for prop in outcome.properties}
    attribute = context.tool_inputs.get("attribute_disambiguation")
    classes = {entity.local_id: entity.class_iri for entity in frozen.entities}
    classes.update({entity["entity_ref"]["id"]: entity["class_iri"]
                    for entity in context.tool_inputs.get("entity_dependencies", [])})
    candidates = []
    for prop in frozen.properties:
        reference = frozen.local_ref_map.get(prop.local_id)
        if reference is None or reference.id in accepted:
            continue
        if frozen.claim_issues.get(prop.local_id):
            # Invalid frozen authority never becomes a displayable property mapping.
            continue
        check = checks.get(prop.local_id)
        reasons = list(dict.fromkeys([
            *(check.issues if check else []), *feedback.get(prop.local_id, []),
        ]))
        if any(code.startswith(("reference_outside", "citation_", "source_not_", "claim_scope"))
               or code in {"metric_claim_mismatch", "attribute_answer_outside_scope"}
               for code in reasons):
            continue
        try:
            value, _ = resolve_fragment_quote(
                prop.value_quote.evidence_id, prop.value_quote.text, context.fragments,
                context_text=prop.value_quote.context_text, fact_required=True,
            )
            labels = [resolve_fragment_quote(
                quote.evidence_id, quote.text, context.fragments, context_text=quote.context_text,
            )[0] for quote in prop.field_support]
        except ValueError:
            continue
        owner = frozen.local_ref_map.get(prop.subject_id)
        owner_class = classes.get(prop.subject_id)
        if owner is not None:
            from .contracts import VersionedRef

            owner = next((VersionedRef.model_validate(resolution["entity_ref"])
                          for resolution in outcome.reference_resolutions
                          if resolution["claim_ref"]["id"] == owner.id), owner)
        properties = [slot for cls in card.class_cards if cls.class_iri == owner_class
                      for slot in cls.properties if slot.iri == prop.predicate_iri]
        if not properties:
            continue
        datatypes = {kind for slot in properties for kind in slot.datatype_iris}
        parsed = (check.parsed_value if check and check.parsed_value else parse_attribute_value(
            prop.value_quote.text,
            expected_datatype=next(iter(datatypes)) if len(datatypes) == 1 else None,
        ))
        options = (attribute["options"] if attribute else [{
            "subject_ref": owner.model_dump(mode="json"), "predicate_iri": prop.predicate_iri,
            "class_iri": owner_class,
        }] if owner else [])
        states = {name: "passed" if passed else "failed" if passed is False else "not_checked"
                  for name, passed in (check.checks if check else {}).items()}
        if not states.get("metric") == "passed":
            states["shacl"] = "not_checked"
        if attribute:
            candidate = field_candidate(task, attribute, options, reason=None, parsed=parsed,
                                        checks=states)
        else:
            candidate = AttributeCalibrationCandidate(
                candidate_id=stable_id("attribute-observation", [
                    task.claim_lineage_id, value, labels,
                ]), record_id=task.record_id, field_label=" / ".join(
                    quote.text for quote in prop.field_support),
                raw_value=prop.value_quote.text, parsed_value=parsed,
                label_refs=labels, value_refs=[value], options=options,
                status="pending", reason_codes=[], checks=states,
            )
        rejected = any(code in {
            "datatype_mismatch", "source_unit_conflict", "unit_missing_or_incompatible",
            "semantic_not_supported", "predicate_not_supported", "owner_row_mismatch",
        } for code in reasons)
        candidates.append(candidate.model_copy(update={
            "reason_codes": reasons or ["attribute_unresolved"],
            "source_claim_id": reference.id,
            "status": "rejected_mapping" if rejected else "pending",
        }))
    if attribute and not outcome.properties and not candidates:
        candidates.append(field_candidate(
            task, attribute, attribute["options"], reason=outcome.reason_code, card=card,
        ))
    return candidates


def merge_candidates(previous, outcome):
    """A failed attempt or omitted proposal cannot erase a valid source observation."""
    from .field_bindings import contains

    merged = {}
    for value in previous:
        candidate = AttributeCalibrationCandidate.model_validate(value)
        resolved = any(
            any(contains(source, ref) for source in candidate.value_refs
                for ref in prop.value_evidence_refs)
            and (candidate.field_id is not None or not candidate.options or any(
                option.get("predicate_iri") == prop.predicate_iri
                and option.get("subject_ref") == prop.subject_ref.model_dump(mode="json")
                for option in candidate.options
            ))
            for prop in outcome.properties
        )
        if resolved:
            continue
        if not outcome.complete:
            candidate = candidate.model_copy(update={"reason_codes": list(dict.fromkeys([
                *candidate.reason_codes, outcome.reason_code,
            ]))})
        merged[candidate.candidate_id] = candidate
    for candidate in outcome.attribute_candidates:
        merged[candidate.candidate_id] = candidate
    return list(merged.values())


def calibration_inputs(task, candidates, dependencies, relations, index, *, is_valid):
    """Only relevant owned, proved relation sources can change this input signature."""
    source_ids = set(task.source_record_ids or [task.record_id])
    source_units = {unit.evidence_id for identity in source_ids
                    for unit in index.by_id[identity].source_units}
    sections = {index.by_id[identity].section_node_id for identity in source_ids}
    owners = {option["subject_ref"]["id"] for candidate in candidates
              for option in candidate.options if option.get("subject_ref")}
    local_entities = []
    for entity in dependencies.get("entity_dependencies", []):
        sources = entity.get("source_refs", [])
        if entity.get("grounding_kind") == "document_root" or any(
            ref["evidence_id"] in source_units or ref["section_node_id"] in sections
            for ref in sources
        ):
            local_entities.append(entity)
            owners.add(entity["entity_ref"]["id"])
    selected, anchors = [], {}
    for relation in relations:
        endpoints = ([relation.object_ref] if hasattr(relation, "object_ref")
                     else relation.object_refs)
        if (relation.scope != task.scope or not is_valid(relation)
                or not owners.intersection([relation.subject_ref.id,
                                             *(ref.id for ref in endpoints)])
                or not any(ref.evidence_id in source_units or ref.section_node_id in sections
                           for ref in relation.evidence_refs)):
            continue
        selected.append({
            "candidate_id": relation.candidate_id, "revision": relation.revision,
            "subject_ref": relation.subject_ref.model_dump(mode="json"),
            "predicate_iri": relation.predicate_iri,
            "object_refs": [ref.model_dump(mode="json") for ref in endpoints],
            "proof_ref": relation.proof_ref.model_dump(mode="json"),
            "polarity": relation.polarity, "modality": relation.modality,
            "conditions": relation.conditions,
        })
        for ref in relation.evidence_refs:
            index.ir.resolve(ref)
            anchors[evidence_hash(ref)] = ref
    selected.sort(key=lambda value: (value["candidate_id"], value["revision"]))
    signature = evidence_hash({
        "entities": sorted(local_entities, key=lambda entity: entity["entity_ref"]["id"]),
        "relations": selected,
        "bindings": dependencies.get("reference_resolutions", []),
    })
    return signature, selected, list(anchors.values())
