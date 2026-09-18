"""Archive identity is separate from document facts and local referents."""

import pytest

from app.services.extraction.ontology_guided.claim_protocol import (
    ExternalCandidate,
    IdentityKeySpec,
)
from app.services.extraction.ontology_guided.contracts import VersionedRef
from tests.test_extraction.test_relationship_groups import run_case

pytest_plugins = ["tests.test_extraction.test_relationship_groups"]


@pytest.mark.parametrize("key_proven", [False, True])
def test_external_identity_requires_key_proof_and_preserves_provenance(graph_case, key_proven):
    proposal, mapping, _context, card, *_rest = graph_case
    entity = proposal["entities"][0]
    quote = entity["mentions"][0]
    if key_proven:
        entity["identifier_claims"] = [{"predicate_iri": "urn:documentCode", "value_quote": quote}]
    card.identity_keys = [IdentityKeySpec(
        class_iri=entity["class_iri"], property_iris=["urn:documentCode"],
        namespace="urn:archive", scope="dataset", declaration_ref="frozen-test-profile",
    )]
    candidate = ExternalCandidate(
        candidate_id="external-candidate", source_id="test-archive", system="mock", dataset="items",
        record_key="record-7", record_version="v2", class_iri=entity["class_iri"],
        matches=[dict(predicate_iri=None, document_quote=quote, record_field="label",
                      record_value=quote["text"], match_kind="name")],
        mapped_fields=[dict(predicate_iri="urn:documentCode", raw_value=quote["text"],
                            datatype_iri="http://www.w3.org/2001/XMLSchema#string")],
        metadata=[],
    )
    proposal["external_links"] = [dict(local_id="archive-link", subject_id=entity["local_id"],
                                      external_candidate_id=candidate.candidate_id,
                                      identity_support=[quote])]
    mapping["archive-link"] = VersionedRef(id="archive-link", revision=1)
    result = run_case(graph_case, external_candidates=[candidate])
    node = next(item for item in result.nodes if item.entity_id == entity["local_id"])
    assert node.revision == 1
    assert not result.properties and not result.edges
    assert len(result.relationship_groups) == 1
    if key_proven:
        assert result.complete and node.identity_status == "verified"
        assert len(node.identity_decision_refs) == 3
        assert node.external_provenance[0].record_key == "record-7"
        assert node.external_provenance[0].record_version == "v2"
        assert node.external_provenance[0].identity_match_evidence
        assert not node.external_provenance[0].record_snapshot
    else:
        assert not result.complete and node.identity_status == "document_local"
        assert not node.external_provenance
        assert "external_identity_key_not_proven" in result.reason
