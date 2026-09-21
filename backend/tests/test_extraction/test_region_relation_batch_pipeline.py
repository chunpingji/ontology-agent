"""Region entity discovery feeds multiple legal predicates without sharing verdicts."""

from copy import deepcopy

import pytest

from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import EdgeSpec
from tests.test_extraction.test_ontology_guided_core import _definition
from tests.test_extraction.test_record_executor import ROOT, ROOT_EDGE, TEXT, A, record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

OTHER_RELATIONS = ("urn:region:uses", "urn:region:maintains")


@pytest.mark.parametrize("reject_second", [False, True])
def test_registered_region_endpoints_feed_multiple_predicates_with_independent_proof(
    tmp_path, monkeypatch, current_run, reject_second,
):
    relation_proposal = None

    def transform(view, payload, *, record):
        nonlocal relation_proposal
        if record:
            return payload
        predicate = view["predicate_iri"]
        if view["stage"] == "discovery":
            if predicate == ROOT_EDGE:
                relation_proposal = deepcopy(payload["relations"][0])
            elif predicate in OTHER_RELATIONS:
                assert relation_proposal is not None  # Root admission happens first.
                proposal = deepcopy(relation_proposal)
                proposal["predicate_iri"] = predicate
                proposal["subject_id"] = view["subject_ref"]["id"]
                registered_ids = {
                    entity["entity_ref"]["id"] for entity in view["registered_entities"]
                }
                assert set(proposal["object_ids"]) <= registered_ids
                payload["relations"] = [proposal]
        elif predicate == OTHER_RELATIONS[1] and reject_second:
            for verification in payload["verifications"]:
                for facet in verification["facets"]:
                    if facet["name"] == "predicate":
                        facet.update(verdict="unsupported", support=[],
                                     reason="原文共现不足以证明维护关系。")
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    ontology = factory().adapter.ontology
    definition = ontology.classes[ROOT]
    ontology.classes[ROOT] = _definition(
        ROOT, definition.label,
        relationships=[*definition.declared_relationships, *[
            EdgeSpec(iri=iri, label=label, declared_by=[ROOT], range_class_iris=[A])
            for iri, label in zip(OTHER_RELATIONS, ("使用", "维护"), strict=True)
        ]],
    )
    ontology.ontology_hash = evidence_hash(ontology.classes)

    result = factory().run(**args, **hooks)

    multi_discovery = [
        request for request in requests if request["stage"] == "discovery"
        and {member["predicate_iri"] for member in request.get("members", [])}
        == set(OTHER_RELATIONS)
    ]
    assert len(multi_discovery) == 1, [
        (request["stage"], [member["predicate_iri"]
                            for member in request.get("members", [])])
        for request in requests
    ]
    request = multi_discovery[0]
    assert len(request["evidence_units"]) == 1  # One shared source, per-member permissions.
    assert request["evidence_units"][0]["text"] == TEXT
    assert all(any(entity["class_iri"] == A for entity in member["registered_entities"])
               for member in request["members"])
    region_discovery = [request for request in requests
                        if request["stage"] == "discovery" and "members" not in request]
    assert len(region_discovery) == 1
    assert {prop.raw_value for prop in result.graph.properties} == {"A", "B"}
    supported = {edge.predicate_iri for edge in result.graph.edges
                 if edge.decision_status == "supported"}
    assert OTHER_RELATIONS[0] in supported
    assert (OTHER_RELATIONS[1] in supported) is not reject_second

    store, run, _token = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    receipts = [receipt for receipt in calls["reservations"]
                if len(receipt.get("member_task_ids", [])) == 2]
    assert receipts and any("discovery" in receipt["stage"] for receipt in receipts)
    before = len(requests)
    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    factory().run(**args, **hooks, resume_state=restored, model_call_state=calls)
    assert len(requests) == before  # No new evidence: do not repeat discovery or rejected claims.
