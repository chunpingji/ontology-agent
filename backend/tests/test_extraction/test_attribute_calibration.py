"""Unresolved source observations survive and calibrate only with new evidence."""

from copy import deepcopy

from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.attribute_calibration import calibration_inputs
from app.services.extraction.ontology_guided.contracts import VersionedRef
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryTask
from tests.test_extraction.test_contextual_record_executor import setup_contextual
from tests.test_extraction.test_record_executor import EDGE, A, record_setup

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]


def test_missing_owner_keeps_raw_and_typed_candidate_without_fake_graph_property(
    tmp_path, monkeypatch, current_run,
):
    args, factory, requests, hooks = setup_contextual(
        tmp_path, monkeypatch, current_run, texts=["编号：00123"],
    )
    result = factory().run(**args, **hooks)
    assert result.graph.properties == []
    candidate, = result.graph.attribute_candidates
    assert candidate.raw_value == candidate.parsed_value.value == "00123"
    assert candidate.parsed_value.datatype_iri.endswith("#string")
    assert candidate.options == [] and candidate.reason_codes == ["attribute_subject_missing"]
    assert candidate.checks["shacl"] == "not_checked"
    store, run, _ = current_run
    work = vars(current_state.restore_work(store, run, run.run_fingerprint))
    assert any(row["value"].get("attribute_candidates")
               for row in work["work_state"]["record_discovery"].values())
    before = deepcopy(requests)
    resumed = factory().run(
        **args, **hooks, resume_state=work,
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert resumed.graph.attribute_candidates == result.graph.attribute_candidates
    assert requests == before


def test_related_relation_then_cold_resume_calibrates_and_retires_candidate(
    tmp_path, monkeypatch, current_run,
):
    calibrating = False

    def transform(view, payload, *, record):
        nonlocal calibrating
        if record and view["stage"] == "discovery":
            calibrating = any(item.get("kind") == "attribute_calibration"
                              for item in view.get("feedback", []))
        if record and view["stage"] == "verification" and not calibrating:
            targets = {target["target_id"]: target
                       for target in view["verification_input"]["targets"]}
            for verification in payload["verifications"]:
                target = targets[verification["target_id"]]
                if target["target_kind"] == "property":
                    for facet in verification["facets"]:
                        facet["verdict"] = "undetermined"
        return payload

    args, factory, requests, hooks = record_setup(
        tmp_path, monkeypatch, current_run, transform=transform,
    )
    paused = False
    save_batch = hooks["batch_hook"]

    def batch(value):
        nonlocal paused
        save_batch(value)
        if (any(edge.predicate_iri == EDGE for edge in value.graph.edges)
                and value.graph.attribute_candidates):
            paused = True

    first = factory(progress_hook=lambda _: not paused).run(
        **args, **{**hooks, "batch_hook": batch},
    )
    assert any(edge.predicate_iri == EDGE for edge in first.graph.edges)
    assert first.graph.attribute_candidates and first.graph.properties == []
    assert all(candidate.parsed_value.value in {"A", "B"}
               for candidate in first.graph.attribute_candidates)
    store, run, _ = current_run
    calls = current_state.restore_calls(store, run, run.run_fingerprint)
    work = vars(current_state.restore_work(store, run, run.run_fingerprint))
    paid = deepcopy(requests)
    result = factory().run(**args, **hooks, resume_state=work, model_call_state=calls)
    assert not result.graph.attribute_candidates
    assert len(result.graph.properties) == 2
    assert all(requests.count(request) == paid.count(request) for request in paid)
    calibration = [entry for view in requests for entry in view.get("feedback", [])
                   if entry.get("kind") == "attribute_calibration"]
    assert calibration and any(relation["predicate_iri"] == EDGE
                               for item in calibration for relation in item["related_relations"])
    before = len(requests)
    factory().run(
        **args, **hooks,
        resume_state=vars(current_state.restore_work(store, run, run.run_fingerprint)),
        model_call_state=current_state.restore_calls(store, run, run.run_fingerprint),
    )
    assert len(requests) == before


def test_calibration_signature_ignores_unproved_or_unrelated_relations(
    tmp_path, monkeypatch, current_run,
):
    args, factory, _, hooks = record_setup(tmp_path, monkeypatch, current_run)
    result = factory().run(**args, **hooks)
    runner = factory()
    index = runner.adapter.index
    node = next(node for node in result.graph.nodes if node.class_iri == A)
    edge = next(edge for edge in result.graph.edges if edge.predicate_iri == EDGE)
    task = RecordDiscoveryTask.create(
        run_fingerprint=args["run_fingerprint"], record_id=index.records[0].record_id,
        schema_card_id="card", analysis_scope_ref="scope", dependency_hash="a" * 64,
    )
    deps = {"entity_dependencies": [{
        "entity_ref": {"id": node.entity_id, "revision": node.revision},
        "source_refs": [ref.model_dump(mode="json") for ref in node.evidence_refs],
    }]}
    empty = calibration_inputs(task, [], deps, [], index, is_valid=lambda _: True)
    invalid = calibration_inputs(task, [], deps, [edge], index, is_valid=lambda _: False)
    assert empty == invalid
    unrelated = edge.model_copy(update={
        "subject_ref": VersionedRef(id="another", revision=1),
        "object_ref": VersionedRef(id="different", revision=1),
    })
    assert calibration_inputs(task, [], deps, [unrelated], index,
                              is_valid=lambda _: True) == empty
    changed = calibration_inputs(task, [], deps, [edge], index, is_valid=lambda _: True)
    assert changed[0] != empty[0] and changed[1] and changed[2]
