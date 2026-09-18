"""Scoped execution and cold continuation through the real coordinator/worker boundary."""

import json
from copy import deepcopy
from threading import get_ident

import pytest

from app.models.document_analysis import DocumentRunRequest
from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import (
    ExternalCandidate,
    ExtractionProfile,
    IdentityKeySpec,
)
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    OntologySnapshot,
    SlotSpec,
    VersionedRef,
)
from app.services.extraction.ontology_guided.executor import (
    ModelCallPersistenceFailure,
    OntologyGuidedExecutor,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_layered_recognition import ROOT, arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

CHILD = "urn:tool:Child"
LINK = "urn:tool:links"


def setup(tmp_path, monkeypatch, current_run, *, polarity="affirmed", identity_links=False):
    store, run, _token = current_run
    owner_thread = get_ident()
    args = arguments(tmp_path, ["报告关联对象乙或对象丙，恰选其一。"])
    args.update(recognition_run_id=str(run.recognition_run_id), run_fingerprint=run.run_fingerprint)
    run.document_hash = args["ir"].document_hash
    store.db.commit()
    classes = {
        ROOT: _definition(ROOT, "报告", relationships=[EdgeSpec(
            iri=LINK, label="关联", range_class_iris=[CHILD],
        )]),
        CHILD: _definition(CHILD, "对象", properties=[SlotSpec(iri="urn:value", label="属性")]),
    }
    ontology = OntologySnapshot(snapshot_id="tool-test", ontology_hash=evidence_hash(classes),
                                classes=classes, created_from="frozen_fixture")
    index = RecordIndex(args["ir"])
    unit = index.records[0].source_units[0]
    requests, contexts = [], []

    def quote(text):
        return {"evidence_id": unit.evidence_id, "text": text, "context_text": None}

    def transport(_client, **kwargs):
        assert get_ident() != owner_thread
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            return ResponseTurn(
                response_id=f"response-{len(requests)}", response_status="completed",
                output_items=[{
                    "type": "function_call", "id": f"item-{i}", "call_id": f"check-{i}",
                    "name": "validate_graph", "arguments": json.dumps({
                        "claim_id": identity, "shape_profile_id": required["shape_profile_id"],
                    }),
                } for i, identity in enumerate(required["required_relation_checks"])],
                incomplete_details=None, error=None, usage={"input_tokens": 10, "output_tokens": 5},
            )
        if view["stage"] == "discovery":
            answer = {"entities": [], "properties": [], "relations": [],
                      "external_links": [], "observations": []}
            if view["predicate_iri"] == LINK:
                answer["entities"] = [{
                    "local_id": key, "class_iri": CHILD, "representation": "mention",
                    "mentions": [quote(label)], "record_components": [], "identifier_claims": [],
                } for key, label in (("b", "对象乙"), ("c", "对象丙"))]
                if identity_links:
                    for entity in answer["entities"]:
                        entity["identifier_claims"] = [{
                            "predicate_iri": "urn:key", "value_quote": entity["mentions"][0],
                        }]
                answer["relations"] = [{
                    "local_id": "r", "subject_id": view["subject_ref"]["id"],
                    "predicate_iri": LINK, "object_ids": ["b", "c"], "selection": "one_of",
                    "bridge_support": [quote(unit.text)],
                    "selection_support": [quote("对象乙或对象丙，恰选其一")],
                    "qualifiers": {"polarity": polarity, "modality": "asserted",
                                   "condition_support": [], "scope_qualifiers": []},
                    "bridge_kind": "explicit_assertion", "bridge_ref_ids": [],
                }]
            elif identity_links:
                subject = view["subject_ref"]["id"]
                answer["external_links"] = [{
                    "local_id": "archive-link", "subject_id": subject,
                    "external_candidate_id": f"archive:{subject}",
                    "identity_support": [quote(unit.text)],
                }]
        else:
            answer = {"verifications": [{
                "target_id": target["target_id"], "content_hash": target["content_hash"],
                "facets": [{"name": facet, "verdict": "supported", "support": [quote(unit.text)],
                            "counterevidence_support": [], "reason": "原文支持"}
                           for facet in target["required_facets"]],
            } for target in view["verification_input"]["targets"]]}
        return ResponseTurn(
            response_id=f"response-{len(requests)}", response_status="completed",
            output_items=[{"type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": json.dumps(answer)}]}],
            incomplete_details=None, error=None, usage={"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(), index=index, ontology=ontology, metadata=args["metadata"],
        profile=ExtractionProfile(identity_keys=[IdentityKeySpec(
            class_iri=CHILD, property_iris=["urn:key"], namespace="urn:archive",
            scope="dataset", declaration_ref="test-identity-key",
        )] if identity_links else []), token_counter=len, model_identity="controlled-qwen",
        max_input_tokens=1000000, max_output_tokens=2000, tool_limits=ToolLimits(20000),
    )
    inspect = adapter.inspect

    def inspect_context(task, context, predicate, menu):
        contexts.append((task, deepcopy(context.tool_inputs)))
        return inspect(task, context, predicate, menu)

    monkeypatch.setattr(adapter, "inspect", inspect_context)
    if identity_links:
        def external_candidates(context, _protocol):
            subject = context.tool_inputs["subject_node"]
            if subject["root"]:
                return []
            return [ExternalCandidate(
                candidate_id=f"archive:{subject['entity_id']}", source_id="test-archive",
                system="mock", dataset="objects", record_key=subject["entity_id"],
                record_version="v1", class_iri=CHILD,
                matches=[dict(predicate_iri="urn:key", document_quote=quote(subject["label"]),
                              record_field="code", record_value=subject["label"],
                              match_kind="exact_key")],
                mapped_fields=[dict(predicate_iri="urn:key", raw_value=subject["label"],
                                    datatype_iri="http://www.w3.org/2001/XMLSchema#string")],
                metadata=[],
            )]
        monkeypatch.setattr(adapter, "_external_candidates", external_candidates)

    def executor(**kwargs):
        return OntologyGuidedExecutor(ontology=ontology, engine=None, adapter=adapter,
                                      current_state=True, max_hops=2, **kwargs)

    def load(lineage, reference, field):
        assert get_ident() == owner_thread
        return current_state.load_protocol_result(store, run, lineage, reference, field)

    return args, executor, requests, contexts, load


