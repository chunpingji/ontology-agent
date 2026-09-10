"""Historical report documents preview originals and explicitly create owned Word runs."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api import document_analysis
from app.config import settings
from app.models.document_analysis import DocumentAnalysisArtifact, DocumentAnalysisRun
from app.models.entity_shadow import EntityShadow
from app.models.extraction import AnnotationExecution, ExtractionJob

from .test_document_analysis import ROOT_IRI, _word_bytes


@pytest.fixture
def report_source(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "runs")
    path = tmp_path / "historical.docx"
    path.write_bytes(_word_bytes(tmp_path))
    iri = "http://slpra.org/facts#upload-historical"
    job = ExtractionJob(
        source_type="word", source_filename="historical.docx", document_path=str(path),
        source_config={"mode": "auto", "doc_class_iri": ROOT_IRI, "doc_ref": iri},
        status="paused",
    )
    db.add(job)
    db.flush()
    document = EntityShadow(
        iri=iri, class_iri=ROOT_IRI, module="document", label_zh=job.source_filename,
        properties_json={"job_id": str(job.id), "_version": 1},
    )
    db.add(document)
    db.commit()
    dispatched = []
    monkeypatch.setattr(document_analysis, "_wake_dispatcher_or_fallback",
                        lambda _tasks, run_id, **_kw: dispatched.append(run_id))
    return document, job, path, dispatched


def _get(client, headers, document, resource="runs"):
    return client.get(f"/api/document-analysis/documents/{resource}", headers=headers,
                      params={"document_iri": document.iri})


def _post(client, headers, document, **body):
    return client.post("/api/document-analysis/documents/runs", headers=headers,
                       params={"document_iri": document.iri}, json=body)


def test_historical_preview_reads_headings_without_models_or_legacy_results(
    client, db, analyst_headers, report_source, tmp_path, monkeypatch,
):
    document, job, path, dispatched = report_source

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET must not call models or create an analysis")

    monkeypatch.setattr(
        "app.services.extraction.local_semantic_model.configured_generic_runner", forbidden,
    )
    monkeypatch.setattr("app.services.llm.local_client.get_local_llm", forbidden)
    monkeypatch.setattr(
        "app.services.extraction.word_tree_summarizer.summarize_word_tree", forbidden,
    )
    monkeypatch.setattr(document_analysis, "_wake_dispatcher_or_fallback", forbidden)
    from app.api import extraction

    cache = tmp_path / "old.annotated.json"
    cache.write_text('{"content":{"text":"legacy-secret"}}')
    monkeypatch.setattr(extraction, "_annotation_cache_path", lambda _: cache)
    before = path.read_bytes()
    for _ in range(2):
        latest = _get(client, analyst_headers, document)
        assert latest.json() == {"run": None}
        assert latest.headers["cache-control"] == "private, no-store"
        response = _get(client, analyst_headers, document, "source")
        assert response.status_code == 200, response.text
        result = response.json()
        assert response.headers["cache-control"] == "private, no-store"
        assert result["source_job_id"] == str(job.id)
        assert result["document_hash"] == hashlib.sha256(before).hexdigest()
        assert result["section_tree"]["children"][0]["heading"] == "第一章 产品概要"
        assert "本报告明确描述产品甲。" in response.text
        assert "legacy-secret" not in response.text
        assert "relationships" not in result
    assert path.read_bytes() == before
    assert cache.read_text() == '{"content":{"text":"legacy-secret"}}'
    assert not dispatched
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 0
    assert db.scalar(select(func.count()).select_from(AnnotationExecution)) == 0
    db.refresh(job)
    assert job.status == "paused"
    assert job.source_config["mode"] == "auto"


def test_create_reuses_original_is_idempotent_and_owner_scoped(
    client, db, analyst_headers, report_source,
):
    document, job, path, dispatched = report_source
    created = _post(client, analyst_headers, document, request_key="same")
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    run = db.get(DocumentAnalysisRun, run_id)
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    assert source.payload["origin"] == {
        "kind": "report-document-origin-v1", "document_iri": document.iri,
        "source_job_id": str(job.id), "document_version": "1", "root_class_iri": ROOT_IRI,
    }
    assert run.document_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert run.metadata_mode == "generate_summary"
    replay = _post(client, analyst_headers, document, request_key="same")
    assert replay.json()["recognition_run_id"] == run_id
    assert replay.json()["idempotent_replay"] is True
    assert len(dispatched) == 1
    assert _get(client, analyst_headers, document).json()["run"]["recognition_run_id"] == run_id
    other_headers = {**analyst_headers, "X-User": "another-user"}
    assert _get(client, other_headers, document).json() == {"run": None}
    foreign = client.get(f"/api/document-analysis/runs/{run_id}", headers=other_headers)
    assert foreign.status_code == 404
    other = _post(client, other_headers, document, request_key="same")
    assert other.status_code == 202
    assert other.json()["recognition_run_id"] != run_id
    assert db.scalar(select(func.count()).select_from(ExtractionJob)) == 1
    assert db.scalar(select(func.count()).select_from(AnnotationExecution)) == 0
    assert document.properties_json == {"job_id": str(job.id), "_version": 1}


@pytest.mark.parametrize("change", ["version", "content", "class", "job", "expired", "deleted"])
def test_latest_does_not_mix_other_versions_or_retired_runs(
    client, db, analyst_headers, report_source, change, tmp_path,
):
    document, job, path, _ = report_source
    created = _post(client, analyst_headers, document, request_key="before-change")
    assert created.status_code == 202, created.text
    run = db.get(DocumentAnalysisRun, created.json()["recognition_run_id"])
    if change == "version":
        document.properties_json = {**document.properties_json, "_version": 2}
    elif change == "content":
        path.write_bytes(_word_bytes(tmp_path, "这是另一个版本的正文。"))
    elif change == "class":
        document.class_iri = "urn:ChangedClass"
        job.source_config = {**job.source_config, "doc_class_iri": document.class_iri}
    elif change == "job":
        replacement = ExtractionJob(
            source_type="word", source_filename=job.source_filename,
            document_path=str(path), source_config=dict(job.source_config),
        )
        db.add(replacement)
        db.flush()
        document.properties_json = {**document.properties_json, "job_id": str(replacement.id)}
    elif change == "expired":
        run.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        run.deletion_state = "requested"
    db.commit()
    assert _get(client, analyst_headers, document).json() == {"run": None}
    if change in {"version", "content"}:
        conflict = _post(client, analyst_headers, document, request_key="before-change")
        assert conflict.status_code == 409


def test_role_input_and_source_failures_are_explicit(
    client, db, analyst_headers, operator_headers, report_source,
):
    document, job, path, dispatched = report_source
    assert _get(client, operator_headers, document, "source").status_code == 200
    assert _post(client, operator_headers, document, request_key="denied").status_code == 403
    assert _post(client, analyst_headers, document, request_key="bad",
                 file_path="/etc/passwd").status_code == 400
    job.source_type = "excel"
    db.commit()
    mismatch = _get(client, analyst_headers, document, "source")
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "SOURCE_TYPE_MISMATCH"
    job.source_type = "word"
    job.source_config = {**job.source_config, "doc_ref": "urn:another-document"}
    db.commit()
    assert _post(client, analyst_headers, document, request_key="wrong-owner").status_code == 422
    job.source_config = {"mode": "auto", "doc_class_iri": "urn:WrongClass"}
    db.commit()
    assert _get(client, analyst_headers, document, "source").status_code == 422
    job.source_config = {"mode": "auto", "doc_class_iri": ROOT_IRI}
    db.commit()
    path.write_bytes(b"not a word file")
    assert _get(client, analyst_headers, document, "source").status_code == 422
    path.unlink()
    missing = _get(client, analyst_headers, document, "source")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "SOURCE_NOT_FOUND"
    assert _post(client, analyst_headers, document, request_key="missing").status_code == 404
    document.properties_json = {"job_id": "invalid"}
    db.commit()
    assert _get(client, analyst_headers, document).status_code == 404
    document.module = "equipment"
    db.commit()
    assert _get(client, analyst_headers, document).status_code == 404
    assert not dispatched


def test_run_can_replay_its_owned_original_after_library_copy_is_removed(
    client, db, analyst_headers, report_source,
):
    document, _job, path, _ = report_source
    created = _post(client, analyst_headers, document, request_key="frozen-source")
    assert created.status_code == 202, created.text
    run_id = created.json()["recognition_run_id"]
    original = path.read_bytes()
    path.unlink()
    assert _get(client, analyst_headers, document).json()["run"]["recognition_run_id"] == run_id
    download = client.get(f"/api/document-analysis/runs/{run_id}/source?format=original",
                          headers=analyst_headers)
    assert download.status_code == 200
    assert download.content == original
