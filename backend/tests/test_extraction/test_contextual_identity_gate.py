"""Deferred scalar fields cannot enter a different claim through entity identifiers."""

import copy

import pytest

from app.services.extraction.ontology_guided.attribute_disambiguation import field_payload
from app.services.extraction.ontology_guided.claim_freeze import freeze_record_proposal
from app.services.extraction.ontology_guided.claim_protocol import (
    DiscoveryEnvelope,
    ExtractionProfile,
    IdentityKeySpec,
    build_verification_input,
)
from app.services.extraction.ontology_guided.context import assemble_record_discovery_context
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    OntologyClassDefinition,
    OntologySnapshot,
    SlotSpec,
    VersionedRef,
)
from app.services.extraction.ontology_guided.record_discovery import (
    RecordDiscoveryTask,
    compile_record_schema_card,
)
from tests.test_extraction.test_contextual_record_adapter import field_for
from tests.test_extraction.test_record_model_adapter import source as source  # noqa: F401


@pytest.mark.parametrize("identifier_text", ["5", "5 mg"])
@pytest.mark.parametrize("deferred", [True, False])
def test_identifier_cannot_bypass_deferred_field_while_independent_claims_remain(
    source, identifier_text, deferred,
):
    index = source["index"]
    ontology = OntologySnapshot(
        snapshot_id="snapshot", ontology_hash="ontology",
        classes={iri: OntologyClassDefinition(
            iri=iri, label=iri, source_hash="fixture", declared_properties=[SlotSpec(
                iri=predicate, label=label, declared_by=[iri],
                datatype_iris=["http://www.w3.org/2001/XMLSchema#string"],
            )],
        ) for iri, predicate, label in [
            ("urn:T", "urn:number", "数量"), ("urn:Other", "urn:name", "名称"),
        ]},
    )
    profile = ExtractionProfile(identity_keys=[IdentityKeySpec(
        class_iri="urn:T", property_iris=["urn:number"], namespace=None,
        scope="document", declaration_ref="declared-identity-key",
    )])
    card = compile_record_schema_card(
        ontology, class_iris=ontology.classes, analysis_scope_ref="scope", profile=profile,
    )
    task = RecordDiscoveryTask.create(
        run_fingerprint="run", record_id=source["record_id"],
        schema_card_id=card.schema_card_id, analysis_scope_ref=card.analysis_scope_ref,
        dependency_hash="source", source_record_ids=[source["record_id"]],
    )
    context = assemble_record_discovery_context(
        task, card, index, ontology_hash=ontology.ontology_hash,
        document_context=DocumentContext(
            document_hash=index.ir.document_hash, document_class_iri="urn:Document",
            root_ref=VersionedRef(id="document-root", revision=1),
        ),
    )
    context.tool_inputs = {
        "deferred_property_fields": [field_payload(field_for(source))] if deferred else [],
    }
    proposal = copy.deepcopy(source["proposal"])
    proposal["entities"][0]["identifier_claims"] = [{
        "predicate_iri": "urn:number", "value_quote": source["quote"](identifier_text),
    }]
    proposal["properties"] = [{
        "local_id": "other-name", "subject_id": "c", "predicate_iri": "urn:name",
        "value_quote": source["quote"]("样品丙"),
        "field_support": [source["quote"]("名称")], "unit_support": [],
        "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                       "condition_support": [], "scope_qualifiers": []},
        "bridge_kind": "explicit_assertion", "bridge_ref_ids": [],
    }]
    proposal = DiscoveryEnvelope.model_validate(proposal)
    original = proposal.model_dump_json()
    frozen = freeze_record_proposal(
        proposal, task=task, context=context, card=card, index=index, generation=1,
        reference_resolution=True,
    )

    assert frozen.claim_issues == (
        {"b": ["attribute_deferred_to_disambiguation"]} if deferred else {}
    )
    # The frozen answer remains inspectable; rejection must never rewrite the model response.
    assert frozen.entities == proposal.entities
    assert frozen.properties == proposal.properties
    assert len(frozen.entities[0].identifier_claims) == 1
    assert proposal.model_dump_json() == original

    verification = build_verification_input(
        frozen, discovery_ref="saved-discovery", context=context, card=card, scope=task.scope,
        entity_dependencies=[], external_candidates=[], bridge_dependencies=[],
        scope_resolutions=[],
    )
    targets = {(item.target_kind, item.payload.local_id) for item in verification.targets}
    expected = {("entity", "c"), ("property", "other-name")}
    assert targets == (expected if deferred else expected | {("entity", "b")})