@pytest.mark.parametrize("tamper", [
    None, "decision_revision", "decision_target", "decision_facet", "decision_support",
    "provenance", "type", "referent", "root", "root_origin",
])
def test_identity_append_preserves_entity_and_requires_exact_finalized_target(
    tmp_path, monkeypatch, current_run, tamper,
):
    args, executor, _requests, _contexts, load = setup(
        tmp_path, monkeypatch, current_run, identity_links=True,
    )
    store, run, token = current_run
    runner = executor()
    inspect = runner.adapter.inspect
    originals, enriched = {}, []

    def inspect_and_edit(task, context, predicate, menu):
        result = inspect(task, context, predicate, menu)
        if task.subject.is_document_root:
            originals.update({node.entity_id: node.model_copy(deep=True) for node in result.nodes})
            return result
        assert result.nodes, result.reason
        node = result.nodes[0]
        assert node.identity_status == "verified", result.reason
        enriched.append(node.entity_id)
        if tamper == "decision_revision":
            node.identity_decision_refs[0] = node.identity_decision_refs[0].model_copy(
                update={"revision": 2},
            )
        elif tamper == "decision_target":
            result.decision_payloads[0]["target_id"] = "another-entity-link-target"
        elif tamper == "decision_facet":
            result.decision_payloads[0]["check_kind"] = "type"
        elif tamper == "decision_support":
            result.decision_payloads[0]["support_refs"] = []
        elif tamper == "provenance":
            node.external_provenance[0].record_key = "another-external-record"
        elif tamper == "type":
            node.class_iri = ROOT
        elif tamper == "referent":
            node.referent_ref = VersionedRef(id="another-referent", revision=1)
        elif tamper == "root":
            result.nodes[0] = node.model_copy(update={
                "root": True, "root_origin": "user_specified", "grounding_kind": "document_root",
            })
        elif tamper == "root_origin":
            node.root_origin = "user_specified"
        return result

    monkeypatch.setattr(runner.adapter, "inspect", inspect_and_edit)
    result = runner.run(
        **args, protocol_result_loader=load,
        model_call_hook=lambda state: current_state.persist_calls(
            store, run, token, run.run_fingerprint, state,
        ),
        work_hook=lambda changes: current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        ),
        batch_hook=lambda batch: current_state.persist_batch(
            store, run, token, batch=batch, fingerprint=run.run_fingerprint,
            ontology=runner.ontology, ir=args["ir"], metadata=args["metadata"],
            index=RecordIndex(args["ir"]), expected_version=run.work_version,
        ),
    )
    assert len(enriched) == 2 and len(set(enriched)) == 2
    assert len(result.graph.relationship_groups) == 1
    root = next(node for node in result.graph.nodes if node.root)
    assert root.entity_id == result.graph.root_ref.id and root.class_iri == ROOT
    for node in result.graph.nodes:
        if node.root:
            continue
        original = originals[node.entity_id]
        if tamper is None:
            assert node.revision == original.revision == 1
            assert node.identity_status == "verified"
            assert len(node.identity_decision_refs) == 3 and len(node.external_provenance) == 1
            fields = {"identity_status", "identity_decision_refs", "external_provenance"}
            assert node.model_dump(exclude=fields) == original.model_dump(exclude=fields)
        else:
            assert node == original
            assert "adapter_node_revision_conflict" in result.diagnostics or (
                "adapter_document_root_proposal_rejected" in result.diagnostics
            )
    display = current_state.read_display(store, run)
    expected = [node.model_dump(mode="json") for node in result.graph.nodes]
    assert display["graph"]["nodes"] == expected
    store.db.expire_all()
    restored = current_state.restore_work(store, run, run.run_fingerprint).work_state
    assert sorted((row["value"] for row in restored["nodes"].values()),
                  key=lambda node: node["entity_id"]) == sorted(
        expected, key=lambda node: node["entity_id"],
    )


