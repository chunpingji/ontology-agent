"""v4 safety and liveness; synthetic calibration is never a production quality claim."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.adaptive_retrieval import (
    INTENTS,
    AdaptivePolicy,
    CalibrationProfile,
    SearchPreparationResult,
    epoch_decision,
)
from app.services.extraction.ontology_guided.adaptive_search import AdaptiveSlotSearch
from app.services.extraction.ontology_guided.contracts import GraphNode, VersionedRef
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
from app.services.extraction.ontology_guided.retrieval_query import build_subject_queries
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from tests.test_extraction.test_heuristic_search import _observe_page, _search
from tests.test_extraction.test_semantic_ranking import RankingModel


def adaptive_fixture(tmp_path, *, dense=-2, rerank=0, mode="enforce", paragraphs=None):
    legacy = _search(tmp_path, paragraphs or [f"背景 {i}" for i in range(40)])
    args = {
        "plan": legacy.plan,
        "index": legacy.search_index.index,
        "metadata": legacy.search_index.metadata,
        "predicate": legacy.predicate,
        "subject_node": GraphNode(
            entity_id="subject",
            revision=1,
            class_iri="urn:Product",
            class_label="产品",
            label="待查产品",
        ),
        "run_fingerprint": "test-run",
        "root_ref": VersionedRef(id="root", revision=1),
        "root_class_iri": "urn:Report",
        "permission_scope": "owner:test",
    }
    profile = CalibrationProfile(
        model_hash=evidence_hash(RankingModel.identity),
        view_version="contextual-retrieval-view-v1",
        predicate_iris=[legacy.predicate.iri],
        dense_thresholds={"discover": dense, "counterevidence": dense},
        self_thresholds={"discover": dense, "counterevidence": dense},
        group_thresholds={"discover": 2, "counterevidence": 2},
        rerank_thresholds={"discover": rerank, "counterevidence": rerank},
        sample_manifest_hash="a" * 64,
        expert_review_hash="b" * 64,
        view_configuration_hash=evidence_hash(
            {
                "view_version": "contextual-retrieval-view-v1",
                "sibling_limit": 4,
                "ancestor_limit": 2,
                "group_member_limit": 8,
            }
        ),
    )
    policy = AdaptivePolicy(
        mode=mode, calibration=None if mode == "enhanced" else profile,
        evaluation_only=mode != "enhanced",
    )
    queries = build_subject_queries(
        subject=legacy.plan.subject,
        **{
            key: value
            for key, value in args.items()
            if key not in {"plan", "metadata", "permission_scope"}
        },
    )
    search = AdaptiveSlotSearch(
        plan=legacy.plan,
        predicate=legacy.predicate,
        search_index=legacy.search_index,
        run_fingerprint="test-run",
        policy=HeuristicSearchPolicy.durable(adaptive=True),
        adaptive_policy=policy,
        permission_scope_hash=evidence_hash("owner:test"),
        query_dependency_hash=queries[0].query_dependency_hash,
    )
    service = RankingService(
        RankingPolicy(mode="semantic", policy_version="semantic-ranking-v2"),
        RankingModel(),
        adaptive_policy=policy,
    )
    return search, service, args


def prepare(search, service, args):
    return service.prepare_next_epoch(
        **args,
        expansion_boundary=search.semantic_boundary,
        expansion_attempt_id=search.expansion_attempt_id,
        candidate_record_ids=search.needs_evaluation_ids(),
    )


def test_pre_gate_all_rejected_has_audit_and_reaches_saturation(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    assert search.next_admission() is None and search.needs_semantic
    result = prepare(search, service, args)
    assert isinstance(result, SearchPreparationResult) and result.kind == "filtered_empty"
    assert not service.epochs and not service.snapshot()["pending_epochs"]
    assert service.costs["model_calls"] > 0
    gate = next(
        service.gate_evaluations[ref]
        for ref in result.evaluation_refs
        if service.gate_evaluations[ref]["stage"] == "dense"
    )
    assert len(gate["observations"]) == 40 and gate["request_refs"]
    search.accept_result(result, service.gate_evaluations)
    assert search.next_admission() is None
    assert search.status == "pass_exhausted" and search.diagnostics().records_soft_pruned == 40
    assert all(entry.coverage_state == "unattempted" for entry in search.plan.ledger.values())
    assert len(search.snapshot()["resume_state"]["expansion_attempts"]) == 1


def test_post_gate_rejection_does_not_destroy_pool_or_revive_h3(tmp_path):
    search, service, args = adaptive_fixture(tmp_path)
    assert search.next_admission() is None
    for _ in range(4):
        epoch = service.commit_epoch(prepare(search, service, args))
        decision = epoch_decision(epoch, search.adaptive_policy)
        assert decision.admitted_record_ids == []
        assert epoch.record_ids and set(epoch.record_ids) == set(epoch.ordered_record_ids)
        search.accept_ranked(epoch, decision)
        search.accept_ranked(epoch, decision)  # Idempotent replay does not burn another round.
        search.next_admission()
        if not search.needs_semantic:
            break
    assert search.status == "pass_exhausted"
    assert search.diagnostics().records_soft_pruned == 40
    assert not search.admitted
    before = list(service.model.calls)
    result = service.prepare_next_epoch(**args, expansion_attempt_id="exhausted")
    assert result.kind == "no_new_candidates" and service.model.calls == before


def test_v4_success_continues_and_snapshot_replays_exactly(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, rerank=-10)
    search.next_admission()
    epoch = service.commit_epoch(prepare(search, service, args))
    search.accept_ranked(epoch, epoch_decision(epoch, search.adaptive_policy))
    page = search.next_admission()
    _observe_page(search, page, supported=True)
    assert search.next_admission() is None and search.needs_semantic
    snapshot = search.snapshot()
    restored = replace(search)
    restored.restore(snapshot)
    assert restored.snapshot() == snapshot
    changed = deepcopy(snapshot)
    changed["resume_state"]["schema_version"] = 2
    with pytest.raises(ValueError, match="version"):
        restored.restore(changed)


def test_decision_must_match_actual_committed_epoch(tmp_path):
    search, service, args = adaptive_fixture(tmp_path)
    search.next_admission()
    epoch = prepare(search, service, args)
    with pytest.raises(ValueError, match="committed"):
        epoch_decision(epoch, search.adaptive_policy)
    epoch = service.commit_epoch(epoch)
    decision = epoch_decision(epoch, search.adaptive_policy)
    forged = decision.model_copy(update={"admitted_record_ids": epoch.record_ids})
    with pytest.raises(ValueError):
        search.accept_ranked(epoch, forged)
    with pytest.raises(ValueError, match="requires"):
        search.accept_semantic(epoch.record_ids, epoch.epoch_id, committed=True)


def test_explicit_continue_reactivates_without_rescoring(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    search.next_admission()
    result = prepare(search, service, args)
    search.accept_result(result, service.gate_evaluations)
    search.next_admission()
    calls = list(service.model.calls)
    search.continue_search()
    page = search.next_admission()
    assert page.stage == "H3" and len(page.record_ids) == 32
    assert service.model.calls == calls
    assert search.diagnostics().records_soft_pruned == 0


def test_observation_preserves_v3_success_stop(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, mode="observation")
    search.next_admission()
    epoch = service.commit_epoch(prepare(search, service, args))
    search.accept_ranked(epoch, epoch_decision(epoch, search.adaptive_policy))
    _observe_page(search, search.next_admission(), supported=True)
    assert search.next_admission() is None and search.status == "local_results_only"
    assert len(search.deferred_record_ids) == 32


def test_development_thresholds_cannot_enable_online_pruning(tmp_path):
    search, _, _ = adaptive_fixture(tmp_path)
    values = search.adaptive_policy.model_dump(mode="json")
    values["evaluation_only"] = False
    with pytest.raises(ValueError, match="isolated"):
        AdaptivePolicy.model_validate(values)


@pytest.mark.parametrize("dense,rerank", [(2, 0), (-2, 0), (-2, -10)])
def test_executor_persists_empty_and_ranked_results_without_spinning(tmp_path, dense, rerank):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    snapshot, args = fixture(tmp_path)
    _, service, _ = adaptive_fixture(tmp_path, dense=dense, rerank=rerank)
    profile = service.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    profile["predicate_iris"] = [
        p.iri for p in snapshot.classes[args["root_class_iri"]].declared_properties
    ]
    policy = AdaptivePolicy(
        mode="enforce", evaluation_only=True, calibration=CalibrationProfile.model_validate(profile)
    )
    saves = []
    batches = []
    executor = OntologyGuidedExecutor(
        ontology=snapshot,
        engine=object(),
        adapter=NoFactsAdapter(),
        evidence_repair=True,
        incremental_performance=True,
        max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy.durable(adaptive=True),
        adaptive_policy=policy,
        ranking_service=service,
    )
    result = executor.run(
        **args,
        ranking_hook=lambda state: saves.append(deepcopy(state)),
        batch_hook=lambda batch: batches.append(batch),
    )
    progress = result.graph.progress
    assert progress.records_planned == 24
    assert (
        progress.records_examined + progress.records_incomplete + progress.records_unattempted == 24
    )
    assert progress.retrieval_diagnostics is not None
    if rerank == 0:
        assert progress.records_examined == 0 and progress.records_unattempted == 24
        assert progress.retrieval_diagnostics.records_soft_pruned == 24
        assert progress.stop_reason == "adaptive_search_saturated"
    else:
        assert progress.records_examined == 24
        assert progress.completion == "in_scope_complete"
    assert saves


def test_coverage_diagnostics_preserve_legacy_shape_and_validate_subset():
    from app.schemas.document_analysis import GraphCoverage
    from app.schemas.retrieval_diagnostics import RetrievalDiagnostics
    from app.services.extraction.ontology_guided.contracts import RunProgress

    assert "retrieval_diagnostics" not in RunProgress().model_dump(mode="json")
    assert "retrieval_diagnostics" not in GraphCoverage().model_dump(mode="json")
    for cls in (RunProgress, GraphCoverage):
        with pytest.raises(ValueError, match="subset"):
            cls(
                records_planned=2,
                records_examined=1,
                records_unattempted=1,
                retrieval_diagnostics=RetrievalDiagnostics(records_soft_pruned=2),
            )


def test_context_variant_overflow_keeps_self_source(tmp_path):
    from app.services.extraction.ontology_guided.contextual_retrieval import (
        build_contextual_views,
        select_available_view,
    )

    search, _, args = adaptive_fixture(tmp_path)
    views = build_contextual_views(
        args["index"],
        args["metadata"],
        search.adaptive_policy,
        count_tokens_batch=lambda values: [len(v) for v in values],
        max_record_tokens=100000,
    )
    view = next(iter(views.values()))
    limit = view.variants["self"]["token_count"] + 5
    selected = select_available_view(view, 5, limit)
    assert selected.status == "complete"
    assert selected.model_text == view.variants["self"]["model_text"]
    assert select_available_view(view, 5, 1).status == "not_rerankable"
    assert all(role["anchor"] for role in selected.source_roles)


def test_gate_checkpoint_reuses_paid_stage_without_reembedding(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    search.next_admission()
    result = prepare(search, service, args)
    snapshot = service.snapshot()
    recovered_model = RankingModel()
    recovered = RankingService(
        service.policy, recovered_model, state=snapshot, adaptive_policy=search.adaptive_policy
    )
    replay = prepare(search, recovered, args)
    assert replay == result
    assert not recovered_model.calls
    assert recovered.costs["model_calls"] == service.costs["model_calls"]
    search.accept_result(replay, recovered.gate_evaluations)
    search.next_admission()
    assert search.status == "pass_exhausted"


def test_stage_watermarks_do_not_treat_front_gate_pass_as_recognition(tmp_path):
    search, service, args = adaptive_fixture(tmp_path)
    search.next_admission()
    prepare(search, service, args)
    for value in service.gate_evaluations.values():
        search.apply_gate(value)
    assert not search.needs_evaluation_ids("dense")
    assert len(search.needs_evaluation_ids("rerank")) == 40
    assert len(search.pending_disposition_ids) == 40
    assert not search.admitted
    saved = search.snapshot()
    clone = replace(search)
    clone.restore(saved)
    clone.continue_search()  # Ordinary recovery of active work cannot reactivate H3.
    assert clone.snapshot() == saved


def test_three_passed_records_are_not_filled_and_group_ids_never_become_tasks(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    search.next_admission()
    required = search.needs_evaluation_ids()[:3]
    epoch = service.commit_epoch(
        service.prepare_next_epoch(
            **args,
            expansion_boundary=search.semantic_boundary,
            expansion_attempt_id=search.expansion_attempt_id,
            candidate_record_ids=search.needs_evaluation_ids(),
            required_record_ids=required,
        )
    )
    assert set(epoch.record_ids) == set(required)
    for value in service.gate_evaluations.values():
        search.apply_gate(value)
    search.accept_ranked(epoch, epoch_decision(epoch, search.adaptive_policy))
    page = search.next_admission()
    assert set(page.record_ids) == set(required)
    assert search.diagnostics().records_soft_pruned == 37
    assert all(entry.coverage_state == "unattempted" for entry in search.plan.ledger.values())


@pytest.mark.parametrize("boundary", ["dense_gate", "epoch", "decision", "empty_result"])
def test_executor_crash_after_durable_artifact_replays_without_paid_work_loss(tmp_path, boundary):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    ontology, args = fixture(tmp_path)
    _, service, _ = adaptive_fixture(tmp_path, dense=2 if boundary == "empty_result" else -2)
    raw = service.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    raw["predicate_iris"] = [
        p.iri for p in ontology.classes[args["root_class_iri"]].declared_properties
    ]
    adaptive = AdaptivePolicy(
        mode="enforce", evaluation_only=True, calibration=CalibrationProfile.model_validate(raw)
    )

    def executor(model):
        return OntologyGuidedExecutor(
            ontology=ontology,
            engine=object(),
            adapter=NoFactsAdapter(),
            max_tasks=100,
            ranking_service=RankingService(service.policy, model, adaptive_policy=adaptive),
            evidence_repair=True,
            incremental_performance=True,
            adaptive_policy=adaptive,
            heuristic_policy=HeuristicSearchPolicy.durable(adaptive=True),
        )

    snapshots = []

    def persist(state):
        snapshots.append(deepcopy(state))
        saved = state["service"]
        hit = {
            "dense_gate": any(g["stage"] == "dense" for g in saved["gate_evaluations"].values()),
            "epoch": bool(saved["epochs"]),
            "decision": any(
                d["decision_target"] == "recognition" for d in saved["admission_decisions"].values()
            ),
            "empty_result": bool(saved["preparation_results"]),
        }[boundary]
        if hit:
            raise RuntimeError("simulated crash after durable save")

    with pytest.raises(RuntimeError, match="simulated crash"):
        executor(RankingModel()).run(**args, ranking_hook=persist)
    saved = snapshots[-1]
    result = executor(RankingModel()).run(**args, ranking_state=saved)
    assert result.graph.progress.retrieval_diagnostics.records_soft_pruned == 24
    assert result.graph.progress.stop_reason == "adaptive_search_saturated"
    assert (
        result.ranking_state["service"]["costs"]["model_calls"]
        >= saved["service"]["costs"]["model_calls"]
    )
    assert all(count == 1 for count in result.ranking_state["service"]["request_attempts"].values())


def test_observation_matches_v3_task_order_requests_and_stop(tmp_path):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    ontology, args = fixture(tmp_path)
    results = []
    for adaptive in (None, AdaptivePolicy(mode="observation")):
        model = RankingModel()
        adapter = NoFactsAdapter()
        result = OntologyGuidedExecutor(
            ontology=ontology,
            engine=object(),
            adapter=adapter,
            max_tasks=100,
            ranking_service=RankingService(
                RankingPolicy(mode="semantic", policy_version="semantic-ranking-v2"), model
            ),
            evidence_repair=True,
            incremental_performance=True,
            adaptive_policy=adaptive,
            heuristic_policy=HeuristicSearchPolicy.durable(
                incremental=True, adaptive=bool(adaptive)
            ),
        ).run(**args)
        results.append((result, adapter.calls, model.calls))
    left, right = results
    assert left[1:] == right[1:]
    for key in ("tokens", "model_calls", "input_pairs", "embedding_inputs"):
        assert (
            left[0].ranking_state["service"]["costs"][key]
            == right[0].ranking_state["service"]["costs"][key]
        )
    assert left[0].graph.progress.stop_reason == right[0].graph.progress.stop_reason


@pytest.mark.parametrize("mode", ["enforce", "enhanced"])
def test_task_checkpoint_resume_keeps_paid_results_and_source_context(tmp_path, mode):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    ontology, args = fixture(tmp_path)
    adaptive = AdaptivePolicy(mode=mode, evaluation_only=mode == "enforce")
    policy = RankingPolicy(mode="semantic", policy_version="semantic-ranking-v2")
    batches, saves = [], []

    def run(adapter, **kwargs):
        return OntologyGuidedExecutor(
            ontology=ontology,
            engine=object(),
            adapter=adapter,
            max_tasks=100,
            ranking_service=RankingService(policy, RankingModel()),
            evidence_repair=True,
            incremental_performance=True,
            adaptive_policy=adaptive,
            heuristic_policy=HeuristicSearchPolicy.durable(adaptive=True),
        ).run(**args, **kwargs)

    def stop(batch):
        batches.append(batch)
        raise RuntimeError("checkpoint saved")

    with pytest.raises(RuntimeError, match="checkpoint saved"):
        run(
            NoFactsAdapter(),
            batch_hook=stop,
            ranking_hook=lambda value: saves.append(deepcopy(value)),
        )
    batch = batches[-1]
    adapter = NoFactsAdapter()
    result = run(
        adapter,
        resume_state={
            "task_outcomes": batch.task_outcomes,
            "frontier": batch.frontier,
            "recall_ledger": batch.recall_ledger,
            "dependency_index": batch.dependency_index,
        },
        ranking_state=saves[-1],
    )
    assert result.graph.progress.records_examined == 24
    assert batch.task.task_id not in adapter.calls
    assert len(adapter.calls) == len(set(adapter.calls)) == 23


def test_calibration_cannot_survive_view_parameter_change(tmp_path):
    search, _, _ = adaptive_fixture(tmp_path)
    raw = search.adaptive_policy.model_dump(mode="json")
    raw["sibling_limit"] = 5
    with pytest.raises(ValueError, match="view configuration"):
        AdaptivePolicy.model_validate(raw)


def test_entirely_unavailable_views_use_bounded_original_source_exploration(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    service.policy = service.policy.model_copy(update={"max_tokens_per_pair": 1})
    search.next_admission()
    result = prepare(search, service, args)
    assert result.kind == "view_unavailable"
    search.accept_result(result, service.gate_evaluations)
    page = search.next_admission()
    assert page.stage == "H3" and len(page.record_ids) == 32
    assert search.diagnostics().records_soft_pruned == 0
    assert len(search.snapshot()["resume_state"]["expansion_attempts"]) == 0
    assert not service.model.calls


def test_group_hit_preserves_complementary_members_without_fact_permission(tmp_path):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    ontology, args = fixture(tmp_path)
    _, service, _ = adaptive_fixture(tmp_path, dense=2)
    values = service.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    values["predicate_iris"] = [
        p.iri for p in ontology.classes[args["root_class_iri"]].declared_properties
    ]
    values["group_thresholds"] = {"discover": -2, "counterevidence": -2}
    policy = AdaptivePolicy(
        mode="enforce", evaluation_only=True, calibration=CalibrationProfile.model_validate(values)
    )

    class ObserveContext(NoFactsAdapter):
        def __init__(self):
            super().__init__()
            self.contexts = []

        def inspect(self, task, context, predicate, menu):
            self.contexts.append(context)
            return super().inspect(task, context, predicate, menu)

    adapter = ObserveContext()
    result = OntologyGuidedExecutor(
        ontology=ontology,
        engine=object(),
        adapter=adapter,
        max_tasks=100,
        ranking_service=service,
        evidence_repair=True,
        incremental_performance=True,
        adaptive_policy=policy,
        heuristic_policy=HeuristicSearchPolicy.durable(adaptive=True),
    ).run(**args)
    progress = result.graph.progress
    assert progress.records_examined == 16  # Eight bounded members per predicate.
    assert progress.retrieval_diagnostics.records_soft_pruned == 8
    fragments = [
        f
        for context in adapter.contexts
        for f in context.fragments
        if f.purpose == "retrieval_group_binding"
    ]
    assert fragments and not any(f.fact_eligible for f in fragments)
    assert len(adapter.calls) == len(set(adapter.calls))


def test_snapshot_history_is_shared_and_source_scope_changes_fail_closed(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, dense=2)
    search.next_admission()
    result = prepare(search, service, args)
    search.accept_result(result, service.gate_evaluations)
    first, second = service.snapshot(), service.snapshot()
    for key in first["gate_evaluations"]:
        assert first["gate_evaluations"][key] is second["gate_evaluations"][key]
    left, right = search.snapshot()["resume_state"], search.snapshot()["resume_state"]
    for key in left["decisions"]:
        assert left["decisions"][key] is right["decisions"][key]
    forged = dict(service.gate_evaluations[result.evaluation_refs[-1]])
    forged.update(permission_scope_hash="different", evaluation_id="", evaluation_hash="")
    with pytest.raises(ValueError, match="another slot"):
        search.apply_gate(forged)


def test_admission_rejects_missing_intent_and_wrong_model_input(tmp_path):
    search, service, args = adaptive_fixture(tmp_path)
    search.next_admission()
    epoch = service.commit_epoch(prepare(search, service, args))
    incomplete = epoch.model_copy(update={"observations": epoch.observations[1:]})
    with pytest.raises(ValueError, match="complete dual-intent"):
        epoch_decision(incomplete, search.adaptive_policy)
    observations = deepcopy(epoch.observations)
    observations[0]["model_input_hash"] = "other-input"
    forged = epoch.model_copy(update={"observations": observations})
    with pytest.raises(ValueError, match="input or identity"):
        epoch_decision(forged, search.adaptive_policy)


def test_enhanced_search_uses_context_and_never_prunes_low_scores(tmp_path):
    search, service, args = adaptive_fixture(tmp_path, mode="enhanced", dense=2, rerank=2)
    search.next_admission()
    prepared = prepare(search, service, args)
    epoch = service.commit_epoch(prepared)
    assert any(gate["group_observations"] for gate in service.gate_evaluations.values())
    assert all(not gate["pruned_record_ids"] for gate in service.gate_evaluations.values())
    decision = epoch_decision(epoch, search.adaptive_policy)
    assert decision.admitted_record_ids == epoch.ordered_record_ids
    assert decision.disposition_records == {}
    search.accept_ranked(epoch, decision)
    assert search.enforcing
    assert not search.adaptive_policy.evaluation_only
    assert search.adaptive_policy.calibration is None
    restored = type(search)(
        plan=search.plan, predicate=search.predicate, search_index=search.search_index,
        run_fingerprint=search.run_fingerprint, policy=search.policy,
        adaptive_policy=search.adaptive_policy, permission_scope_hash=search.permission_scope_hash,
        query_dependency_hash=search.query_dependency_hash,
    )
    restored.restore(search.snapshot())
    assert restored.snapshot() == search.snapshot()


def test_enhanced_mode_cannot_silently_use_calibration(tmp_path):
    search, _, _ = adaptive_fixture(tmp_path)
    with pytest.raises(ValueError, match="cannot carry pruning calibration"):
        AdaptivePolicy(mode="enhanced", calibration=search.adaptive_policy.calibration)


def test_online_defaults_enable_enhanced_search_and_enforce_still_requires_calibration(
    monkeypatch,
):
    from app.config import Settings
    from app.services.document_analysis.adaptive_configuration import configured_adaptive_policy

    for key in (
        "DOCUMENT_ANALYSIS_EVIDENCE_REPAIR_ENABLED",
        "DOCUMENT_ANALYSIS_ADAPTIVE_RETRIEVAL_MODE",
        "DOCUMENT_ANALYSIS_ADAPTIVE_CALIBRATION_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    policy = configured_adaptive_policy(settings)
    assert settings.document_analysis_evidence_repair_enabled
    assert policy.mode == "enhanced"
    assert policy.active_search and not policy.evaluation_only
    assert policy.calibration is None
    settings.document_analysis_adaptive_retrieval_mode = "enforce"
    with pytest.raises(ValueError, match="expert-reviewed calibration"):
        configured_adaptive_policy(settings)
    settings.document_analysis_adaptive_retrieval_mode = "disabled"
    assert configured_adaptive_policy(settings) is None
    settings.document_analysis_adaptive_retrieval_mode = "enhanced"
    settings.document_analysis_evidence_repair_enabled = False
    with pytest.raises(ValueError, match="requires evidence repair"):
        configured_adaptive_policy(settings)


def trial_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(RankingModel, "identity", {
        "model": "trial-test", "score_semantics": "raw_single_logit",
    })
    monkeypatch.setattr(RankingModel, "score_pairs", lambda self, pairs: [-12.0] * len(pairs))
    search, service, args = adaptive_fixture(tmp_path, dense=2, rerank=-8)
    raw = search.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    raw["group_thresholds"] = dict.fromkeys(INTENTS, 0.0)
    policy = AdaptivePolicy(mode="trial", calibration=CalibrationProfile.model_validate(raw))
    search.adaptive_policy = service.adaptive_policy = policy
    return search, service, args


def test_trial_prunes_only_after_complete_rerank_and_keeps_coverage(tmp_path, monkeypatch):
    search, service, args = trial_fixture(tmp_path, monkeypatch)
    policy = search.adaptive_policy
    assert policy.thresholds(model_identity=service.model_identity,
                             predicate_iri=search.predicate.iri, stage="dense") is None
    search.next_admission()
    decisions = []
    for _ in range(4):
        epoch = service.commit_epoch(prepare(search, service, args))
        assert all(not gate["pruned_record_ids"] for gate in service.gate_evaluations.values())
        decision = epoch_decision(epoch, policy)
        decisions.append(decision)
        search.accept_ranked(epoch, decision)
        page = search.next_admission()
        if page:
            _observe_page(search, page)
        search.next_admission()
        if not search.needs_semantic:
            break
    assert any(d.disposition_records.get("soft_pruned") for d in decisions)
    diagnostic = search.diagnostics()
    assert diagnostic.pruning_quality == "unvalidated"
    assert diagnostic.records_soft_pruned > 0
    assert diagnostic.records_soft_pruned <= len(search.deferred_record_ids)
    assert not policy.evaluation_only
    assert policy.calibration.quality_status == "development"


def test_trial_configuration_cannot_relax_cutoff_or_masquerade_as_validated(tmp_path, monkeypatch):
    search, service, _ = trial_fixture(tmp_path, monkeypatch)
    raw = search.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    for change, message in [
        ({"rerank_thresholds": dict.fromkeys(INTENTS, -2)}, "conservative"),
        ({"group_thresholds": dict.fromkeys(INTENTS, 0.6)}, "positive group"),
        ({"quality_status": "validated"}, "unvalidated"),
    ]:
        with pytest.raises(ValueError, match=message):
            AdaptivePolicy(mode="trial", calibration=CalibrationProfile(**{**raw, **change}))
    with pytest.raises(ValueError, match="exact view"):
        AdaptivePolicy(mode="trial")
    with pytest.raises(ValueError, match="isolated"):
        AdaptivePolicy(mode="enforce", calibration=search.adaptive_policy.calibration)
    from app.config import Settings
    from app.services.document_analysis.adaptive_configuration import configured_adaptive_policy

    profile_path = tmp_path / "development-thresholds.json"
    profile_path.write_text(search.adaptive_policy.calibration.model_dump_json())
    settings = Settings(_env_file=None, document_analysis_adaptive_retrieval_mode="trial",
                        document_analysis_adaptive_calibration_path=str(profile_path))
    assert configured_adaptive_policy(settings) == search.adaptive_policy
    assert configured_adaptive_policy(settings).thresholds(
        model_identity=service.model_identity, predicate_iri=search.predicate.iri, stage="rerank"
    ) == dict.fromkeys(INTENTS, -8)


def test_trial_executor_projects_pending_quality_and_replays_policy(tmp_path, monkeypatch):
    from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
    from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter, fixture

    ontology, args = fixture(tmp_path)
    search, service, _ = trial_fixture(tmp_path, monkeypatch)
    raw = search.adaptive_policy.calibration.model_dump(mode="json", exclude={"profile_hash"})
    raw["predicate_iris"] = [
        p.iri for p in ontology.classes[args["root_class_iri"]].declared_properties
    ]
    policy = AdaptivePolicy(mode="trial", calibration=CalibrationProfile.model_validate(raw))
    result = OntologyGuidedExecutor(
        ontology=ontology, engine=object(), adapter=NoFactsAdapter(), evidence_repair=True,
        incremental_performance=True, max_tasks=100,
        heuristic_policy=HeuristicSearchPolicy.durable(adaptive=True),
        adaptive_policy=policy, ranking_service=service,
    ).run(**args)
    progress = result.graph.progress
    assert progress.records_planned == 24
    assert (
        progress.records_examined + progress.records_incomplete + progress.records_unattempted == 24
    )
    assert progress.retrieval_diagnostics.pruning_quality == "unvalidated"
    assert progress.retrieval_diagnostics.records_soft_pruned > 0
    restored = RankingService(service.policy, RankingModel(), state=result.ranking_state["service"],
                              adaptive_policy=policy)
    assert restored.snapshot() == result.ranking_state["service"]
    from app.schemas.retrieval_diagnostics import RetrievalDiagnostics

    assert "pruning_quality" not in RetrievalDiagnostics().model_dump(mode="json")
