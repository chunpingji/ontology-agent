"""Legacy template detail reads must create source jobs with foreign keys enforced."""

from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.ast_templates import get_template
from app.db import Base
from app.models.extraction import AstTemplate, ExtractionJob
from app.services.reporting.ast_template import load_default_template


@pytest.fixture(params=[True, False], ids=["autoflush", "explicit-flush"])
def db(request):
    """An isolated database: shared test fixtures normally leave FK checks disabled."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        Base.metadata.create_all(engine)
        with Session(engine, autoflush=request.param) as session:
            assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            yield session
    finally:
        engine.dispose()


def _legacy_template(db, tmp_path, *, suffix=".docx", filename="source.docx"):
    source = tmp_path / f"legacy-source{suffix}"
    source.write_bytes(b"test source; background parsing must not run")
    row = AstTemplate(
        name="Legacy risk template",
        version="v18",
        status="published",
        schema_json=load_default_template().model_dump(),
        sample_text="Existing sample",
        sample_content_json={"type": "doc", "content": []},
        sample_analysis={"schema_version": "test"},
        iri_pattern="https://ontology.pharma-gmp.cn/slpra/drug-development/CMCReport",
        default_source_path=str(source),
        default_source_filename=filename,
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize(
    ("suffix", "filename", "source_type"),
    [(".docx", "source.docx", "word"), (".xlsx", None, "excel")],
)
def test_legacy_detail_creates_job_before_setting_foreign_key(
    db, tmp_path, fake_engine, suffix, filename, source_type,
):
    from app.api.extraction import _precompute_annotation_bg

    row = _legacy_template(db, tmp_path, suffix=suffix, filename=filename)
    unchanged = {
        key: getattr(row, key)
        for key in (
            "name", "version", "status", "schema_json", "iri_pattern",
            "sample_text", "sample_content_json", "sample_analysis",
        )
    }
    background = BackgroundTasks()

    result = get_template(row.id, background, db, fake_engine)

    db.expire_all()
    job = db.get(ExtractionJob, result["default_source_job_id"])
    assert job is not None
    assert row.default_source_job_id == job.id
    assert db.query(ExtractionJob).count() == 1
    assert job.source_type == source_type
    assert job.source_filename == (filename or f"legacy-source{suffix}")
    assert job.document_path == row.default_source_path
    assert job.status == "running"
    assert job.source_config == {
        "mode": "template_default",
        "template_id": str(row.id),
        "doc_class_iri": row.iri_pattern,
    }
    for key, value in unchanged.items():
        assert result[key] == value
        assert getattr(row, key) == value
    assert result["training_pairs"] == []
    assert len(background.tasks) == 1
    assert background.tasks[0].func is _precompute_annotation_bg
    assert background.tasks[0].args == (job.id, fake_engine, db)


def test_detail_api_reuses_job_on_second_read(
    client, db, tmp_path, analyst_headers, monkeypatch,
):
    row = _legacy_template(db, tmp_path)
    background = Mock()
    monkeypatch.setattr("app.api.extraction._precompute_annotation_bg", background)
    url = f"/api/ast-templates/{row.id}"

    first = client.get(url, headers=analyst_headers)
    second = client.get(url, headers=analyst_headers)

    assert first.status_code == second.status_code == 200
    job_id = first.json()["default_source_job_id"]
    assert job_id is not None
    assert second.json()["default_source_job_id"] == job_id
    assert db.query(ExtractionJob).count() == 1
    background.assert_called_once()
    assert str(background.call_args.args[0]) == job_id


def test_failed_commit_rolls_back_job_and_template_reference(
    db, tmp_path, fake_engine, monkeypatch,
):
    row = _legacy_template(db, tmp_path)
    template_id = row.id
    background = BackgroundTasks()

    def fail_after_flush():
        db.flush()
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db, "commit", fail_after_flush)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        get_template(template_id, background, db, fake_engine)
    db.rollback()

    assert db.get(AstTemplate, template_id).default_source_job_id is None
    assert db.query(ExtractionJob).count() == 0
    assert background.tasks == []


def test_missing_source_does_not_create_job(db, tmp_path, fake_engine):
    row = _legacy_template(db, tmp_path)
    row.default_source_path = str(tmp_path / "missing.docx")
    db.commit()
    background = BackgroundTasks()

    result = get_template(row.id, background, db, fake_engine)

    assert result["default_source_job_id"] is None
    assert db.query(ExtractionJob).count() == 0
    assert background.tasks == []
