"""An exact entity revision keeps its interpretation across later proposals."""
from __future__ import annotations

import json

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.contracts import GraphNode
from app.services.extraction.ontology_guided.dependencies import DependencyIndex
from app.services.extraction.ontology_guided.model_adapter import LocalModelRecognitionAdapter
from tests.test_extraction.test_semantic_graph_closure import (
    API,
    DESCRIBES,
    ROOT,
    _analysis,
    _effective,
    _respond,
)


@pytest.mark.parametrize("changed", [
    {"class_iri": API},
    {"label": "另一个实体解释"},
    {"evidence_refs": []},
    {"identity_status": "verified"},
])
def test_same_revision_conflict_cannot_reinterpret_an_existing_supported_edge(
    client, db, analyst_headers, tmp_path, monkeypatch, changed,
):
    monkeypatch.setattr(
        model_adapter, "chat_with_schema",
        lambda _client, *, user, **_kwargs: _respond(json.loads(user)),
    )
    base = LocalModelRecognitionAdapter(object(), model_identity="node-revision-test")

    class ConflictingAdapter:
        model_identity = base.model_identity
        original = None
        original_edge = None
        injected = False

        def inspect(self, task, context, predicate, menu):
            outcome = base.inspect(task, context, predicate, menu)
            if self.original is not None and task.subject.is_document_root and not self.injected:
                self.injected = True
                outcome.nodes.append(self.original.model_copy(update=changed))
                outcome.edges.append(self.original_edge.model_copy(update={
                    "candidate_id": "conflicting-node-new-edge",
                }))
            elif task.predicate_iri == DESCRIBES and outcome.nodes:
                self.original = outcome.nodes[0].model_copy(deep=True)
                self.original_edge = outcome.edges[0].model_copy(deep=True)
            return outcome

    adapter = ConflictingAdapter()
    _run_id, result, batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path), adapter,
    )
    assert adapter.injected
    current = next(
        node for node in result.graph.nodes if node.entity_id == adapter.original.entity_id
    )
    assert current == adapter.original
    effective = _effective(result, DependencyIndex.from_snapshot(batches[-1].dependency_index))
    assert any(edge.predicate_iri == DESCRIBES for edge in effective.edges)
    assert all(edge.candidate_id != "conflicting-node-new-edge" for edge in result.graph.edges)
    assert "adapter_node_revision_conflict" in result.diagnostics


def test_later_weak_observation_keeps_supported_node_and_root_immutable(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        model_adapter, "chat_with_schema",
        lambda _client, *, user, **_kwargs: _respond(json.loads(user)),
    )
    base = LocalModelRecognitionAdapter(object(), model_identity="weak-observation-test")

    class WeakAdapter:
        model_identity = base.model_identity
        original = None
        injected = False

        def inspect(self, task, context, predicate, menu):
            outcome = base.inspect(task, context, predicate, menu)
            if self.original is not None and task.subject.is_document_root and not self.injected:
                self.injected = True
                outcome.nodes.extend([
                    self.original.model_copy(update={"decision_status": "unsupported"}),
                    GraphNode(entity_id=task.subject.entity_id, revision=task.subject.revision,
                              class_iri=API, class_label="非法根类型", label="模型改写的根"),
                    GraphNode(entity_id="extra-root", revision=1, class_iri=ROOT,
                              class_label="报告", label="模型伪造根", root=True),
                ])
            elif task.predicate_iri == DESCRIBES and outcome.nodes:
                self.original = outcome.nodes[0].model_copy(deep=True)
            return outcome

    adapter = WeakAdapter()
    _run_id, result, _batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path), adapter,
    )
    assert adapter.injected
    current = next(
        node for node in result.graph.nodes if node.entity_id == adapter.original.entity_id
    )
    assert current.decision_status == "supported"
    roots = [node for node in result.graph.nodes if node.root]
    assert len(roots) == 1
    assert roots[0].entity_id == result.graph.root_ref.id
    assert roots[0].class_iri == ROOT and roots[0].root_origin == "user_specified"
    assert roots[0].label == "not-a-product-name.docx"
    assert all(node.entity_id != "extra-root" for node in result.graph.nodes)


