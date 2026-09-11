"""Canonical evidence content identity, independent of task IDs and retrieval order."""

from app.services.extraction.evidence_identity import evidence_hash

EVIDENCE_REPAIR_VERSION = "evidence-repair-v1"
SCOPE_PROTOCOL_VERSION = "source-quoted-scope-v1"
LITERAL_QUOTE_VERSION = "source-integer-quotes-v2"


def evidence_content_hash(context) -> str:
    def ordered(values):
        return sorted(values, key=evidence_hash)

    return evidence_hash(
        {
            "version": EVIDENCE_REPAIR_VERSION,
            "scope_protocol": SCOPE_PROTOCOL_VERSION,
            "literal_quotes": LITERAL_QUOTE_VERSION,
            "document": context.target.document_context,
            "ontology_hash": context.target.ontology_hash,
            "subject": context.target.subject_ref,
            "predicate": context.target.predicate_iri,
            "record": context.record_id,
            "fragments": ordered([f.model_dump(mode="json") for f in context.fragments]),
            "dependencies": ordered(context.proof_dependencies),
            "subject_sources": ordered(context.subject_evidence_refs),
            "owners": ordered(context.owner_field_refs),
            "required": ordered(context.required_context_refs),
            "counterevidence": ordered(context.counterevidence_refs),
            "bindings": ordered(
                [
                    b.model_dump(mode="json", exclude={"field_binding_id"})
                    for b in context.field_bindings
                ]
            ),
            "proof_menu": context.proof_menu,
        }
    )
