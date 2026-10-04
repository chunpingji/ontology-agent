"""A human interpretation is scoped independently of the model's unresolved relation."""

from uuid import uuid4

import pytest

from app.models.document_analysis import DocumentRunCurrentState
from app.services.document_analysis.run_store import DocumentAnalysisRunStore, content_hash
from app.services.document_harness.application import ENGINE
from app.services.document_harness.interpretations import _slash_connects_members
from app.services.document_harness.source import build_windows
from tests.test_document_harness.test_lookup import NS, quote
from tests.test_document_harness.test_lookup import lookup_fixture as lookup_fixture


def seed_row(db, run, domain, key, value):
    payload = {"key": key, "value": value}
    db.add(DocumentRunCurrentState(
        recognition_run_id=run.recognition_run_id, domain="harness:" + domain,
        business_key=content_hash(key), work_version=1,
        payload=payload, content_hash=content_hash(payload),
    ))


def seed_run(db, fixture, *, owner="analyst", document_hash=None, two_tasks=False,
             slash=True, group_state="unresolved"):
    ir, catalog, *_ = fixture
    run_key = uuid4().hex
    payload = catalog.model_dump(mode="json")
    run, _ = DocumentAnalysisRunStore(db).create_run_with_source(
        owner_id=owner, request_key=run_key, filename="equipment.docx",
        document_hash=document_hash or ir.original_document_hash,
        root_class_iri=catalog.root_class_iri,
        root_class_label="报告", ontology_snapshot_hash=content_hash(payload),
        source_artifact_id="source:" + run_key, source_storage_uri="original.docx",
        source_media_type="application/docx", source_size_bytes=1,
        source_payload={"engine": ENGINE, "policy": {"protocol": "document-harness-v10", "execution_policy": {"flow": "four_stage", "reading_concurrency": 2}}},
        ontology_artifact_id="ontology:" + run_key, ontology_payload=payload,
        progress={"engine": ENGINE},
    )
    run.execution_status = "finished"
    ref = build_windows(ir)[0].resolve(ir, quote("A1/A2"))
    root = {"id": "document", "label": "报告", "role": "document_root",
            "class_iri": NS + "Report", "class_label": "报告", "state": "accepted",
            "reason": "输入类型", "evidence": []}
    entity_definitions = [("document", "报告", NS + "Report"),
                          ("a", "A1", NS + "Object"), ("b", "A2", NS + "Object")]
    if two_tasks:
        entity_definitions.append(("c", "A2", NS + "Object"))
    for key, label, iri in entity_definitions:
        entity = root if key == "document" else {
            "id": key, "label": label, "role": "object", "class_iri": iri,
            "class_label": "设备", "state": "accepted", "reason": "原文确认", "evidence": [ref],
        }
        seed_row(db, run, "entities", key, entity)
    for index in range(2 if two_tasks else 1):
        subject_id = "document"
        key = f"group-{index}"
        seed_row(db, run, "relation_groups", key, {
            "id": key, "subject_id": subject_id,
            "object_ids": ["a", "b" if index == 0 else "c"],
            "alignment_class_iri": NS + "Report", "predicate_iri": NS + "uses",
            "label": "使用设备", "state": group_state,
            "reason": "斜杠也不足以证明两设备为或选关系" if slash else "原文另有疑点",
            "evidence": [ref] if slash else [{**ref, "text": "A1", "end": ref["start"] + 2}],
            "polarity": "positive", "conditions": [],
            "participation": "unknown" if slash else "options",
            "selection": "unspecified", "timing": "unspecified", "ordered_object_ids": None, "order_evidence": [],
            "timing_state": "unresolved", "timing_reason": "时间未决",
        })
    from app.services.document_harness.accounting import empty_metrics, update_business_metrics
    from app.services.document_harness.projection import build_graph_base
    from app.services.document_harness.runtime import read_rows

    db.flush()
    state = read_rows(db, run)
    metrics = empty_metrics()
    for domain, rows in state.items():
        for row in rows.values():
            metrics = update_business_metrics(metrics, domain, None, row)
    seed_row(db, run, "metrics", "main", metrics)
    seed_row(db, run, "display", "graph", {
        "work_version": run.work_version, "base": build_graph_base(state, payload),
    })
    db.commit()
    return run


def graph(client, run, headers):
    result = client.get(
        f"/api/document-analysis/runs/{run.recognition_run_id}/harness-graph", headers=headers,
    )
    assert result.status_code == 200, result.text
    return result.json()


def answer(client, run, task, meaning, scope, headers):
    return client.post(
        f"/api/document-analysis/runs/{run.recognition_run_id}"
        f"/interpretation-tasks/{task['id']}/answer",
        headers=headers, json={"meaning": meaning, "scope": scope,
                               "expected_revision": task["scope_revisions"][scope]},
    )


