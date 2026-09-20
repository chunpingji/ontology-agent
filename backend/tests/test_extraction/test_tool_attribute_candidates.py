"""Verified entity sources remain local candidates for inherited tool attributes."""

import json
from functools import partial

import pytest

from app.models.document_analysis import DocumentRunCurrentState
from app.services.document_analysis import current_state
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.contracts import EdgeSpec, OntologySnapshot, SlotSpec
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
from app.services.extraction.ontology_guided.recognition_batch import RecognitionBatchPolicy
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_layered_recognition import ROOT, arguments
from tests.test_extraction.test_ontology_guided_core import _definition

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

PATHWAY = "urn:test:DegradationPathway"
OXIDATIVE = "urn:test:OxidativeDegradation"
HAS_PATHWAY = "urn:test:hasDegradationPathway"
ATTRIBUTES = {
    "urn:test:degradationCondition": ("降解条件", "string"),
    "urn:test:degradationPercent": ("降解百分比（%）", "decimal"),
    "urn:test:majorDegradant": ("主要降解杂质", "string"),
}
SOURCE = "强制降解试验，氧化降解：3%H₂O₂，室温/1h，降解约8%，主要杂质为Imp-I"


def setup(tmp_path, monkeypatch, current_run, *, batching=False, rejected=False, extra_lines=()):
    store, run, token = current_run
    args = arguments(tmp_path, [SOURCE, *extra_lines])
    args.update(recognition_run_id=str(run.recognition_run_id), run_fingerprint=run.run_fingerprint)
    run.document_hash = args["ir"].document_hash
    store.db.commit()
    classes = {
        ROOT: _definition(ROOT, "报告", relationships=[EdgeSpec(
            iri=HAS_PATHWAY, label="强制降解试验", range_class_iris=[PATHWAY],
        )]),
        PATHWAY: _definition(PATHWAY, "降解途径", properties=[SlotSpec(
            iri=iri, label=label, declared_by=[PATHWAY],
            datatype_iris=["http://www.w3.org/2001/XMLSchema#" + datatype],
        ) for iri, (label, datatype) in ATTRIBUTES.items()]),
        OXIDATIVE: _definition(OXIDATIVE, "氧化降解", parents=[PATHWAY]),
    }
    ontology = OntologySnapshot(
        snapshot_id="attribute-sources", ontology_hash=evidence_hash(classes),
        classes=classes, created_from="frozen_fixture",
    )
    index = RecordIndex(args["ir"])
    record = index.records[0]
    source = record.source_units[0]
    requests = []

    def quote(text):
        return {"evidence_id": source.evidence_id, "text": text, "context_text": None}

    def transport(_client, **kwargs):
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(view)
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            output = [{
                "type": "function_call", "id": f"item-{position}",
                "call_id": f"check-{position}", "name": "validate_graph",
                "arguments": json.dumps({
                    **(check if batching else {"claim_id": check}),
                    "shape_profile_id": required["shape_profile_id"],
                }),
            } for position, check in enumerate(required["required_relation_checks"])]
        else:
            answers = []
            for member in view.get("members", [view]):
                if view["stage"] == "discovery":
                    answer = dict(entities=[], properties=[], relations=[], external_links=[],
                                  observations=[], reference_bindings=[])
                    if member["predicate_iri"] == HAS_PATHWAY:
                        answer["entities"] = [{
                            "local_id": "oxidative", "class_iri": OXIDATIVE,
                            "representation": "mention", "mentions": [quote("氧化降解")],
                            "record_components": [], "identifier_claims": [],
                        }]
                        answer["relations"] = [{
                            "local_id": "pathway", "subject_id": member["subject_ref"]["id"],
                            "predicate_iri": HAS_PATHWAY, "object_ids": ["oxidative"],
                            "selection": "all", "bridge_support": [quote(SOURCE)],
                            "selection_support": [],
                            "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                                           "condition_support": [], "scope_qualifiers": []},
                            "bridge_kind": "document_subject_description", "bridge_ref_ids": [],
                            "source_assertion": {
                                "subject_support": [],
                                "object_support": [{"object_id": "oxidative",
                                                    "support": [quote("氧化降解")]}],
                                "predicate_support": [quote(SOURCE)], "binding_ids": [],
                            },
                        }]
                else:
                    answer = {"verifications": [{
                        "target_id": target["target_id"], "content_hash": target["content_hash"],
                        "facets": [{
                            "name": facet, "verdict": "unsupported" if rejected else "supported",
                            "support": [quote(SOURCE)], "counterevidence_support": [],
                            "reason": "受控核验结果",
                        } for facet in target["required_facets"]],
                    } for target in member["verification_input"]["targets"]]}
                answers.append({"task_id": member.get("task_id"), "result": answer})
            answer = {"members": answers} if batching else answers[0]["result"]
            output = [{"type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": json.dumps(answer)}]}]
        return ResponseTurn(
            response_id=f"response-{len(requests)}", response_status="completed",
            output_items=output, incomplete_details=None, error=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    adapter = ToolModelRecognitionAdapter(
        object(), index=index, ontology=ontology, metadata=args["metadata"],
        profile=ExtractionProfile(), token_counter=len, model_identity="candidate-test",
        max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(20000),
        recognition_batching=RecognitionBatchPolicy() if batching else None,
        reference_resolution=True,
    )

    def executor(**kwargs):
        return OntologyGuidedExecutor(
            ontology=ontology, engine=None, adapter=adapter, current_state=True,
            incremental_performance=True, candidate_policy="sparse-candidates-v1", max_tasks=4,
            heuristic_policy=HeuristicSearchPolicy.durable(
                incremental=True, initial_page_size=1, expanded_page_size=1,
            ), **kwargs,
        )

    def calls(state):
        if state.get("work_changes"):
            state = {**state, "expected_work_version": run.work_version}
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)

    def work(changes):
        current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        )

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
    return args, executor, requests, hooks, record


