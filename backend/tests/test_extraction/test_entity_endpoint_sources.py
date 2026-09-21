"""Type evidence must not expand the physical endpoint a relation must bind."""

import json
from copy import deepcopy

import pytest

from app.services.document_analysis import current_state
from app.services.llm import local_client
from tests.test_extraction.test_record_executor import ROOT_EDGE, TEXT, A, record_setup
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = [
    "tests.test_extraction.test_tool_engine_resume",
    "tests.test_extraction.test_tool_engine_freeze",
]


def test_remote_type_support_is_retained_without_becoming_an_endpoint(tool_source, monkeypatch):
    adapter, task, context, predicate, menu, _stored, _requests = setup_adapter(
        tool_source, monkeypatch,
    )
    transport = local_client.responses_create
    remote = next(fragment for fragment in context.fragments
                  if fragment.purpose == "required_context")

    def respond(client, **kwargs):
        turn = transport(client, **kwargs)
        item = turn.output_items[0]
        if item["type"] == "message":
            part = item["content"][0]
            answer = json.loads(part["text"])
            for target in answer.get("verifications", []):
                for facet in target["facets"]:
                    if facet["name"] == "type":
                        facet["support"].append(
                            tool_source["quote"](remote.text, supplemental=True),
                        )
            part["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", respond)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert outcome.nodes and outcome.complete
    for node in outcome.nodes:
        assert all(ref.evidence_id != remote.anchor.evidence_id for ref in node.evidence_refs)
        decision = next(value for value in outcome.decision_payloads
                        if value["decision_id"] == node.type_decision_ref.id)
        assert any(ref["evidence_id"] == remote.anchor.evidence_id
                   for ref in decision["support_refs"])


@pytest.mark.parametrize("representation", ["mention", "record"])
def test_registered_endpoint_keeps_physical_sources_and_separate_type_proof(
    tmp_path, monkeypatch, current_run, representation,
):
    def transform(view, payload, *, record):
        if record and view["stage"] == "discovery" and representation == "record":
            entity = next(item for item in payload["entities"] if item["class_iri"] == A)
            source = entity["mentions"][0]
            entity.update(representation="record", mentions=[], record_components=[
                {"role": role, "quote": {**source, "text": text}}
                for role, text in (("subject", "装置甲"), ("field", "型号"), ("value", "A"))
            ])
        if not record and view["stage"] == "discovery" and representation == "record":
            for relation in payload["relations"]:
                if relation["predicate_iri"] == ROOT_EDGE:
                    support = relation["source_assertion"]["object_support"][0]["support"]
                    support[:] = [{**support[0], "text": text} for text in ("装置甲", "型号", "A")]
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    outcomes = []
    persist_batch = hooks["batch_hook"]

    def capture(batch):
        if batch.outcome is not None:
            outcomes.append(deepcopy(batch.outcome))
        persist_batch(batch)

    hooks["batch_hook"] = capture
    result = factory().run(**args, **hooks)
    node = next(node for node in result.graph.nodes if node.class_iri == A)
    expected = ["装置甲"] if representation == "mention" else ["装置甲", "型号", "A"]
    assert [args["ir"].resolve(anchor) for anchor in node.evidence_refs] == expected
    decisions = [decision for outcome in outcomes for decision in outcome.decision_payloads
                 if decision["decision_id"] == node.type_decision_ref.id]
    assert len(decisions) == 1
    assert [args["ir"].resolve(anchor) for anchor in decisions[0]["support_refs"]] == [TEXT]
    assert node.type_decision_ref in node.dependency_refs
    relations = [member for request in requests if request["stage"] == "discovery"
                 for member in request.get("members", []) if member["predicate_iri"] == ROOT_EDGE]
    assert relations
    for member in relations:
        dependency = next(item for item in member["registered_entities"] if item["class_iri"] == A)
        # Transport aliases the evidence ID, but preserves exact physical bounds.
        assert [(ref["span_start"], ref["span_end"]) for ref in dependency["source_refs"]] == [
            (anchor.span_start, anchor.span_end) for anchor in node.evidence_refs
        ]
    assert any(edge.predicate_iri == ROOT_EDGE for edge in result.graph.edges)
    store, run, _token = current_run
    before = len(requests)
    factory().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(requests) == before
