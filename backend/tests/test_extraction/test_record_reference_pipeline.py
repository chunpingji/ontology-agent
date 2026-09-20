"""Saved record-stage coreference feeds a later relation through the real store."""

import json
from copy import deepcopy
from functools import partial

from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    OntologySnapshot,
    ScopeMember,
    TraversalScope,
)
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.record_discovery import RECORD_PIPELINE
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.reference_dependencies import (
    ReferenceBindingDependencyView,
)
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]
ROOT, DEVICE, PART = "urn:Report", "urn:Device", "urn:Part"
EDGE = "urn:contains"
FIRST, NEXT = "装置甲是一台装置。", "该装置包含部件乙。"


def reference_setup(tmp_path, monkeypatch, current_run, *, missing_binding=False):
    store, run, token = current_run
    args = arguments(tmp_path, [FIRST, NEXT])
    args.update(recognition_run_id=str(run.recognition_run_id), run_fingerprint=run.run_fingerprint,
                root_class_iri=ROOT)
    run.document_hash = args["ir"].document_hash
    payload = {"analysis": args["ir"].model_dump(mode="json")}
    store.update_artifact(
        run.recognition_run_id, run.owner_id, token, artifact_kind="metadata",
        expected_revision=0, artifact_hash=current_state.content_hash(payload),
        status="ready", artifact_id="reference-fixture-metadata", payload=payload,
    )
    store.db.commit()
    classes = {
        ROOT: _definition(ROOT, "报告", relationships=[
            EdgeSpec(iri="urn:describes", label="描述", declared_by=[ROOT],
                     range_class_iris=[DEVICE]),
        ]),
        DEVICE: _definition(DEVICE, "装置", relationships=[
            EdgeSpec(iri=EDGE, label="包含", declared_by=[DEVICE], range_class_iris=[PART]),
        ]),
        PART: _definition(PART, "部件"),
    }
    ontology = OntologySnapshot(
        snapshot_id="record-references", ontology_hash=evidence_hash(classes),
        classes=classes, created_from="frozen_fixture",
    )
    index = RecordIndex(args["ir"])
    first = next(record for record in index.records if record.text == FIRST)
    second = next(record for record in index.records if record.text == NEXT)
    units = {FIRST: first.source_units[0], NEXT: second.source_units[0]}
    requests = []

    def quote(text, *, antecedent=False):
        return dict(evidence_id=units[FIRST if antecedent else NEXT].evidence_id,
                    text=text, context_text=None)

    def entity(identity, class_iri, text, *, antecedent=False):
        return dict(local_id=identity, class_iri=class_iri, representation="mention",
                    mentions=[quote(text, antecedent=antecedent)], record_components=[],
                    identifier_claims=[])

    def answer(view, *, record):
        if view["stage"] == "verification":
            support = [dict(evidence_id=unit["evidence_id"], text=unit["text"], context_text=None)
                       for unit in view["evidence_units"]]
            return {"verifications": [{
                "target_id": target["target_id"], "content_hash": target["content_hash"],
                "facets": [dict(name=facet, verdict="supported", support=support,
                                counterevidence_support=[], reason="独立核验原文")
                           for facet in target["required_facets"]],
            } for target in view["verification_input"]["targets"]]}
        result = dict(entities=[], properties=[], relations=[], external_links=[],
                      observations=[], reference_bindings=[])
        registered = view.get("registered_entities", [])
        device = next((item["entity_ref"]["id"] for item in registered
                       if item["class_iri"] == DEVICE), None)
        if record:
            if view["record_id"] == first.record_id:
                if device is None:
                    result["entities"] = [entity("device", DEVICE, "装置甲", antecedent=True)]
            else:
                result["entities"] = [entity("part", PART, "部件乙")]
                if device and not missing_binding:
                    result["entities"].append(entity("pronoun", DEVICE, "该装置"))
                    result["reference_bindings"] = [dict(
                        local_id="binding", source_id="pronoun", target_id=device,
                        binding_kind="anaphora",
                        support=[quote("装置甲", antecedent=True), quote("该装置")],
                    )]
        elif view["predicate_iri"] == EDGE:
            destination = next((item["entity_ref"]["id"] for item in registered
                                if item["class_iri"] == PART), None)
            eligible = any(unit["evidence_id"] == units[NEXT].evidence_id and unit["fact_eligible"]
                           for unit in view["evidence_units"])
            if destination and eligible:
                dependencies = [dep["binding_ref"] for dep in view["reference_dependencies"]]
                result["relations"] = [dict(
                    local_id="relation", subject_id=view["subject_ref"]["id"], predicate_iri=EDGE,
                    object_ids=[destination], selection="all", bridge_support=[quote(NEXT)],
                    selection_support=[], qualifiers=dict(polarity="affirmed", modality="asserted",
                                                         condition_support=[], scope_qualifiers=[]),
                    bridge_kind="resolved_reference_chain", bridge_ref_ids=[],
                    source_assertion=dict(subject_support=[quote("该装置")],
                                          object_support=[dict(object_id=destination,
                                                               support=[quote("部件乙")])],
                                          predicate_support=[quote(NEXT)], binding_ids=[],
                                          binding_dependency_refs=dependencies),
                )]
        return result

    def transport(_client, **kwargs):
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [dict(type="function_call", id=f"check-{i}", call_id=f"call-{i}",
                           name="validate_graph", arguments=json.dumps({
                               **check, "shape_profile_id": required["shape_profile_id"],
                           })) for i, check in enumerate(required["required_relation_checks"])]
        else:
            if "members" in view:
                shared = {unit["unit_id"]: unit for unit in view["evidence_units"]}
                payload = {"members": [dict(task_id=member["task_id"], result=answer({
                    **member, "stage": view["stage"], "evidence_units": [
                        {**shared[ref["unit_id"]], **ref} for ref in member["evidence_refs"]
                    ],
                }, record=False)) for member in view["members"]]}
            else:
                payload = answer(view, record=True)
            output = [dict(type="message", role="assistant", status="completed",
                           content=[dict(type="output_text", text=json.dumps(payload))])]
        return ResponseTurn(response_id=f"reference-{len(requests)}", response_status="completed",
                            output_items=output, incomplete_details=None, error=None,
                            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(), index=index, ontology=ontology, metadata=args["metadata"],
        profile=ExtractionProfile(), token_counter=len, model_identity="controlled-record-model",
        max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(20000),
        recognition_pipeline=RECORD_PIPELINE,
    )

    def executor(**kwargs):
        return OntologyGuidedExecutor(ontology=ontology, engine=None, adapter=adapter,
                                      current_state=True, max_hops=2, **kwargs)

    def calls(state):
        if state.get("work_changes"):
            state = {**state, "expected_work_version": run.work_version}
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)

    def work(changes):
        current_state.persist_boundary(store, run, token, changes=changes,
                                       fingerprint=run.run_fingerprint,
                                       expected_version=run.work_version)

    def batch(value):
        current_state.persist_batch(
            store, run, token, batch=value, fingerprint=run.run_fingerprint, ontology=ontology,
            ir=args["ir"], metadata=args["metadata"], index=index,
            expected_version=run.work_version,
        )

    hooks = dict(model_call_hook=calls, work_hook=work, batch_hook=batch,
                 protocol_result_loader=partial(current_state.load_protocol_result, store, run),
                 protocol_record_loader=partial(current_state.load_protocol_record, store, run))
    return args, executor, requests, hooks


