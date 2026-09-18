"""Cross-task entity reuse and cold continuation through the real coordinator."""

import json
from copy import deepcopy
from threading import get_ident

import pytest

from app.services.document_analysis import current_state
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction import test_tool_engine_execution as execution_fixture
from tests.test_extraction.test_layered_recognition import arguments

pytest_plugins = ["tests.test_extraction.test_tool_engine_resume"]

LINES = [
    "本报告的工艺产物为 HRS-9267 粗品。",
    "本报告同一工艺产物 HRS-9267粗品 的收率见本行。",
    "本报告同一工艺产物 HRS-9267粗品 的包装条件见本行。",
]


def reference_execution(tmp_path, monkeypatch, current_run, *, linked=False):
    """Reuse the persistence/worker fixture with independent entity-only responses."""
    monkeypatch.setattr(
        execution_fixture, "arguments", lambda directory, _lines: arguments(directory, LINES),
    )
    args, original_factory, _, _, load = execution_fixture.setup(
        tmp_path, monkeypatch, current_run,
    )
    template = original_factory().adapter
    template.reference_resolution = True
    coordinator_thread = get_ident()
    requests, contexts, instances = [], [], []

    def transport(_client, **kwargs):
        assert get_ident() != coordinator_thread
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        requests.append(deepcopy(view))
        if kwargs.get("tool_choice") == "required":
            required = json.loads(kwargs["instructions"].splitlines()[-1])
            return ResponseTurn(
                response_id=f"reference-{len(requests)}", response_status="completed",
                output_items=[{
                    "type": "function_call", "id": f"item-{position}",
                    "call_id": f"check-{position}", "name": "validate_graph",
                    "arguments": json.dumps({"claim_id": identity,
                                             "shape_profile_id": required["shape_profile_id"]}),
                } for position, identity in enumerate(required["required_relation_checks"])],
                incomplete_details=None, error=None,
                usage={"input_tokens": 10, "output_tokens": 5},
            )

        def quote(unit, text=None):
            return {"evidence_id": unit["evidence_id"],
                    "text": unit["text"] if text is None else text, "context_text": None}

        if view["stage"] == "discovery":
            source = next(unit for unit in view["evidence_units"]
                          if unit["fact_eligible"] and "粗品" in unit["text"])
            spelling = "HRS-9267 粗品" if "HRS-9267 粗品" in source["text"] else "HRS-9267粗品"
            answer = {
                "entities": [{
                    "local_id": "product", "class_iri": execution_fixture.CHILD,
                    "representation": "mention", "mentions": [quote(source, spelling)],
                    "record_components": [], "identifier_claims": [],
                }],
                "properties": [], "relations": [], "external_links": [],
                "observations": [], "reference_bindings": [],
            }
            existing = [entity for entity in view.get("registered_entities", [])
                        if entity["class_iri"] == execution_fixture.CHILD]
            if existing:
                candidate = existing[0]
                answer["reference_bindings"] = [{
                    "local_id": "same-product", "source_id": "product",
                    "target_id": candidate["entity_ref"]["id"], "binding_kind": "coreference",
                    "support": [quote(source), *candidate["proposal"]["mentions"]],
                }]
            if view["predicate_iri"] != execution_fixture.LINK:
                answer["entities"] = []
                answer["reference_bindings"] = []
            elif linked:
                answer["relations"] = [{
                    "local_id": "report-product", "subject_id": view["subject_ref"]["id"],
                    "predicate_iri": execution_fixture.LINK, "object_ids": ["product"],
                    "selection": "all", "bridge_support": [quote(source)],
                    "selection_support": [],
                    "qualifiers": {"polarity": "affirmed", "modality": "asserted",
                                   "condition_support": [], "scope_qualifiers": []},
                    "bridge_kind": "document_subject_description", "bridge_ref_ids": [],
                    "source_assertion": {
                        "subject_support": [],
                        "object_support": [{"object_id": "product",
                                            "support": [quote(source, spelling)]}],
                        "predicate_support": [quote(source)],
                        "binding_ids": [item["local_id"] for item in answer["reference_bindings"]],
                    },
                }]
        else:
            # Every binding facet explicitly reviews both physical source locations.
            support = [quote(unit) for unit in view["evidence_units"]]
            answer = {"verifications": [{
                "target_id": target["target_id"], "content_hash": target["content_hash"],
                "facets": [{
                    "name": facet, "verdict": "supported", "support": support,
                    "counterevidence_support": [],
                    "reason": "原文明确说明是本报告同一工艺产物，已核对双方提及和作用域。",
                } for facet in target["required_facets"]],
            } for target in view["verification_input"]["targets"]]}
        return ResponseTurn(
            response_id=f"reference-{len(requests)}", response_status="completed",
            output_items=[{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(answer)}],
            }], incomplete_details=None, error=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)

    def executor(**kwargs):
        runner = original_factory(**kwargs)
        # A new adapter has no runtime registry, protocol cache or earlier task inputs.
        adapter = ToolModelRecognitionAdapter(
            object(), index=template.index, ontology=template.ontology, metadata=template.metadata,
            profile=template.profile, token_counter=len, model_identity=template.model_identity,
            max_input_tokens=template.max_input_tokens,
            max_output_tokens=template.max_output_tokens,
            tool_limits=template.tool_limits, reference_resolution=True,
        )
        instances.append(adapter)
        inspect = adapter.inspect

        def capture(task, context, predicate, menu):
            contexts.append((task, deepcopy(context.tool_inputs)))
            return inspect(task, context, predicate, menu)

        monkeypatch.setattr(adapter, "inspect", capture)
        runner.adapter = adapter
        return runner

    return args, executor, requests, contexts, instances, load


