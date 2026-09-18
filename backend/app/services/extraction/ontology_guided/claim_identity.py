"""Exact local mention identity; names and overlapping evidence never imply sameness."""

from app.services.extraction.evidence_identity import evidence_hash


def duplicate_mentions(entities, dependencies, references, resolve_quote):
    """Return duplicate local IDs and existing refs, using only grounding mentions.

    Record compositions require their own proof; broad supporting excerpts are not
    mention identity. Unresolvable quotes are rejected by the source gate instead.
    """
    def key(proposal):
        if proposal is None or proposal.representation != "mention":
            return None
        try:
            anchors = sorted({evidence_hash(resolve_quote(q)) for q in proposal.mentions})
        except ValueError:
            return None
        return (proposal.class_iri, tuple(anchors)) if anchors else None

    known = {}
    for dependency in dependencies:
        identity = key(dependency.proposal)
        if identity is not None:
            known.setdefault(identity, dependency.entity_ref)
    duplicates = {}
    for proposal in entities:
        identity = key(proposal)
        if identity is None:
            continue
        if identity in known:
            duplicates[proposal.local_id] = known[identity]
        else:
            known[identity] = references[proposal.local_id]
    return duplicates
