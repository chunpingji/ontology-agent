"""Record discovery through the actual coordinator, worker and transactional store."""
import json
from copy import deepcopy
from functools import partial

import pytest

from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import EdgeSpec, OntologySnapshot, SlotSpec
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.model_reference_projection import (
    project_reference_payload,
)
from app.services.extraction.ontology_guided.record_discovery import (
    RECORD_PIPELINE,
    RECORD_PROTOCOL,
    RecordDiscoveryPolicy,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]
ROOT, A, B = "urn:ZReport", "urn:ADevice", "urn:BComponent"
EDGE, VALUE, ROOT_EDGE = "urn:connects", "urn:model", "urn:describes"
TEXT = "装置甲型号A，连接规格B的部件乙。"


def record_setup(tmp_path, monkeypatch, current_run, *, split=False, empty_relations=False,
                 transform=None, texts=None, page_size=8):
    store, run, token = current_run
    args = arguments(tmp_path, texts or [TEXT])
    args.update(recognition_run_id=str(run.recognition_run_id),
                run_fingerprint=run.run_fingerprint, root_class_iri=ROOT)
    run.document_hash = args["ir"].document_hash
    payload = {"analysis": args["ir"].model_dump(mode="json")}
    store.update_artifact(
        run.recognition_run_id, run.owner_id, token, artifact_kind="metadata",
        expected_revision=0, artifact_hash=current_state.content_hash(payload),
        status="ready", artifact_id="record-fixture-metadata", payload=payload,
    )
    store.db.commit()
    classes = {
        ROOT: _definition(ROOT, "报告", relationships=[
            EdgeSpec(iri=ROOT_EDGE, label="描述", declared_by=[ROOT], range_class_iris=[A]),
        ]),
        A: _definition(A, "装置", properties=[
            SlotSpec(iri=VALUE, label="型号", declared_by=[A],
                     datatype_iris=["http://www.w3.org/2001/XMLSchema#string"]),
        ], relationships=[
            EdgeSpec(iri=EDGE, label="连接", declared_by=[A], range_class_iris=[B]),
        ]),
        B: _definition(B, "部件", properties=[
            SlotSpec(iri=VALUE, label="规格", declared_by=[B],
                     datatype_iris=["http://www.w3.org/2001/XMLSchema#string"]),
        ]),
    }
    ontology = OntologySnapshot(snapshot_id="record-fixture", ontology_hash=evidence_hash(classes),
                                classes=classes, created_from="frozen_fixture")
    index = RecordIndex(args["ir"])
    unit = index.records[0].source_units[0]
    requests = []
    def quote(text):
        return {"evidence_id": unit.evidence_id, "text": text,
                "context_text": TEXT if text == "部件乙" else None}
    qualifiers = dict(polarity="affirmed", modality="asserted",
                      condition_support=[], scope_qualifiers=[])

    def answer(view, *, record):
        payload = base_answer(view, record=record)
        return transform(view, payload, record=record) if transform else payload

    def base_answer(view, *, record):
        if view["stage"] == "verification":
            return {"verifications": [{
                "target_id": target["target_id"], "content_hash": target["content_hash"],
                "facets": [dict(name=facet, verdict="supported", support=[quote(TEXT)],
                                counterevidence_support=[], reason="原文逐项支持")
                           for facet in target["required_facets"]],
            } for target in view["verification_input"]["targets"]]}
        result = dict(entities=[], properties=[], relations=[], external_links=[],
                      observations=[], reference_bindings=[])
        registered = view.get("registered_entities", [])
        if record:
            permitted = {card["class_iri"] for card in view["schema_card"]["class_cards"]}
            for cls, name, value, field in ((A, "装置甲", "A", "型号"),
                                           (B, "部件乙", "B", "规格")):
                if cls not in permitted:
                    continue
                owner = next((e["entity_ref"]["id"] for e in registered
                              if e["class_iri"] == cls), None)
                if owner is None:
                    owner = "a" if cls == A else "b"
                    result["entities"].append(dict(
                        local_id=owner, class_iri=cls, representation="mention",
                        mentions=[quote(name)], record_components=[], identifier_claims=[],
                    ))
                result["properties"].append(dict(
                    local_id="p" + value, subject_id=owner, predicate_iri=VALUE,
                    value_quote=quote(value), field_support=[quote(field)], unit_support=[],
                    qualifiers=qualifiers, bridge_kind="explicit_assertion", bridge_ref_ids=[],
                ))
        elif view["predicate_iri"] == EDGE and not empty_relations:
            destination = next((e["entity_ref"]["id"] for e in registered
                                if e["class_iri"] == B), None)
            if destination:
                result["relations"].append(dict(
                    local_id="rel", subject_id=view["subject_ref"]["id"], predicate_iri=EDGE,
                    object_ids=[destination], selection="all", bridge_support=[quote(TEXT)],
                    selection_support=[], qualifiers=qualifiers,
                    bridge_kind="explicit_assertion", bridge_ref_ids=[],
                    source_assertion=dict(
                        subject_support=[quote("装置甲")],
                        object_support=[dict(object_id=destination, support=[quote("部件乙")])],
                        predicate_support=[quote(TEXT)], binding_ids=[],
                        binding_dependency_refs=[],
                    ),
                ))
        return result

    def transport(_client, **kwargs):
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [dict(
                type="function_call", id=f"check-{i}", call_id=f"call-{i}", name="validate_graph",
                arguments=json.dumps({**check, "shape_profile_id": required["shape_profile_id"]}),
            ) for i, check in enumerate(required["required_relation_checks"])]
        else:
            if "members" in view:
                payload = {"members": [dict(
                    task_id=member["task_id"], result=answer({**member, "stage": view["stage"]},
                                                           record=False),
                ) for member in view["members"]]}
            else:
                payload = answer(view, record=True)
            output = [dict(type="message", role="assistant", status="completed",
                           content=[dict(type="output_text", text=json.dumps(payload))])]
        return ResponseTurn(
            response_id=f"record-response-{len(requests)}", response_status="completed",
            output_items=output, incomplete_details=None, error=None,
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(), index=index, ontology=ontology, metadata=args["metadata"],
        profile=ExtractionProfile(), token_counter=len, model_identity="controlled-record-model",
        max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(20000),
        recognition_pipeline=RECORD_PIPELINE,
        record_discovery=RecordDiscoveryPolicy(max_classes_per_card=1 if split else 4,
                                                endpoint_page_size=page_size),
        reference_resolution=True,
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
    hooks = dict(
        model_call_hook=calls, work_hook=work, batch_hook=batch,
        protocol_result_loader=partial(current_state.load_protocol_result, store, run),
        protocol_record_loader=partial(current_state.load_protocol_record, store, run),
    )
    return args, executor, requests, hooks


def test_record_candidates_without_incoming_edges_keep_their_own_properties(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    result = executor().run(**args, **hooks)
    assert {node.class_iri for node in result.graph.nodes} == {ROOT, A, B}, result.diagnostics
    assert {prop.raw_value for prop in result.graph.properties} == {"A", "B"}, result.events
    assert not result.graph.edges
    record_requests = [view for view in requests if "members" not in view]
    assert len(record_requests) == 2
    assert all("subject_ref" not in view and "predicate_iri" not in view
               for view in record_requests)
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert any(value["version"] == RECORD_PROTOCOL for value in calls["protocols"].values())
    assert result.graph.progress.model_calls == len(requests)


def test_late_endpoint_reopens_only_affected_relation_with_original_lineage_budget(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = record_setup(tmp_path, monkeypatch, current_run, split=True)
    result = executor().run(**args, **hooks)
    assert any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.events
    opportunities = [member for view in requests if "members" in view
                     for member in view["members"] if member["predicate_iri"] == EDGE]
    # No paid empty attempt before the component exists; one ready task proves the edge.
    assert len({member["task_id"] for member in opportunities}) == 1
    assert all(any(entity["class_iri"] == B for entity in member["registered_entities"])
               for member in opportunities)
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert max(calls["lineage_calls"].values()) <= 4
    assert result.graph.progress.model_calls == len(requests)


def test_missing_endpoints_spend_no_relation_calls_and_remain_unattempted(
    tmp_path, monkeypatch, current_run,
):
    def empty(view, payload, *, record):
        assert record, "a relation without any object endpoint must not call the model"
        return {key: [] for key in payload}

    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=empty,
    )
    result = executor().run(**args, **hooks)
    assert not result.graph.edges
    assert result.graph.progress.records_unattempted > 0
    assert result.graph.progress.completion == "incomplete"
    assert requests and all("members" not in request for request in requests)
    assert len(requests) == 1  # No new source gap: do not repeat the empty discovery either.
    store, run, _ = current_run
    work = current_state.restore_work(store, run, run.run_fingerprint).work_state
    rows = [row["value"] for row in work["record_relation_inputs"].values()]
    assert rows and all(row["status"] == "waiting_endpoints" for row in rows)
    entries = [row["value"]["value"] for row in work["plan_parts"].values()
               if row["name"] == "ledger"]
    assert all(entry["coverage_state"] == "unattempted" for entry in entries)
    assert all(entry["execution_state"] == "blocked_dependency" for entry in entries)
    assert not any(entry["semantic_outcomes"] for entry in entries)
    assert result.graph.progress.model_calls == len(requests)


def test_waiting_endpoint_survives_cold_continue_without_repeating_paid_discovery(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks = record_setup(tmp_path, monkeypatch, current_run, split=True)
    stopped = False
    persist = hooks["work_hook"]

    def work(changes):
        nonlocal stopped
        persist(changes)
        stopped = stopped or "waiting_endpoints" in str(changes)

    paused = executor(progress_hook=lambda _: not stopped).run(
        **args, **{**hooks, "work_hook": work},
    )
    assert stopped and "execution_pause_requested" in paused.diagnostics
    paid_requests = deepcopy(requests)
    assert paid_requests
    store, run, _ = current_run
    result = executor().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert any(edge.predicate_iri == EDGE for edge in result.graph.edges), result.diagnostics
    assert all(requests.count(request) == 1 for request in paid_requests)
    assert result.graph.progress.model_calls == len(requests)


@pytest.mark.parametrize("observation_kind", ["missing", "unbound"])
def test_source_located_missing_relation_observation_reaches_bounded_discovery_feedback(
    tmp_path, monkeypatch, current_run, observation_kind,
):
    def gap(view, payload, *, record):
        if not record and view["stage"] == "discovery" and view["predicate_iri"] == EDGE:
            unit = RecordIndex(args["ir"]).records[0].source_units[0]
            payload["relations"] = []
            payload["observations"] = [{
                "kind": observation_kind, "subject_id": view["subject_ref"]["id"],
                "predicate_iri": EDGE, "quote": {
                    "evidence_id": unit.evidence_id, "text": "部件乙", "context_text": TEXT,
                }, "reason": "原文对象尚未正确绑定，需核对已有实体与此提及",
            }]
        return payload

    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=gap,
    )
    result = executor().run(**args, **hooks)
    discoveries = [request for request in requests
                   if "members" not in request and request["stage"] == "discovery"]
    assert len(discoveries) == 2
    assert any(item.get("kind") == "record_feedback"
               for item in discoveries[1].get("feedback", []))
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    records = [p for p in calls["protocols"].values() if p["version"] == RECORD_PROTOCOL]
    assert len(records) == 1 and records[0]["request_attempt"] == 4
    assert result.graph.progress.model_calls == len(requests)


@pytest.mark.parametrize("pause_stage", ["discovery", "verification"])
def test_cold_continue_reuses_confirmed_record_response(
    tmp_path, monkeypatch, current_run, pause_stage,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    stopped = False
    persist = hooks["model_call_hook"]
    def calls(state):
        nonlocal stopped
        persist(state)
        if any(row["field"] == "model_turn" and row["value"]["stage"] == pause_stage
               for row in state.get("result_changes", {}).values()):
            stopped = True
    first = executor(progress_hook=lambda _: not stopped).run(
        **args, **{**hooks, "model_call_hook": calls},
    )
    assert "execution_pause_requested" in first.diagnostics
    store, run, _ = current_run
    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    result = executor().run(
        **args, **hooks, resume_state=restored,
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert {prop.raw_value for prop in result.graph.properties} == {"A", "B"}, result.events
    assert len([view for view in requests if "members" not in view]) == 2


@pytest.mark.parametrize("recover_missing", [True, False])
def test_anchored_discovery_gap_reopens_record_once_with_same_budget(
    tmp_path, monkeypatch, current_run, recover_missing,
):
    generations = []
    def transform(view, payload, *, record):
        if view["stage"] != "discovery":
            return payload
        if record:
            generations.append(view)
            if len(generations) == 1 or not recover_missing:
                payload["entities"] = [e for e in payload["entities"] if e["class_iri"] != B]
                payload["properties"] = [p for p in payload["properties"] if p["local_id"] != "pB"]
                evidence_id = RecordIndex(args["ir"]).records[0].source_units[0].evidence_id
                payload["observations"] = [{"kind": "unbound", "subject_id": None,
                    "predicate_iri": None, "quote": {"evidence_id": evidence_id,
                    "text": "部件乙", "context_text": TEXT},
                    "reason": "原文对象尚未登记，需核对类型和指称"}]
        elif view["predicate_iri"] == EDGE and not payload["relations"]:
            evidence_id = RecordIndex(args["ir"]).records[0].source_units[0].evidence_id
            payload["observations"] = [{"kind": "unbound",
                "subject_id": view["subject_ref"]["id"], "predicate_iri": EDGE, "quote": {
                "evidence_id": evidence_id, "text": "部件乙", "context_text": TEXT,
            }, "reason": "关系对象尚未登记"}]
        return payload
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    result = executor().run(**args, **hooks)
    assert len(generations) == 2, result.events
    assert bool(result.graph.edges) == recover_missing, result.events
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    protocols = [p for p in calls["protocols"].values() if p["version"] == RECORD_PROTOCOL]
    assert len(protocols) == 1
    assert protocols[0]["assertion_generation"] == 2
    assert len(protocols[0]["completed_attempts"]) == 4
    assert max(calls["lineage_calls"].values()) <= 4
    assert result.graph.progress.model_calls_reserved == len(requests)


@pytest.mark.parametrize("stage", ["discovery", "verification"])
def test_record_unknown_request_retains_cost_and_incomplete_status(
    tmp_path, monkeypatch, current_run, stage,
):
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, empty_relations=True,
    )
    from app.services.llm import local_client
    transport = local_client.responses_create
    def unavailable(*args, **kwargs):
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        if view["stage"] == stage:
            raise RuntimeError("connection lost after send")
        return transport(*args, **kwargs)
    monkeypatch.setattr(local_client, "responses_create", unavailable)
    result = executor().run(**args, **hooks)
    assert result.graph.progress.stop_reason == "model_calls_unresolved"
    assert result.graph.progress.model_calls_reserved == (1 if stage == "discovery" else 2)
    assert result.graph.progress.completion == "incomplete"
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert any(p["pending_request"] for p in calls["protocols"].values())
    assert not result.graph.edges


@pytest.mark.parametrize("budget", [3, 4])
def test_relation_pages_visit_optional_endpoints_and_keep_unfinished_budget_visible(
    tmp_path, monkeypatch, current_run, budget,
):
    second = "装置甲连接部件乙、部件丙、部件丁。"
    def transform(view, payload, *, record):
        if view["stage"] != "discovery":
            return payload
        if record:
            second_record_ref = project_reference_payload({
                "record_id": RecordIndex(args["ir"]).records[1].record_id,
            })["record_id"]
            if view["record_id"] == second_record_ref:
                return {key: [] for key in payload}
            for identity, name in (("c", "部件丙"), ("d", "部件丁")):
                template = deepcopy(next(e for e in payload["entities"] if e["class_iri"] == B))
                template.update(local_id=identity)
                template["mentions"][0].update(text=name, context_text=None)
                payload["entities"].append(template)
        else:
            payload["relations"] = []
        return payload
    args, executor, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
        texts=[TEXT + "部件丙与部件丁也是部件。", second], page_size=2,
    )
    result = executor(max_model_calls_per_record=budget).run(**args, **hooks)
    record_id = RecordIndex(args["ir"]).records[1].record_id
    store, run, _ = current_run
    restored = vars(current_state.restore_work(store, run, run.run_fingerprint))
    rows = [row["value"] for row in restored["work_state"]["record_relation_inputs"].values()
            if row["value"]["task"]["predicate_iri"] == EDGE
            and row["value"]["task"]["record_id"] == record_id]
    assert len(rows) == 1
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    task_ids = {task["task_id"] for protocol in calls["protocols"].values()
                for task in protocol.get("work_unit", {}).get("members", [])
                if task["record_id"] == record_id and task["predicate_iri"] == EDGE}
    task_refs = set(project_reference_payload({"task_ids": sorted(task_ids)})["task_ids"])
    turns = [member for view in requests if view["stage"] == "discovery"
             for member in view.get("members", [])
             if member["task_id"] in task_refs]
    assert len(turns) == (2 if budget == 4 else 1), result.diagnostics
    if budget == 4:
        authorized = {e["entity_ref"]["id"] for member in turns
                      for e in member["registered_entities"] if e["class_iri"] == B}
        expected_refs = project_reference_payload({
            "entity_ids": [n.entity_id for n in result.graph.nodes if n.class_iri == B],
        })["entity_ids"]
        assert authorized == set(expected_refs)
        assert rows[0]["status"] == "examined"
        assert not rows[0]["remaining_pages"]
    else:
        assert rows[0]["status"] == "incomplete"
        assert result.graph.progress.completion == "incomplete"
        assert result.graph.progress.records_incomplete > 0
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert max(calls["lineage_calls"].values()) <= budget


@pytest.mark.parametrize("invalid", ["entity", "property", "root_bridge", "foreign_source"])
def test_independent_candidates_survive_other_claim_failure(
    tmp_path, monkeypatch, current_run, invalid,
):
    def transform(view, payload, *, record):
        if not record:
            return payload
        if view["stage"] == "discovery":
            if invalid == "root_bridge":
                payload["properties"][0]["bridge_kind"] = "document_subject_description"
            if invalid == "foreign_source":
                payload["properties"][0]["value_quote"]["evidence_id"] = "foreign-source"
        elif invalid in {"entity", "property"}:
            targets = {t["target_id"]: t for t in view["verification_input"]["targets"]}
            for verification in payload["verifications"]:
                target = targets[verification["target_id"]]
                if (target["target_kind"] == invalid
                        and target["payload"]["local_id"] in {"a", "pA"}):
                    for facet in verification["facets"]:
                        facet.update(verdict="unsupported", support=[],
                                     counterevidence_support=[], reason="主体或属性缺少独立证明")
        return payload
    args, executor, _, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform, empty_relations=True,
    )
    result = executor().run(**args, **hooks)
    assert any(n.class_iri == B and n.decision_status == "supported" for n in result.graph.nodes)
    assert any(p.raw_value == "B" and p.decision_status == "supported"
               for p in result.graph.properties)
    assert not any(p.raw_value == "A" and p.decision_status == "supported"
                   for p in result.graph.properties), result.events
    if invalid == "entity":
        assert not any(n.class_iri == A and n.decision_status == "supported"
                       for n in result.graph.nodes)