@pytest.mark.parametrize("pause_after", [None, 1, 2, "second_discovery"])
def test_current_entity_identity_survives_cross_task_binding_and_cold_resume(
    tmp_path, monkeypatch, current_run, pause_after,
):
    store, run, token = current_run
    args, executor, requests, contexts, instances, load = reference_execution(
        tmp_path, monkeypatch, current_run,
    )
    stop, batches = False, []

    def calls(state):
        nonlocal stop
        current_state.persist_calls(store, run, token, run.run_fingerprint, state)
        if pause_after == "second_discovery" and len(requests) == 3 and any(
            row["field"] == "model_turn" for row in state.get("result_changes", {}).values()
        ):
            stop = True

    def ranking(state):
        current_state.persist_ranking(store, run, token, run.run_fingerprint, state)

    def work(changes):
        current_state.persist_boundary(
            store, run, token, changes=changes, fingerprint=run.run_fingerprint,
            expected_version=run.work_version,
        )

    runner = executor(progress_hook=lambda _stage: not stop)

    def batch(value):
        nonlocal stop
        current_state.persist_batch(
            store, run, token, batch=value, fingerprint=run.run_fingerprint,
            ontology=runner.ontology, ir=args["ir"], metadata=args["metadata"],
            index=RecordIndex(args["ir"]), expected_version=run.work_version,
        )
        batches.append(value)
        if pause_after and len(batches) == pause_after:
            stop = True

    hooks = dict(model_call_hook=calls, protocol_result_loader=load,
                 work_hook=work, batch_hook=batch, ranking_hook=ranking)
    result = runner.run(**args, **hooks)
    if pause_after:
        assert result.graph.artifact_status == "partial"
        expected_batches = 2 if pause_after == "second_discovery" else pause_after
        assert len(batches) == expected_batches
        if pause_after == "second_discovery":
            assert len([value for value in batches if value.outcome.complete]) == 1
            assert batches[-1].outcome.reason_code == "execution_pause_requested"
        assert len(requests) == (3 if pause_after == "second_discovery" else pause_after * 2)
        assert not result.graph.edges and not result.graph.relationship_groups
        restored = current_state.restore_work(store, run, run.run_fingerprint).work_state
        assert restored.get("entity_origins"), "orphan entities need a current source origin"
        assert restored.get("reference_resolutions"), "resolved mentions must survive pause"
        control = restored["control"]["current"]
        restored_calls = current_state.restore_calls(store, run, run.run_fingerprint)
        if pause_after == "second_discovery":
            incomplete = [protocol for protocol in restored_calls["protocols"].values()
                          if protocol["outcome_ref"] is None]
            assert len(incomplete) == 1
            assert incomplete[0]["completed_attempts"] == [1]
            assert len(incomplete[0]["reference_context"]["entity_refs"]) == 2
        runner = executor()
        result = runner.run(
            **args, **hooks,
            model_call_state=restored_calls,
            ranking_state=current_state.restore_ranking(store, run, run.run_fingerprint),
            resume_state={"work_state": restored, "frontier": control["frontier_policy"],
                          "diagnostics": control["diagnostics"]},
        )
        assert len(instances) == 2 and instances[0] is not instances[1]

    discoveries = [view for view in requests if view["stage"] == "discovery"]
    assert len(discoveries) == 3, result.diagnostics
    assert len(requests) == 6, "completed tasks must not make repeated model calls after resume"
    assert len(batches) == (4 if pause_after == "second_discovery" else 3)
    assert len([value for value in batches if value.outcome.complete]) == 3
    assert not result.graph.edges and not result.graph.relationship_groups
    product_nodes = [
        node for node in result.graph.nodes if node.class_iri == execution_fixture.CHILD
    ]
    assert len(product_nodes) == 1, result.diagnostics
    canonical = product_nodes[0].entity_id
    assert product_nodes[0].label == "HRS-9267 粗品"
    for view in discoveries[1:]:
        candidates = [entity for entity in view["registered_entities"]
                      if entity["class_iri"] == execution_fixture.CHILD]
        assert len(candidates) == 1 and candidates[0]["entity_ref"]["id"] == canonical
        prior_ids = {anchor["evidence_id"] for anchor in candidates[0]["source_refs"]}
        assert all(not unit["fact_eligible"] for unit in view["evidence_units"]
                   if unit["evidence_id"] in prior_ids)
    assert all(task.subject.is_document_root for task, _ in contexts)
    rows = current_state.restore_work(store, run, run.run_fingerprint).work_state
    resolutions = [row["value"] for row in rows["reference_resolutions"].values()]
    assert len(resolutions) == 3
    assert {item["entity_ref"]["id"] for item in resolutions} == {canonical}
    assert len({anchor["evidence_id"] for item in resolutions
                for anchor in item["source_refs"]}) == 3
    assert len([item for item in resolutions if item["binding_ref"]]) == 2
    requirements = {row["key"]: row["value"]
                    for row in rows["dependencies:requirements"].values()}
    for identity, alternatives in requirements.items():
        assert all(identity not in dependency_ids for dependency_ids in alternatives)
    for resolution in resolutions:
        for field, dependency_field in (("claim_ref", "entity_dependency_refs"),
                                        ("binding_ref", "binding_dependency_refs")):
            reference = resolution[field]
            if reference is None:
                continue
            identity = f"{reference['id']}@{reference['revision']}"
            expected = {f"{ref['id']}@{ref['revision']}"
                        for ref in resolution[dependency_field]}
            assert expected and any(expected <= set(proof) for proof in requirements[identity])
    display = current_state.read_display(store, run)
    assert len([node for node in display["graph"]["nodes"]
                if node["class_iri"] == execution_fixture.CHILD]) == 1


def test_canonical_relation_endpoints_expand_one_shared_child_task_scope(
    tmp_path, monkeypatch, current_run,
):
    args, executor, _requests, contexts, _, load = reference_execution(
        tmp_path, monkeypatch, current_run, linked=True,
    )
    store, run, token = current_run
    runner = executor()
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
    products = [node for node in result.graph.nodes if node.class_iri == execution_fixture.CHILD]
    assert len(products) == 1, result.diagnostics
    canonical = products[0].entity_id
    assert len(result.graph.edges) == 3, result.diagnostics
    assert {edge.object_ref.id for edge in result.graph.edges} == {canonical}
    children = [(task, values) for task, values in contexts if not task.subject.is_document_root]
    assert len(children) == 3, result.diagnostics
    assert len({task.task_id for task, _ in children}) == 3
    assert {task.subject.entity_id for task, _ in children} == {canonical}
    for task, values in children:
        dependency = next(item for item in values["entity_dependencies"]
                          if item["entity_ref"]["id"] == canonical)
        assert dependency["proposal"]["mentions"][0]["text"] == "HRS-9267 粗品"
        assert task.scope.members == []
    assert not result.graph.progress.model_calls_unresolved