def test_questions_and_scope_precedence_leave_model_unresolved(
    lookup_fixture, db, client, analyst_headers,
):
    run = seed_run(db, lookup_fixture, two_tasks=True)
    initial = graph(client, run, analyst_headers)
    tasks = initial["interpretation_tasks"]
    # Two distinct relation occurrences share the same slash text and schema.
    assert len(tasks) == 2
    assert [question["prompt"] for question in tasks[0]["questions"]] == [
        "此处斜杠表示这些对象之间的什么含义？", "这个解释适用于哪个范围？",
    ]
    assert {item["value"] for item in tasks[0]["questions"][0]["options"]} == {
        "alternatives", "parallel", "joint_unspecified", "unresolved",
    }
    assert tasks[0]["evidence"][0]["text"] == "A1/A2"
    saved = answer(client, run, tasks[0], "alternatives", "occurrence", analyst_headers)
    assert saved.status_code == 200, saved.text
    after = graph(client, run, analyst_headers)
    assert [task["answer"]["meaning"] if task["answer"] else None
            for task in after["interpretation_tasks"]] == ["alternatives", None]
    assert all(item["state"] == "unresolved" for item in after["relation_groups"])
    saved = answer(client, run, after["interpretation_tasks"][1],
                   "parallel", "document", analyst_headers)
    assert saved.status_code == 200, saved.text
    after = graph(client, run, analyst_headers)
    assert [task["answer"]["meaning"] for task in after["interpretation_tasks"]] == [
        "alternatives", "parallel",
    ]
    same_document = seed_run(db, lookup_fixture)
    assert graph(client, same_document, analyst_headers)["interpretation_tasks"][0]["answer"][
        "meaning"] == "parallel"
    other_owner = seed_run(db, lookup_fixture, owner="another")
    other_headers = {"X-User": "another", "X-Role": "operator"}
    assert graph(client, other_owner, other_headers)["interpretation_tasks"][0]["answer"] is None
    other_document = seed_run(db, lookup_fixture, document_hash="b" * 64)
    assert (graph(client, other_document, analyst_headers)["interpretation_tasks"][0]
            ["answer"] is None)


def test_platform_role_owner_conflicts_and_non_ambiguity(
    lookup_fixture, db, client, analyst_headers, operator_headers,
):
    run = seed_run(db, lookup_fixture)
    task = graph(client, run, analyst_headers)["interpretation_tasks"][0]
    denied = answer(client, run, task, "parallel", "platform", operator_headers)
    assert denied.status_code in {403, 404}
    ordinary_owner = {"X-User": "analyst", "X-Role": "operator"}
    denied = answer(client, run, task, "parallel", "platform", ordinary_owner)
    assert denied.status_code == 403, denied.text
    bad = answer(client, run, task, "unresolved", "platform", analyst_headers)
    assert bad.status_code == 400
    saved = answer(client, run, task, "parallel", "platform", analyst_headers)
    assert saved.status_code == 200, saved.text
    stale = answer(client, run, task, "alternatives", "platform", analyst_headers)
    assert stale.status_code == 409
    foreign = seed_run(db, lookup_fixture, owner="another")
    foreign_headers = {"X-User": "another", "X-Role": "operator"}
    assert graph(client, foreign, foreign_headers)["interpretation_tasks"][0]["answer"][
        "meaning"] == "parallel"
    assert client.get(
        f"/api/document-analysis/runs/{run.recognition_run_id}/harness-graph",
        headers=foreign_headers,
    ).status_code in {403, 404}
    no_slash = seed_run(db, lookup_fixture, slash=False)
    accepted = seed_run(db, lookup_fixture, group_state="accepted")
    assert graph(client, no_slash, analyst_headers)["interpretation_tasks"] == []
    assert graph(client, accepted, analyst_headers)["interpretation_tasks"] == []


@pytest.mark.parametrize("meaning", ["alternatives", "parallel", "joint_unspecified", "unresolved"])
def test_all_meaning_options_can_be_recorded_locally(
    lookup_fixture, db, client, analyst_headers, meaning,
):
    run = seed_run(db, lookup_fixture)
    task = graph(client, run, analyst_headers)["interpretation_tasks"][0]
    response = answer(client, run, task, meaning, "occurrence", analyst_headers)
    assert response.status_code == 200, response.text
    assert response.json()["answer"]["meaning"] == meaning


def test_date_slash_does_not_create_relation_question():
    assert not _slash_connects_members(
        {"text": "2026/08/01 使用 A1 和 A2"},
        [{"label": "A1"}, {"label": "A2"}],
    )


def test_task_disappearing_before_submission_is_rejected(
    lookup_fixture, db, client, analyst_headers,
):
    run = seed_run(db, lookup_fixture)
    task = graph(client, run, analyst_headers)["interpretation_tasks"][0]
    row = db.get(DocumentRunCurrentState, (
        run.recognition_run_id, "harness:relation_groups", content_hash("group-0"),
    ))
    payload = {"key": "group-0", "value": {**row.payload["value"], "state": "accepted"}}
    row.payload, row.content_hash = payload, content_hash(payload)
    db.commit()
    response = answer(client, run, task, "parallel", "occurrence", analyst_headers)
    assert response.status_code == 409, response.text