def _persisted_run(client, db, analyst_headers, tmp_path, monkeypatch, analysis, adapter):
    """Exercise real run-store candidates, proofs, graph and checkpoint writes."""
    from app.models.document_analysis import DocumentAnalysisArtifact
    from app.services.document_analysis import execution as execution_service
    from app.services.document_analysis.state_artifacts import decode_state
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from app.services.extraction.ontology_guided.metadata import prepare_metadata
    from app.services.extraction.ontology_guided.records import RecordIndex
    from tests.test_extraction.test_document_analysis_execution_recovery import (
        _claim,
        _create_pending_run,
    )
    from tests.test_extraction.test_semantic_graph_closure import _ontology

    run_id = _create_pending_run(
        client, analyst_headers, tmp_path, monkeypatch, key="immutable-graph-revisions"
    )
    store, token = _claim(db, run_id)
    run = store.get_owned(run_id, "analyst")
    metadata = prepare_metadata(
        analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
        summary_version="persisted-closure",
    )
    ontology = _ontology()
    executor = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=adapter, phase1_section_limit=1,
    )
    arguments = dict(
        recognition_run_id=run_id, run_fingerprint="persisted-closure-fingerprint",
        ir=analysis.ir, metadata=metadata, root_class_iri=ROOT, root_class_label="报告",
        filename="not-a-product-name.docx",
    )
    batches = []

    def persist(batch):
        execution_service._persist_recognition_batch(
            db, store, run, token, final_fingerprint=arguments["run_fingerprint"],
            ontology=ontology, ir=analysis.ir, metadata=metadata,
            index=RecordIndex(analysis.ir), batch=batch,
        )
        batches.append(batch)

    result = executor.run(**arguments, batch_hook=persist)
    head = store.get_artifact_head(run_id, "analyst", "recognition_checkpoint")
    checkpoint = decode_state(
        store, run, db.get(DocumentAnalysisArtifact, head.artifact_id).payload,
    )
    # Reconstruct from the durable ledger without another model invocation.
    class NoMoreCalls:
        model_identity = adapter.model_identity

        def inspect(self, *_args):
            raise AssertionError("completed durable ledger must not recall the model")

    executor.adapter = NoMoreCalls()
    restored = executor.run(**arguments, resume_state=checkpoint)
    assert restored.graph.nodes == result.graph.nodes
    assert restored.graph.edges == result.graph.edges
    assert restored.graph.properties == result.graph.properties
    assert restored.ranking_state == result.ranking_state
    assert restored.diagnostics == result.diagnostics
    return run_id, result, batches