def test_record_binding_reconstructs_original_proof_before_relationship(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = reference_setup(tmp_path, monkeypatch, current_run)
    result = executor().run(**args, **hooks)
    assert len([node for node in result.graph.nodes if node.class_iri == DEVICE]) == 1
    assert any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.events
    relation_views = [member for view in requests for member in view.get("members", [])
                      if member["predicate_iri"] == EDGE and member["reference_dependencies"]]
    assert relation_views
    assert all("reference_evidence" not in member for member in relation_views)
    store, run, _ = current_run
    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    resolutions = restored["work_state"]["reference_resolutions"]
    assert any(item["value"].get("binding_origin") for item in resolutions.values())


def test_missing_original_binding_cannot_turn_pronoun_into_relationship_endpoint(
    tmp_path, monkeypatch, current_run,
):
    args, executor, _, hooks = reference_setup(
        tmp_path, monkeypatch, current_run, missing_binding=True,
    )
    result = executor().run(**args, **hooks)
    assert not any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.events


def test_binding_scope_mismatch_cannot_be_overridden_by_supported_relation_facets(
    tmp_path, monkeypatch, current_run,
):
    from app.services.extraction.ontology_guided import record_binding_evidence

    restore = record_binding_evidence.restore_record_binding
    restored_bindings = []

    def wrong_scope(*args, **kwargs):
        view, evidence = restore(*args, **kwargs)
        payload = view.model_dump(mode="json", exclude={"content_hash"})
        payload["scope"] = TraversalScope.create([
            ScopeMember(relation_ref=view.binding_ref, member_ref=view.entity_ref),
        ]).model_dump(mode="json")
        changed = ReferenceBindingDependencyView.model_validate({
            **payload, "content_hash": evidence_hash(payload),
        })
        restored_bindings.append(changed)
        return changed, evidence

    monkeypatch.setattr(record_binding_evidence, "restore_record_binding", wrong_scope)
    args, executor, _, hooks = reference_setup(tmp_path, monkeypatch, current_run)
    result = executor().run(**args, **hooks)
    assert restored_bindings
    assert len([node for node in result.graph.nodes if node.class_iri == DEVICE]) == 1
    assert not any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.events