@pytest.mark.parametrize("pause", [None, "pending_request", "model_turn", "root_batch"])
@pytest.mark.parametrize("sparse", [False, True])
def test_group_scope_and_exact_entity_source_survive_cold_pause(
    tmp_path, monkeypatch, current_run, pause, sparse,
):
    store, run, token = current_run
    args, executor, requests, contexts, load = setup(tmp_path, monkeypatch, current_run)
    if sparse:
        from functools import partial

        from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy

        executor = partial(
            executor, candidate_policy="sparse-candidates-v1", incremental_performance=True,
            heuristic_policy=HeuristicSearchPolicy.generic(),
        )
    pause_now = False

    def calls(state):
        nonlocal pause_now
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)
        if pause == "model_turn" and any(
            r["field"] == "model_turn" for r in state.get("result_changes", {}).values()
        ):
            pause_now = True
        if pause == "pending_request" and any(
            row["value"]["pending_request"] for row in state.get("protocols", {}).values()
        ):
            pause_now = True

    def work(changes):
        current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        )

    def batch(value):
        nonlocal pause_now
        current_state.persist_batch(
            store, run, token, batch=value, fingerprint=run.run_fingerprint,
            ontology=executor().ontology, ir=args["ir"], metadata=args["metadata"],
            index=RecordIndex(args["ir"]), expected_version=run.work_version,
        )
        if pause == "root_batch" and value.outcome.relationship_groups:
            pause_now = True

    hooks = dict(model_call_hook=calls, protocol_result_loader=load,
                 work_hook=work, batch_hook=batch)
    result = executor(progress_hook=lambda _stage: not pause_now).run(**args, **hooks)
    if pause:
        assert len(requests) == (3 if pause == "root_batch" else 1)
        assert not result.graph.progress.model_calls_unresolved
        assert result.graph.artifact_status == "partial"
        rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
        control = rows["control"]["current"]
        result = executor().run(
            **args, **hooks, model_call_state=current_state.restore_calls(
                store, run, run.run_fingerprint,
            ), resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                             "diagnostics": control["diagnostics"]},
        )
    assert len([r for r in requests if r["predicate_iri"] == LINK]) == 3
    assert len(result.graph.relationship_groups) == 1
    assert result.graph.edges == []
    assert result.graph.projection == "verified"
    group = result.graph.relationship_groups[0]
    assert group.selection == "one_of"
    children = [(task, values) for task, values in contexts if not task.subject.is_document_root]
    assert len(children) == 2, [value["outcome"] for kind, value in result.events
                                if kind == "task_outcome"]
    assert len({task.scope.scope_id for task, _values in children}) == 2
    for task, values in children:
        assert task.scope.members[0].relation_ref.id == group.candidate_id
        entity = next(v for v in values["entity_dependencies"]
                      if v["entity_ref"]["id"] == task.subject.entity_id)
        assert entity["proposal"]["mentions"]
        assert entity["dependency_refs"]
        assert values["scope_resolutions"][0]["steps"][0]["selection"] == "one_of"
    assert len({item.scope.scope_id for item in result.graph.coverage if item.scope}) == 3
    assert not result.graph.progress.model_calls_unresolved
    display = current_state.read_display(store, run)
    assert display["graph"]["relationship_groups"] == [group.model_dump(mode="json")]
    assert len({item["scope"]["scope_id"] for item in display["graph"]["coverage"]}) == 3


