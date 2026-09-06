"""Read-only compatibility views. Never use these flattened views to commit facts."""

from app.schemas.evidence import Candidate


def _predicate_label(schema: dict, class_iri: str, predicate_iri: str) -> str:
    definition = schema.get(class_iri, {})
    for field in ("properties", "relationships"):
        match = next(
            (item for item in definition.get(field, []) if item.get("iri") == predicate_iri),
            None,
        )
        if match and match.get("label"):
            return match["label"]
    return predicate_iri


def preview_relationships(candidates: list[Candidate], schema: dict) -> list[dict]:
    entities = {c.candidate_id: c for c in candidates if c.kind == "entity"}
    values: dict[str, list] = {}
    for candidate in candidates:
        if candidate.kind == "property" and candidate.positive_eligible:
            values.setdefault(candidate.subject.candidate_id, []).append(
                {
                    "iri": candidate.predicate_iri,
                    "label": _predicate_label(
                        schema, entities[candidate.subject.candidate_id].class_iri,
                        candidate.predicate_iri,
                    ) if candidate.subject.candidate_id in entities else candidate.predicate_iri,
                    "value": candidate.literal.raw_value,
                    "candidate_id": candidate.candidate_id,
                }
            )
    result = []
    for candidate in candidates:
        if candidate.kind != "relationship" or not candidate.positive_eligible:
            continue
        subject = entities.get(candidate.subject.candidate_id)
        target = entities.get(candidate.object.candidate_id)
        if subject is None or target is None:
            continue
        result.append(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_revision": candidate.revision,
                "review_status": candidate.review_status,
                "commit_status": candidate.commit_status,
                "subject_candidate_id": subject.candidate_id,
                "object_candidate_id": target.candidate_id,
                "subject_class_iri": subject.class_iri,
                "subject_text": subject.text,
                "subject_class_label": schema.get(subject.class_iri, {}).get(
                    "label", subject.class_iri
                ),
                "predicate_iri": candidate.predicate_iri,
                "predicate_label": _predicate_label(
                    schema, subject.class_iri, candidate.predicate_iri,
                ),
                "object_class_iri": target.class_iri,
                "object_text": target.text,
                "object_class_label": schema.get(target.class_iri, {}).get(
                    "label", target.class_iri
                ),
                "object_source": "evidence_candidate",
                "object_data_properties": values.get(target.candidate_id, []),
                "sub_relationships": [],
                "source_ref": "",
                "preview_only": True,
            }
        )
    return result
