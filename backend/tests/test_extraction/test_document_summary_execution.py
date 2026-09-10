"""Summary requests retain the owned run's cancellation and telemetry scope."""

from app.api import document_analysis
from app.config import settings
from app.services.document_analysis import execution
from app.services.llm.model_runtime import ModelCancelled, runtime

from .test_document_analysis_execution_recovery import ROOT_IRI, _claim, _word_bytes


def test_cancelled_summary_keeps_run_identity_and_never_publishes_fallback_metadata(
    client, db, analyst_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "document_analysis_storage_dir", tmp_path / "run-artifacts")
    monkeypatch.setattr(document_analysis, "dispatch_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(execution, "get_local_llm", object)
    response = client.post(
        "/api/document-analysis/runs", headers=analyst_headers,
        files={"file": ("summary.docx", _word_bytes(tmp_path),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"root_class_iri": ROOT_IRI, "request_key": "summary-cancellation",
              "metadata_mode": "generate_summary"},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["recognition_run_id"]
    calls = []

    def cancelled(_structure, _client, *, should_stop_fn):
        assert runtime.get()["run_id"] == run_id
        assert runtime.get()["bind"] is db.get_bind()
        assert runtime.get()["should_stop"] == should_stop_fn
        calls.append(True)
        raise ModelCancelled()

    monkeypatch.setattr(execution, "summarize_word_tree", cancelled)
    store, token = _claim(db, run_id)
    execution._execute_dispatched_run(db, store, store.get_owned(run_id, "analyst"), token)
    db.expire_all()
    run = store.get_owned(run_id, "analyst")
    assert calls == [True]
    assert run.execution_status == "failed" and run.stop_reason == "model_interrupted"
    assert run.stage == "metadata"
    assert run.metadata_snapshot_id is None
    assert store.get_artifact(run_id, "analyst", "metadata") is None
