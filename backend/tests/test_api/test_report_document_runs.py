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
from app.services.document_analysis.adaptive_configuration import configured_adaptive_policy
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.adaptive_retrieval import (
    AdaptivePolicy,
    CalibrationProfile,
)
from app.services.extraction.ontology_guided.ontology_lexical import RDFS_LABEL_IRI
from app.services.extraction.ontology_guided.ontology_plan import ontology_snapshot_from_engine

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


@pytest.mark.parametrize(
    "failure", ["repair_disabled", "calibration_missing", "calibration_invalid"],
)
def test_creation_reports_invalid_adaptive_configuration_without_creating_a_run(
    client, db, analyst_headers, report_source, tmp_path, monkeypatch, failure,
):
    document, _job, path, dispatched = report_source
    original = path.read_bytes()
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", True)
    monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "trial")
    profile = tmp_path / "calibration.json"
    monkeypatch.setattr(settings, "document_analysis_adaptive_calibration_path", str(profile))
    if failure == "repair_disabled":
        monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", False)
        monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "enhanced")
    elif failure == "calibration_invalid":
        profile.write_text("{}", encoding="utf-8")

    response = _post(client, analyst_headers, document, request_key="invalid-configuration")

    assert response.status_code == 503, response.text
    assert response.json() == {
        "contract_version": "document-analysis-runs-v1",
        "error": {
            "code": "ADAPTIVE_CONFIGURATION_INVALID",
            "message": "自适应检索配置或校准制品无效",
            "retryable": True,
            "current_revision": None,
        },
    }
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 0
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact)) == 0
    assert not list(settings.document_analysis_storage_dir.iterdir())
    assert path.read_bytes() == original
    assert not dispatched


def test_legacy_calibration_rejects_lexical_ontology_but_enhanced_creates_a_run(
    client, db, analyst_headers, report_source, fake_engine, tmp_path, monkeypatch,
):
    document, _job, path, dispatched = report_source
    original = path.read_bytes()
    monkeypatch.setattr(fake_engine, "get_lexical_annotations", lambda iris: {
        iri: [{"text": "CMC报告", "language": "zh", "predicate_iri": RDFS_LABEL_IRI}]
        if iri == ROOT_IRI else [] for iri in iris
    })
    ontology = ontology_snapshot_from_engine(fake_engine, ROOT_IRI)
    assert ontology.lexical_context is not None
    policy = AdaptivePolicy(mode="observation")
    legacy = CalibrationProfile(
        model_hash="a" * 64,
        view_version=policy.view_version,
        view_configuration_hash=evidence_hash({key: getattr(policy, key) for key in (
            "view_version", "sibling_limit", "ancestor_limit", "group_member_limit",
        )}),
        predicate_iris=[ontology.classes[ROOT_IRI].declared_relationships[0].iri],
        dense_thresholds={"discover": -2, "counterevidence": -2},
        self_thresholds={"discover": -2, "counterevidence": -2},
        group_thresholds={"discover": 0, "counterevidence": 0},
        rerank_thresholds={"discover": -8, "counterevidence": -8},
        sample_manifest_hash="b" * 64,
        expert_review_hash="c" * 64,
    )
    profile = tmp_path / "legacy-calibration.json"
    profile.write_text(legacy.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(settings, "document_analysis_evidence_repair_enabled", True)
    monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "trial")
    monkeypatch.setattr(settings, "document_analysis_adaptive_calibration_path", str(profile))
    configured = configured_adaptive_policy(settings)
    assert configured.calibration.query_version == "subject-slot-query-v1"
    assert configured.calibration.ontology_hash is None
    assert configured.calibration.lexical_context_hash is None

    rejected = _post(client, analyst_headers, document, request_key="lexical-source")

    assert rejected.status_code == 503, rejected.text
    assert rejected.json()["error"]["code"] == "ADAPTIVE_CONFIGURATION_INVALID"
    assert rejected.json()["error"]["retryable"] is True
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 0
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisArtifact)) == 0
    assert not list(settings.document_analysis_storage_dir.iterdir())
    assert path.read_bytes() == original
    assert not dispatched

    monkeypatch.setattr(settings, "document_analysis_adaptive_retrieval_mode", "enhanced")
    accepted = _post(client, analyst_headers, document, request_key="lexical-source")

    assert accepted.status_code == 202, accepted.text
    run = db.get(DocumentAnalysisRun, accepted.json()["recognition_run_id"])
    source = db.get(DocumentAnalysisArtifact, run.source_artifact_ref)
    frozen_policy = source.payload["performance_policy"]["adaptive_retrieval"]
    assert frozen_policy["mode"] == "enhanced"
    assert frozen_policy["calibration"] is None
    assert run.ontology_snapshot_hash == ontology.ontology_hash
    assert db.scalar(select(func.count()).select_from(DocumentAnalysisRun)) == 1
    assert dispatched == [run.recognition_run_id]
    assert path.read_bytes() == original