def test_technical_retry_keeps_physical_node_immutable_and_advances_assertion_in_store(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    from sqlalchemy import select

    from app.models.document_analysis import DocumentRunCandidate
    from tests.test_extraction.test_semantic_graph_closure import INGREDIENT

    first_target = None

    def respond(_client, *, user, **_kwargs):
        nonlocal first_target
        request = json.loads(user)
        result = _respond(request)
        if request["predicate"]["iri"] == DESCRIBES and result.get("verifications"):
            if first_target is None:
                first_target = request["target"]["task_id"]
                for verification in result["verifications"]:
                    verification["predicate_verdict"] = "unsupported"
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    base = LocalModelRecognitionAdapter(object(), model_identity="persisted-technical-retry")

    class RetryAdapter:
        model_identity = base.model_identity
        initial_node = None

        def inspect(self, task, context, predicate, menu):
            outcome = base.inspect(task, context, predicate, menu)
            if task.task_id == first_target and outcome.nodes:
                self.initial_node = outcome.nodes[0].model_copy(deep=True)
                outcome.complete = False
            return outcome

    adapter = RetryAdapter()
    run_id, result, batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path), adapter,
    )
    node = next(item for item in result.graph.nodes
                if item.entity_id == adapter.initial_node.entity_id)
    assert node == adapter.initial_node
    assert node.decision_status == "unsupported" and node.revision == 1
    effective = _effective(result, DependencyIndex.from_snapshot(batches[-1].dependency_index))
    assert {edge.predicate_iri for edge in effective.edges} == {DESCRIBES, INGREDIENT}
    describes = next(edge for edge in effective.edges if edge.predicate_iri == DESCRIBES)
    assert describes.revision == 2 and describes.object_ref.revision == 1
    history = list(db.scalars(select(DocumentRunCandidate).where(
        DocumentRunCandidate.recognition_run_id == run_id,
        DocumentRunCandidate.candidate_id == describes.candidate_id,
    ).order_by(DocumentRunCandidate.revision)))
    assert [item.revision for item in history] == [1, 2]
    assert history[0].payload["decision_status"] == "unsupported"
    assert history[1].payload["decision_status"] == "supported"
    assert history[0].proof_refs != history[1].proof_refs
    node_rows = list(db.scalars(select(DocumentRunCandidate).where(
        DocumentRunCandidate.recognition_run_id == run_id,
        DocumentRunCandidate.candidate_id == node.entity_id,
    )))
    assert len(node_rows) == 1 and node_rows[0].payload == node.model_dump(mode="json")
    queries = [query for epoch in result.ranking_state["service"]["epochs"]
               if epoch["subject_ref"]["entity_id"] == node.entity_id
               for query in epoch["queries"]]
    assert queries and all(query["trusted_context"] for query in queries)
    assert all({"id": describes.candidate_id, "revision": 2} in query["dependency_refs"]
               for query in queries)


def test_successful_counterevidence_recheck_persists_new_proof_without_reviving_old_version(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    from sqlalchemy import select

    from app.models.document_analysis import DocumentRunCandidate
    from tests.test_extraction.test_semantic_graph_closure import INGREDIENT

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        result = _respond(request)
        if request["required_counterevidence"] and result.get("verifications"):
            negative = next(item for item in request["fragments"]
                            if "活性成分不含" in item["text"])
            for verification in result["verifications"]:
                verification["counterevidence_support"] = [
                    {"evidence_id": negative["evidence_id"], "text": negative["text"]}
                ]
                verification["counterevidence_verdict"] = "supported"
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    run_id, result, batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path, negative=True),
        LocalModelRecognitionAdapter(object(), model_identity="persisted-full-recheck"),
    )
    dependencies = DependencyIndex.from_snapshot(batches[-1].dependency_index)
    effective = _effective(result, dependencies)
    ingredient = next(edge for edge in effective.edges if edge.predicate_iri == INGREDIENT)
    assert ingredient.revision == 2
    assert not dependencies.is_valid(f"{ingredient.candidate_id}@1")
    assert dependencies.is_valid(f"{ingredient.candidate_id}@2")
    history = list(db.scalars(select(DocumentRunCandidate).where(
        DocumentRunCandidate.recognition_run_id == run_id,
        DocumentRunCandidate.candidate_id == ingredient.candidate_id,
    ).order_by(DocumentRunCandidate.revision)))
    assert [item.revision for item in history] == [1, 2]
    assert history[0].proof_refs != history[1].proof_refs
    assert history[1].payload == ingredient.model_dump(mode="json")
    assert ingredient.proof_ref.model_dump(mode="json") in history[1].proof_refs


def test_multiple_proposals_for_one_candidate_get_distinct_immutable_batch_revisions(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    from sqlalchemy import select

    from app.models.document_analysis import DocumentRunCandidate

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        result = _respond(request)
        if request["predicate"]["iri"] == DESCRIBES and result.get("proposals"):
            result["proposals"].append({**result["proposals"][0], "reason": "第二个独立核验提议。"})
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    run_id, result, batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path),
        LocalModelRecognitionAdapter(object(), model_identity="batch-multiple-proposals"),
    )
    edge = next(item for item in result.graph.edges if item.predicate_iri == DESCRIBES)
    assert edge.revision == 2
    batch = next(item for item in batches if len(item.outcome.edges) == 2)
    assert [item.revision for item in batch.outcome.edges] == [1, 2]
    history = list(db.scalars(select(DocumentRunCandidate).where(
        DocumentRunCandidate.recognition_run_id == run_id,
        DocumentRunCandidate.candidate_id == edge.candidate_id,
    ).order_by(DocumentRunCandidate.revision)))
    assert [item.revision for item in history] == [1, 2]
    assert history[0].proof_refs != history[1].proof_refs


