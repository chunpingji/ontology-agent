"""Duplicate retrieval texts share one exact embedding across durable batches."""

from collections import Counter

from app.services.document_analysis import current_state
from app.services.document_analysis.run_store import DocumentAnalysisRunStore
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_document_state_artifacts import seed
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot


def test_ranking_write_failure_has_a_specific_public_message():
    from app.services.document_analysis.execution import _failure_payload
    from app.services.llm.model_runtime import ModelWaitFailure

    assert _failure_payload(ModelWaitFailure("ranking_persistence_failed")) == (
        "RANKING_STATE_PERSISTENCE_FAILED", "语义检索进度保存失败，已保留已完成结果",
    )


def test_repeated_text_cannot_overwrite_paid_embedding_and_resumes_without_model_calls(
    db, tmp_path, monkeypatch,
):
    args = setup_slot(tmp_path)
    store = DocumentAnalysisRunStore(db)
    run, token = seed(store, "repeated-embedding")
    run.run_fingerprint = args["run_fingerprint"]
    db.commit()

    class BatchSensitiveModel(RankingModel):
        def __init__(self):
            super().__init__()
            self.inputs = []

        def embed(self, texts):
            # Real GPU kernels can differ slightly across differently shaped
            # batches. Re-embedding one key must not replace its paid result.
            self.inputs.extend(texts)
            self.calls.append(("embed", len(texts)))
            return [[1.0 + len(self.calls) * 1e-7, 1.0] for _text in texts]

    model = BatchSensitiveModel()

    def persist(state):
        current_state.persist_ranking(store, run, token, run.run_fingerprint, {
            **state, "recognition_run_id": str(run.recognition_run_id),
            "run_fingerprint": run.run_fingerprint, "committed_at": {}, "discarded_epochs": {},
        })

    service = RankingService(
        RankingPolicy(mode="semantic", batch_size=1), model,
        current_state=True, before_model_hook=persist,
    )
    build_views = service._views

    def repeated_views(*arguments, **kwargs):
        views = build_views(*arguments, **kwargs)
        shared_text = next(iter(views.values())).model_text
        assert len(views) > 1
        return {key: view.model_copy(update={"model_text": shared_text})
                for key, view in views.items()}

    monkeypatch.setattr(service, "_views", repeated_views)
    epoch = service.prepare_next_epoch(**args)
    service.commit_epoch(epoch)
    persist(service.current_changes())
    assert max(Counter(model.inputs).values()) == 1
    assert service.costs["embedding_inputs"] == len(model.inputs)

    paid_calls = len(model.calls)
    state = current_state.restore_ranking(store, run, run.run_fingerprint)["service"]
    restored = RankingService(service.policy, model, state=state, current_state=True)
    assert restored.epochs[0] == service.epochs[0]
    assert restored.snapshot()["cache"] == service.snapshot()["cache"]
    assert restored.prepare_next_epoch(**args) is None
    assert len(model.calls) == paid_calls
