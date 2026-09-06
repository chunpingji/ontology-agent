from copy import deepcopy
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from app.models.evidence import EvidenceJobState
from app.models.extraction import GeneratedReport
from tests.test_reporting.test_snapshot_report import setup_report


def test_report_api_freezes_before_background_work_and_preserves_download(
    client,
    db,
    analyst_headers,
    operator_headers,
    real_world,
    tmp_path,
    monkeypatch,
):
    from app.api import extraction
    from app.config import settings
    from app.services.extraction import extraction_tasks

    job, template, schema = setup_report(db, real_world, tmp_path, positive=True)
    monkeypatch.setattr(extraction_tasks, "semantic_schema_from_engine", lambda engine: schema)
    monkeypatch.setattr(settings, "report_output_dir", str(tmp_path / "reports"))
    monkeypatch.setattr("app.db.SessionLocal", sessionmaker(bind=db.bind, expire_on_commit=False))
    worker = extraction._build_and_save_report
    queued = []
    monkeypatch.setattr(
        extraction, "_build_and_save_report", lambda **kwargs: queued.append(kwargs)
    )
    endpoint = f"/api/extraction/jobs/{job.id}/risk-report"
    assert client.post(endpoint, headers=operator_headers).status_code == 403
    response = client.post(endpoint, headers=analyst_headers)
    assert response.status_code == 202, response.text
    receipt = response.json()
    template.version = "v2"
    template.schema_json = {**template.schema_json, "sections": []}
    db.commit()
    worker(**queued[0])
    record = db.get(GeneratedReport, UUID(receipt["report_id"]), populate_existing=True)
    assert record.report_status == "completed", record.report_error
    assert record.rules_summary["template_version"] == "v1"
    assert record.evidence_snapshot_id == receipt["snapshot_id"]
    assert Path(record.file_path).read_bytes().startswith(b"PK")
    download = client.get(endpoint, headers=analyst_headers)
    assert download.status_code == 200 and download.content.startswith(b"PK")


def test_discovery_requires_explicit_current_manifest_and_separate_permission(
    client,
    db,
    analyst_headers,
    operator_headers,
    real_world,
    tmp_path,
    monkeypatch,
):
    from app.api import evidence

    job, template, schema = setup_report(db, real_world, tmp_path, positive=True)
    monkeypatch.setattr(evidence, "semantic_schema_from_engine", lambda engine: schema)
    value = deepcopy(template.schema_json)
    value["sections"][0]["coverage"][0]["quantifier"] = "all"
    template.schema_json = value
    state = db.get(EvidenceJobState, job.id)
    state.extraction_run = {"completion": "complete"}
    db.commit()
    endpoint = f"/api/extraction/jobs/{job.id}/evidence"
    manifest = client.get(endpoint + "/coverage", headers=analyst_headers).json()
    task = manifest["tasks"][0]
    assert task["reason"] == "object_universe_open"
    request = {
        "manifest_id": manifest["manifest_id"],
        "reason": "逐节核对了全部设备对象",
        "coverage_task_ids": [task["coverage_task_id"]],
    }
    assert (
        client.post(endpoint + "/discovery", headers=operator_headers, json=request).status_code
        == 403
    )
    response = client.post(endpoint + "/discovery", headers=analyst_headers, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["tasks"][0]["status"] == "filled"
    assert (
        client.post(endpoint + "/discovery", headers=analyst_headers, json=request).status_code
        == 409
    )
    db.refresh(state)
    assert (
        state.extraction_run["discovery_decisions"][task["coverage_task_id"]]["actor"] == "analyst"
    )


def test_gap_api_records_model_failure_and_deduplicates_same_input(
    client,
    db,
    analyst_headers,
    real_world,
    tmp_path,
    monkeypatch,
):
    from docx import Document

    from app.api import evidence
    from app.services.extraction import local_semantic_model
    from app.services.extraction.candidate_store import CandidateStore
    from app.services.extraction.extraction_tasks import GenericExtractionRunner
    from app.services.extraction.word_analysis import analyze_word_core

    job, _, schema = setup_report(db, real_world, tmp_path)
    monkeypatch.setattr(evidence, "semantic_schema_from_engine", lambda engine: schema)
    monkeypatch.setattr(
        local_semantic_model,
        "configured_generic_runner",
        lambda engine: GenericExtractionRunner(
            schema,
            None,
            None,
            model_identity="offline-test",
        ),
    )
    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("供测试使用的原文证据")
    doc.save(source)
    store = CandidateStore(db)
    ir = analyze_word_core(source).ir
    store.save_analysis(job.id, ir, {"completion": "complete"})
    endpoint = f"/api/extraction/jobs/{job.id}/evidence"
    for expected in ("model_unavailable", "identical_input_already_processed"):
        manifest = client.get(endpoint + "/coverage", headers=analyst_headers).json()
        response = client.post(
            endpoint + "/fill-gaps",
            headers=analyst_headers,
            json={
                "manifest_id": manifest["manifest_id"],
                "reason": "补齐设备缺口",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == expected
    from app.services.extraction import extraction_tasks

    # A new prompt/projection cannot reuse the old failure result, but must not
    # reset the durable two-round allowance either.
    for version, expected in (
        ("next-wire", "model_unavailable"),
        ("third-wire", "gap_round_budget_exhausted"),
    ):
        monkeypatch.setattr(extraction_tasks, "MODEL_CONTEXT_VERSION", version)
        manifest = client.get(endpoint + "/coverage", headers=analyst_headers).json()
        response = client.post(
            endpoint + "/fill-gaps", headers=analyst_headers,
            json={"manifest_id": manifest["manifest_id"], "reason": "协议更新后验证缺口"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == expected
    store.save_analysis(job.id, ir, {"completion": "incomplete"})
    state = db.get(EvidenceJobState, job.id)
    assert len(state.extraction_run["gap_history"]) == 2
