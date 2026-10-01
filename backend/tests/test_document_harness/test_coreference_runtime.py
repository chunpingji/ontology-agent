"""Persist co-reference once, expose canonical nodes through the owned read-only API."""

from copy import deepcopy
from uuid import uuid4

from app.schemas.document_harness import HarnessGraph
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_harness.application import ENGINE
from app.services.document_harness.controller import Engine, Paused
from app.services.document_harness.coreference import review_coreferences
from app.services.document_harness.runtime import Repository, initialize_state
from app.services.document_harness.work import DEFAULT_POLICY, make_work
from tests.test_document_harness.test_coreference import (
    answer,
    two,
)
from tests.test_document_harness.test_coreference import (
    case as case,  # noqa: F401
)


def test_paid_coreference_resume_and_owned_read_only_projection(
    case, db, client, analyst_headers, operator_headers, monkeypatch,
):
    from app.services.document_harness import model

    ir, catalog, _ = case
    key = uuid4().hex
    store = DocumentAnalysisRunStore(db)
    payload = catalog.model_dump(mode="json")
    run, _ = store.create_run_with_source(
        owner_id="analyst", request_key=key, filename="coreference.docx",
        document_hash=ir.original_document_hash, root_class_iri=catalog.root_class_iri,
        root_class_label="报告", ontology_snapshot_hash=content_hash(payload),
        source_artifact_id="source:" + key, source_storage_uri="original.docx",
        source_media_type="application/docx", source_size_bytes=1,
        source_payload={"engine": ENGINE, "policy": {"max_request_bytes": 32768}},
        ontology_artifact_id="ontology:" + key, ontology_payload=payload,
        progress={"engine": ENGINE},
    )
    initialize_state(db, run, payload)
    token = store.claim(run.recognition_run_id, "analyst", actor="test", worker_id="test")
    db.commit()
    repo = Repository(db, run, token)
    initial = two(case)
    original_mentions = deepcopy(initial["entities"])
    initial["input"] = {"document": ir.model_dump(mode="json")}
    ref = initial["entities"]["1"]["referent"]
    initial["properties"] = {"p": {
        "id": "p", "subject_id": "1", "field_id": "field", "predicate_iri": "urn:test:value",
        "label": "测试属性", "value": "周一", "source_value": ref["text"], "source_unit": None,
        "value_component": "span", "value_evidence": [ref], "state": "accepted",
        "reason": "原文属性", "evidence": [ref],
    }}
    initial["relations"] = {"r": {
        "id": "r", "subject_id": "document", "object_id": "1",
        "predicate_iri": "urn:test:describes", "label": "描述", "state": "accepted",
        "reason": "原文支持", "evidence": [ref], "polarity": "positive", "conditions": ["周一"],
    }}
    work = make_work("coreference_review", {
        "left_mention_id": "0", "right_mention_id": "1",
        "clue_refs": [initial["entities"]["0"]["referent"]],
    }, initial, catalog, DEFAULT_POLICY)
    initial["work"] = {work["id"]: work}
    initial["cursor"] = {"main": {"stage": "coreference_review", "active_window_id": None,
                                  "active_batch": None, "scope_complete": False}}
    repo.save(initial)
    calls, pause = [], {"value": False}
    def model_call(stage, inputs, schema, policy):
        calls.append((stage, inputs, schema))
        pause["value"] = True
        return {"output": answer(inputs), "error": None, "raw_response": {}, "seconds": 1,
                "usage": {"input_tokens": 20, "output_tokens": 10}}
    monkeypatch.setattr(model, "call_model", model_call)
    def engine(repository):
        return Engine(
            ir=ir, catalog=catalog, state=repository.load(), invoke=repository.invoke,
            save=repository.save, should_stop=lambda: pause["value"],
            prepare_batch=repository.prepare_batch, invoke_prepared=repository.invoke_prepared,
            batch_request=repository.batch_request,
        )
    # Stop after payment commits, before the exact current batch is applied.
    def interrupted(batch):
        repo.invoke_prepared(batch)
        raise Paused()
    first = engine(repo)
    first.invoke_prepared = interrupted
    try:
        review_coreferences(first)
    except Paused:
        pass
    assert len(calls) == 1 and not repo.load().get("coreferences")
    assert repo.load()["cursor"]["main"]["active_batch"]["targets"][0]["id"] == work["id"]
    pause["value"] = False
    resumed_repo = Repository(db, run, token)
    continued = engine(resumed_repo)
    review_coreferences(continued)
    assert len(calls) == 1
    assert resumed_repo.load()["cursor"]["main"]["active_batch"] is None
    assert resumed_repo.load()["entities"] == original_mentions
    endpoint = f"/api/document-analysis/runs/{run.recognition_run_id}/harness-graph"
    for _ in range(2):
        response = client.get(endpoint, headers=analyst_headers)
        assert response.status_code == 200, response.text
        graph = HarnessGraph.model_validate(response.json())
        group = next(row for row in graph.entities if row.role != "document_root")
        assert len(group.mentions) == 2 and graph.coreferences[0].applied
        assert graph.properties[0].subject_id == group.id
        assert graph.properties[0].subject_mention_id == "1"
        assert graph.relations[0].object_id == group.id
        assert graph.relations[0].object_mention_id == "1"
        assert graph.relations[0].conditions == ["周一"]
        assert graph.progress.stage_costs[0].stage == "coreference_review"
    assert len(calls) == 1
    assert client.get(endpoint, headers=operator_headers).status_code == 404