def test_verified_negative_group_does_not_expand_member_tasks(tmp_path, monkeypatch, current_run):
    store, run, token = current_run
    args, executor, _requests, contexts, load = setup(
        tmp_path, monkeypatch, current_run, polarity="negated",
    )
    result = executor().run(
        **args, protocol_result_loader=load,
        model_call_hook=lambda state: current_state.persist_calls(
            store, run, token, run.run_fingerprint, state,
        ),
    )
    assert len(result.graph.relationship_groups) == 1
    assert result.graph.relationship_groups[0].polarity == "negated"
    assert all(task.subject.is_document_root for task, _values in contexts)
    assert result.graph.progress.pending_frontiers == 0


@pytest.mark.parametrize("failure", ["reservation_hook", "reservation_transaction", "budget"])
def test_unreserved_request_does_not_become_unknown_on_cold_continue(
    tmp_path, monkeypatch, current_run, failure,
):
    store, run, token = current_run
    args, executor, requests, _contexts, load = setup(tmp_path, monkeypatch, current_run)
    first_executor = executor()
    rejected_hashes = []
    put_rows = current_state.put_rows

    def fail_transaction(*values, **kwargs):
        if values[3] == "calls:protocols":
            pending = next((row["value"]["pending_request"] for row in values[4].values()
                            if row["value"]["pending_request"] is not None), None)
            if pending is not None and not rejected_hashes:
                assert current_state.get_row(
                    store, run, "calls:requests", pending["reservation_key"],
                    model=DocumentRunRequest,
                ) is not None
                rejected_hashes.append(pending["request_hash"])
                raise RuntimeError("injected reservation transaction failure")
        return put_rows(*values, **kwargs)

    def calls(state):
        if failure == "reservation_hook" and state["reservations"] and not rejected_hashes:
            rejected_hashes.append(state["reservations"][0]["input_hash"])
            raise RuntimeError("injected reservation hook failure")
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)

    if failure == "reservation_transaction":
        monkeypatch.setattr(current_state, "put_rows", fail_transaction)
    if failure == "budget":
        inspect = first_executor.adapter.inspect

        def close_budget_after_context(*values):
            first_executor.max_model_calls_per_record = 0
            return inspect(*values)

        monkeypatch.setattr(first_executor.adapter, "inspect", close_budget_after_context)

    def work(changes):
        current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        )

    def batch(value):
        current_state.persist_batch(
            store, run, token, batch=value, fingerprint=run.run_fingerprint,
            ontology=first_executor.ontology, ir=args["ir"], metadata=args["metadata"],
            index=RecordIndex(args["ir"]), expected_version=run.work_version,
        )

    hooks = dict(model_call_hook=calls, protocol_result_loader=load,
                 work_hook=work, batch_hook=batch)
    if failure == "budget":
        result = first_executor.run(**args, **hooks)
        assert result.graph.artifact_status == "partial"
        monkeypatch.setattr(first_executor.adapter, "inspect", inspect)
    else:
        with pytest.raises(ModelCallPersistenceFailure):
            first_executor.run(**args, **hooks)
    assert not requests
    restored_calls = current_state.restore_calls(store, run, run.run_fingerprint)
    assert not restored_calls["reservations"]
    assert not current_state.read_rows(
        store, run, DocumentRunRequest, prefix="calls:requests",
    )
    assert not any(restored_calls["lineage_calls"].values())
    assert all(state["pending_request"] is None and state["request_attempt"] == 0
               for state in restored_calls["protocols"].values())
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = rows["control"]["current"]
    result = executor().run(
        **args, **hooks, model_call_state=restored_calls,
        resume_state={"work_state": rows, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert len(result.graph.relationship_groups) == 1
    receipts = current_state.read_rows(
        store, run, DocumentRunRequest, prefix="calls:requests",
    )["calls:requests"].values()
    first = min((row["reservation"] for row in receipts), key=lambda row: row["sequence"])
    assert first["protocol_attempt"] == 1
    if rejected_hashes:
        assert first["input_hash"] == rejected_hashes[0]
    assert not result.graph.progress.model_calls_unresolved