def test_withdrawn_parent_version_requires_descendant_revalidation_after_support_returns(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    from app.services.extraction.ontology_guided.contracts import VersionedRef
    from tests.test_extraction.test_semantic_graph_closure import INGREDIENT

    monkeypatch.setattr(
        model_adapter, "chat_with_schema",
        lambda _client, *, user, **_kwargs: _respond(json.loads(user)),
    )
    base = LocalModelRecognitionAdapter(object(), model_identity="withdrawn-parent-test")

    class WithdrawingAdapter:
        model_identity = base.model_identity
        original = None
        descendant_calls = 0
        withdrawn = False
        restored = False
        steps = []

        def inspect(self, task, context, predicate, menu):
            outcome = base.inspect(task, context, predicate, menu)
            if task.predicate_iri == INGREDIENT and any(
                edge.decision_status == "supported" for edge in outcome.edges
            ):
                self.descendant_calls += 1
                self.steps.append("descendant")
            if task.subject.is_document_root and self.descendant_calls and not self.restored:
                outcome = self.original.model_copy(deep=True)
                if not self.withdrawn:
                    self.withdrawn = True
                    self.steps.append("withdrawn")
                    outcome.edges[0].decision_status = "unsupported"
                    outcome.edges[0].policy_eligible = False
                    outcome.edges[0].reason_code = "revalidation_withdrawn"
                else:
                    self.restored = True
                    self.steps.append("restored")
                # Fresh observations retain historical candidate/proof rows.
                suffix = f":{task.task_id}"
                decision_refs = []
                for decision in outcome.decision_payloads:
                    decision["decision_id"] += suffix
                    decision["target_id"] += suffix
                    decision_refs.append(VersionedRef(id=decision["decision_id"], revision=1))
                for proof in outcome.proof_payloads:
                    proof["proof_id"] += suffix
                    proof["target_id"] += suffix
                outcome.edges[0].decision_refs = decision_refs
                outcome.edges[0].proof_ref = VersionedRef(
                    id=outcome.proof_payloads[0]["proof_id"], revision=1,
                )
            elif task.predicate_iri == DESCRIBES and outcome.edges and self.original is None:
                self.original = outcome.model_copy(deep=True)
            return outcome

    adapter = WithdrawingAdapter()
    _run_id, result, batches = _persisted_run(
        client, db, analyst_headers, tmp_path, monkeypatch, _analysis(tmp_path), adapter,
    )
    assert adapter.steps == ["descendant", "withdrawn", "restored", "descendant"]
    parent = next(edge for edge in result.graph.edges if edge.predicate_iri == DESCRIBES)
    assert parent.revision == 3
    restored_batch = next(batch for batch in batches
                          if any(edge.candidate_id == parent.candidate_id and edge.revision == 3
                                 for edge in batch.outcome.edges))
    intermediate_dependencies = DependencyIndex.from_snapshot(restored_batch.dependency_index)
    intermediate = _effective(
        type("Result", (), {"graph": restored_batch.graph}), intermediate_dependencies,
    )
    assert [edge.predicate_iri for edge in intermediate.edges] == [DESCRIBES]
    assert not intermediate_dependencies.is_valid(f"{parent.candidate_id}@1")
    final_dependencies = DependencyIndex.from_snapshot(batches[-1].dependency_index)
    assert {edge.predicate_iri for edge in _effective(result, final_dependencies).edges} == {
        DESCRIBES, INGREDIENT,
    }