@pytest.mark.parametrize("batching", [False, True])
def test_verified_source_reaches_all_inherited_attributes_before_semantic_search(
    tmp_path, monkeypatch, current_run, batching,
):
    args, executor, requests, hooks, record = setup(
        tmp_path, monkeypatch, current_run, batching=batching,
    )
    result = executor().run(**args, **hooks)
    assert any(edge.decision_status == "supported" for edge in result.graph.edges)
    admissions = [value for kind, value in result.events
                  if kind == "heuristic_admission" and value["predicate_iri"] in ATTRIBUTES]
    assert {value["predicate_iri"] for value in admissions} == set(ATTRIBUTES)
    assert all(value["stage"] == "H0" and value["record_ids"] == [record.record_id]
               for value in admissions)
    attribute_requests = [view for view in requests if view["stage"] == "discovery"
                          and any(member["predicate_iri"] in ATTRIBUTES
                                  for member in view.get("members", [view]))]
    assert len(attribute_requests) == (1 if batching else 3)
    assert {member["predicate_iri"] for view in attribute_requests
            for member in view.get("members", [view])} == set(ATTRIBUTES)
    # Admission supplies the full quote; an empty model answer never becomes a value.
    assert all("3%H₂O₂" in json.dumps(view, ensure_ascii=False)
               and "约8%" in json.dumps(view, ensure_ascii=False) for view in attribute_requests)
    assert result.graph.properties == []


def test_same_name_elsewhere_is_not_a_seed_and_current_seeds_survive_cold_resume(
    tmp_path, monkeypatch, current_run,
):
    args, executor, requests, hooks, record = setup(
        tmp_path, monkeypatch, current_run, batching=True,
        extra_lines=["另一批次氧化降解：降解条件不同，降解百分比（%）为99%，主要降解杂质为Imp-X。"],
    )
    paused = False
    persist = hooks["batch_hook"]

    def pause_after_relation(value):
        nonlocal paused
        persist(value)
        paused = True

    executor(progress_hook=lambda _stage: not paused).run(
        **args, **{**hooks, "batch_hook": pause_after_relation},
    )
    store, run, _token = current_run
    rows = current_state.read_rows(store, run, DocumentRunCurrentState)
    searches = [row["value"] for row in rows["work:searches"].values()
                if row["key"][-1] in ATTRIBUTES]
    assert len(searches) == 3
    assert all(row["seed_record_ids"] == row["priority_record_ids"] == [record.record_id]
               for row in searches)
    state = current_state.restore_work(store, run, run.run_fingerprint).work_state
    control = state["control"]["current"]
    paid = len(requests)
    resumed = executor().run(
        **args, **hooks,
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
        resume_state={"work_state": state, "frontier": control["frontier_policy"],
                      "diagnostics": control["diagnostics"]},
    )
    assert any(member["predicate_iri"] in ATTRIBUTES for view in requests[paid:]
               for member in view.get("members", [view]))
    assert not any("mismatch" in item for item in resumed.diagnostics)


def test_unverified_entity_cannot_seed_attribute_work(tmp_path, monkeypatch, current_run):
    args, executor, requests, hooks, _record = setup(
        tmp_path, monkeypatch, current_run, batching=True, rejected=True,
    )
    result = executor().run(**args, **hooks)
    assert not any(member["predicate_iri"] in ATTRIBUTES for view in requests
                   for member in view.get("members", [view]))
    assert not any(kind == "heuristic_admission" and value["predicate_iri"] in ATTRIBUTES
                   for kind, value in result.events)
    assert result.graph.properties == []
