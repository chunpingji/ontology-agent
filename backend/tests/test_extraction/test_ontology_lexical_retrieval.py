"""Ontology annotations affect retrieval inputs, never evidence or entity identity."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.adaptive_retrieval import (
    AdaptivePolicy,
    CalibrationProfile,
)
from app.services.extraction.ontology_guided.contracts import (
    ONTOLOGY_LEXICAL_SNAPSHOT_VERSION,
    OntologySnapshot,
    RangeClass,
    SlotSpec,
)
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.heuristic_search import HeuristicSearchPolicy
from app.services.extraction.ontology_guided.lexical_query import (
    LEXICAL_QUERY_VERSION,
    LEXICAL_SELECTION_VERSION,
    select_query_vocabulary,
)
from app.services.extraction.ontology_guided.ontology_lexical import (
    RDFS_LABEL_IRI,
    SKOS_ALT_LABEL_IRI,
    build_lexical_context,
)
from app.services.extraction.ontology_guided.retrieval import plan_slot
from app.services.extraction.ontology_guided.retrieval_query import (
    SubjectSlotQuery,
    build_subject_queries,
)
from app.services.extraction.ontology_guided.semantic_reranker import RankingPolicy, RankingService
from app.services.extraction.ontology_guided.semantic_retrieval import query_terms, sparse_scores
from tests.test_extraction.test_ontology_guided_core import DESCRIBES, PRODUCT, REPORT, ontology
from tests.test_extraction.test_semantic_ranking import RankingModel, setup_slot
from tests.test_extraction.test_semantic_ranking_execution import NoFactsAdapter


def term(text, predicate=RDFS_LABEL_IRI, language=None):
    return {"text": text, "language": language, "predicate_iri": predicate}


def lexical_snapshot(annotations=None):
    old = ontology()
    context = build_lexical_context(annotations if annotations is not None else {
        REPORT: [term("报告", language="zh"), term("Report", language="en")],
        DESCRIBES: [term("描述产品"), term("记述制剂", SKOS_ALT_LABEL_IRI, "zh")],
        PRODUCT: [term("产品"), term("制剂", SKOS_ALT_LABEL_IRI, "zh"),
                  term("Drug product", SKOS_ALT_LABEL_IRI, "en")],
        "urn:unrelated": [term("不相关概念词", SKOS_ALT_LABEL_IRI)],
    })
    return OntologySnapshot(
        snapshot_id="lexical-test", version=ONTOLOGY_LEXICAL_SNAPSHOT_VERSION,
        ontology_hash=evidence_hash({"classes": old.classes, "lexical_context": context}),
        classes=old.classes, lexical_context=context, created_from="frozen_fixture",
    )


def query_args(args):
    return {"subject": args["plan"].subject, **{
        key: value for key, value in args.items()
        if key not in {"plan", "metadata", "permission_scope"}
    }}


def bind_snapshot(args, snapshot):
    return {**args, "ontology": snapshot, "plan": plan_slot(
        args["plan"].subject, args["predicate"], args["index"], args["metadata"],
        ontology_hash=snapshot.ontology_hash,
    )}


class CapturingModel(RankingModel):
    def __init__(self):
        super().__init__()
        self.embedded = []
        self.pairs = []

    def embed(self, texts):
        self.embedded.extend(texts)
        return super().embed(texts)

    def score_pairs(self, pairs):
        self.pairs.extend(pairs)
        return super().score_pairs(pairs)


@pytest.mark.parametrize("predicates", [
    [RDFS_LABEL_IRI], [SKOS_ALT_LABEL_IRI], [RDFS_LABEL_IRI, SKOS_ALT_LABEL_IRI],
])
def test_either_source_and_union_reach_queries_with_provenance(tmp_path, predicates):
    snapshot = lexical_snapshot({PRODUCT: [term("制剂", p, "zh") for p in predicates]})
    args = bind_snapshot(setup_slot(tmp_path), snapshot)
    for query in build_subject_queries(**query_args(args)):
        assert query.query_version == LEXICAL_QUERY_VERSION
        payload = json.loads(query.model_text)
        assert payload["allowed_object_types"][0]["terms"] == ["制剂"]
        assert payload["subject_mentions"] == []
        assert not query.trusted_context
        selected = query.lexical_selection["selected"]
        assert len(selected) == 1
        assert {item["predicate_iri"] for item in selected[0]["sources"]} == set(predicates)
        assert all(item["language"] == "zh" for item in selected[0]["sources"])


def test_scoped_terms_reach_both_models_and_sparse_search(tmp_path):
    args = bind_snapshot(setup_slot(tmp_path), lexical_snapshot())
    model = CapturingModel()
    service = RankingService(RankingPolicy(mode="semantic"), model)
    epoch = service.prepare_next_epoch(**args)
    assert epoch.status == "ready"
    expected = build_subject_queries(**query_args(args))
    for query in expected:
        assert query.model_text in model.embedded
        assert any(left == query.model_text for left, _ in model.pairs)
        assert "不相关概念词" not in query.model_text
        assert "记述制剂" in query.model_text and "Drug product" in query.model_text
        assert json.loads(query.model_text)["subject_mentions"] == []
        assert not query.trusted_context
        assert sparse_scores({"alias": "本文记述制剂", "other": "温度记录"},
                             query_terms(query))["alias"] > 0
    assert all("lexical_selection" in item for item in epoch.queries)


def test_shared_text_preserves_separate_concept_scope_and_language(tmp_path):
    snapshot = lexical_snapshot({
        REPORT: [term("共享词", language="zh")],
        PRODUCT: [term("共享词", SKOS_ALT_LABEL_IRI, "en"), term("共享词", language="zh")],
        "urn:outside": [term("外部词")],
    })
    queries = build_subject_queries(**query_args(bind_snapshot(setup_slot(tmp_path), snapshot)))
    selection = queries[0].lexical_selection["selected"]
    assert {item["iri"] for item in selection} == {REPORT, PRODUCT}
    assert {item["role"] for item in selection} == {"subject_class", "object_type"}
    assert len(next(item for item in selection if item["iri"] == PRODUCT)["sources"]) == 2
    assert all(not item.source_refs for item in queries[0].trusted_context)


def test_definition_remains_background_without_becoming_lexical_terms(tmp_path):
    args = bind_snapshot(setup_slot(tmp_path), lexical_snapshot())
    args["predicate"] = args["predicate"].model_copy(update={"description": "独有定义背景词"})
    query = build_subject_queries(**query_args(args))[0]
    assert "独有定义背景词" in json.loads(query.model_text)["predicate"]["definition"]
    assert "独有定义背景词" not in str(query.lexical_selection)
    assert "独有定义背景词" not in query_terms(query)


def test_selection_bounds_preserve_full_snapshot_without_truncating_terms():
    annotations = {
        DESCRIBES: [term(f"{i:02d}" + "a" * 68) for i in range(10)] + [term("x" * 81)],
        REPORT: [term("z" * 80), term("尾项")],
        PRODUCT: [term("合法对象术语")],
    }
    snapshot = lexical_snapshot(annotations)
    before = snapshot.model_dump_json()
    selection = select_query_vocabulary(snapshot, subject_class_iri=REPORT,
                                        predicate_iri=DESCRIBES, target_iris=[PRODUCT])
    selected = selection["selected"]
    assert sum(len(item["text"]) for item in selected) == 640
    assert max(Counter(item["iri"] for item in selected).values()) == 8
    assert all(len(item["text"]) <= 80 for item in selected)
    source_texts = {item["text"] for values in annotations.values() for item in values}
    assert all(item["text"] in source_texts for item in selected)
    counts = Counter()
    for item in selection["omitted"]:
        counts.update(item["counts"])
    assert counts == {"concept_limit": 2, "term_too_long": 1, "query_character_budget": 2}
    assert snapshot.model_dump_json() == before
    assert len(snapshot.lexical_context.annotations[DESCRIBES]) == 11


@pytest.mark.parametrize("literal", [False, True])
def test_omitted_alias_cannot_reenter_via_legacy_display_fields(tmp_path, literal):
    long_alias = "超长别名" * 30
    snapshot = lexical_snapshot({
        iri: [term(long_alias, SKOS_ALT_LABEL_IRI)] for iri in (REPORT, DESCRIBES, PRODUCT)
    })
    args = bind_snapshot(setup_slot(tmp_path), snapshot)
    args["subject_node"] = args["subject_node"].model_copy(update={"class_label": long_alias})
    args["predicate"] = (
        SlotSpec(iri=DESCRIBES, label=long_alias) if literal else
        args["predicate"].model_copy(update={
            "label": long_alias, "range_classes": [RangeClass(iri=PRODUCT, label=long_alias)],
        })
    )
    query = build_subject_queries(**query_args(args))[0]
    assert long_alias not in query.model_text
    assert long_alias not in query_terms(query)
    assert query.lexical_selection["selected"] == []
    assert len(query.lexical_selection["omitted"]) == (2 if literal else 3)


def test_alias_edit_invalidates_query_and_durable_epoch(tmp_path):
    args = bind_snapshot(setup_slot(tmp_path), lexical_snapshot())
    model = CapturingModel()
    service = RankingService(RankingPolicy(mode="semantic"), model)
    epoch = service.commit_epoch(service.prepare_next_epoch(**args))
    service.validate_epoch(epoch, **args)
    # Restoring a prepared/committed epoch must use the same ontology context.
    restored = RankingService(RankingPolicy(mode="semantic"), model, state=service.snapshot())
    restored.prepare_next_epoch(**args)
    annotations = args["ontology"].lexical_context.annotations
    changed = lexical_snapshot({**annotations, PRODUCT: [*annotations[PRODUCT], term("新别称")]})
    changed_args = bind_snapshot(args, changed)
    before = len(model.calls)
    assert build_subject_queries(**query_args(args))[0].query_dependency_hash != (
        build_subject_queries(**query_args(changed_args))[0].query_dependency_hash
    )
    with pytest.raises(ValueError, match="dependencies"):
        service.validate_epoch(epoch, **changed_args)
    with pytest.raises(ValueError, match="ontology"):
        service.prepare_next_epoch(**{**args, "ontology": changed})
    assert len(model.calls) == before


def test_legacy_query_dump_hash_and_replay_remain_identical(tmp_path):
    args = setup_slot(tmp_path)
    old = build_subject_queries(**query_args(args))
    explicit_legacy = build_subject_queries(**query_args(args), ontology=ontology())
    assert [query.model_dump(mode="json") for query in old] == [
        query.model_dump(mode="json") for query in explicit_legacy
    ]
    for query in old:
        raw = query.model_dump(mode="json")
        assert "lexical_selection" not in raw and query.query_version == "subject-slot-query-v1"
        assert SubjectSlotQuery.model_validate(raw).model_dump(mode="json") == raw
        assert query.query_dependency_hash == evidence_hash({
            "subject": args["plan"].subject, "predicate": args["predicate"], "trusted": [],
            "dependencies": [], "root": args["root_ref"], "source": args["index"].ir.document_hash,
        })


def calibration(snapshot=None):
    policy = AdaptivePolicy(mode="observation")
    return CalibrationProfile(
        model_hash=evidence_hash(RankingModel.identity), view_version=policy.view_version,
        view_configuration_hash=evidence_hash({key: getattr(policy, key) for key in (
            "view_version", "sibling_limit", "ancestor_limit", "group_member_limit",
        )}),
        predicate_iris=[DESCRIBES], dense_thresholds={"discover": -2, "counterevidence": -2},
        self_thresholds={"discover": -2, "counterevidence": -2},
        group_thresholds={"discover": -2, "counterevidence": -2},
        rerank_thresholds={"discover": -8, "counterevidence": -8},
        sample_manifest_hash="a" * 64, expert_review_hash="b" * 64,
        **({"query_version": LEXICAL_QUERY_VERSION, "ontology_hash": snapshot.ontology_hash,
            "lexical_context_hash": snapshot.lexical_context.context_hash,
            "lexical_selection_version": LEXICAL_SELECTION_VERSION} if snapshot else {}),
    )


def test_calibration_requires_exact_query_ontology_and_selection_binding(tmp_path):
    snapshot = lexical_snapshot()
    old = calibration()
    raw = old.model_dump(mode="json")
    assert "ontology_hash" not in raw and "lexical_context_hash" not in raw
    assert "lexical_selection_version" not in raw
    assert CalibrationProfile.model_validate(raw).model_dump(mode="json") == raw
    assert old.profile_hash == evidence_hash({k: v for k, v in raw.items() if k != "profile_hash"})
    model = CapturingModel()
    stale = RankingService(RankingPolicy(mode="semantic"), model,
                           adaptive_policy=AdaptivePolicy(mode="trial", calibration=old))
    with pytest.raises(ValueError, match="lexical query changed"):
        stale.prepare_next_epoch(**bind_snapshot(setup_slot(tmp_path), snapshot))
    assert not model.calls
    current = AdaptivePolicy(mode="trial", calibration=calibration(snapshot))
    current.validate_ontology_context(snapshot)
    with pytest.raises(ValueError, match="frozen lexical ontology"):
        current.validate_ontology_context(ontology())
    with pytest.raises(ValueError, match="lexical query changed"):
        current.validate_ontology_context(lexical_snapshot({PRODUCT: [term("新词")]}))
    with pytest.raises(ValueError, match="bindings"):
        CalibrationProfile.model_validate({**raw, "profile_hash": "", "query_version":
                                           LEXICAL_QUERY_VERSION})


@pytest.mark.parametrize("next_cutoff", [-4.0, -2.5])
def test_lexical_trial_uses_manual_cutoff_and_preserves_frozen_policy(tmp_path, next_cutoff):
    from app.config import Settings
    from app.services.document_analysis.adaptive_configuration import configured_adaptive_policy

    snapshot = lexical_snapshot()
    raw = calibration(snapshot).model_dump(mode="json", exclude={"profile_hash"})
    raw["rerank_thresholds"] = dict.fromkeys(("discover", "counterevidence"), -3.0)
    path = tmp_path / "manual-thresholds.json"
    profile = CalibrationProfile.model_validate(raw)
    path.write_text(profile.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, document_analysis_adaptive_retrieval_mode="trial",
                        document_analysis_adaptive_calibration_path=str(path))
    frozen = configured_adaptive_policy(settings)
    frozen.validate_ontology_context(snapshot)
    assert frozen.calibration.rerank_thresholds == raw["rerank_thresholds"]
    assert frozen.calibration.quality_status == "development"
    with pytest.raises(ValueError, match="isolated"):
        AdaptivePolicy(mode="enforce", calibration=frozen.calibration)

    raw["rerank_thresholds"] = dict.fromkeys(("discover", "counterevidence"), next_cutoff)
    adjusted = CalibrationProfile.model_validate(raw)
    path.write_text(adjusted.model_dump_json(), encoding="utf-8")
    assert configured_adaptive_policy(settings).calibration.rerank_thresholds == (
        raw["rerank_thresholds"]
    )
    assert frozen.calibration.rerank_thresholds == {"discover": -3.0, "counterevidence": -3.0}
    assert adjusted.profile_hash != frozen.calibration.profile_hash


@pytest.mark.parametrize("adaptive", [False, True])
def test_executor_passes_frozen_vocabulary_through_ranking_context(tmp_path, adaptive):
    snapshot = lexical_snapshot()
    args = setup_slot(tmp_path)
    model = CapturingModel()
    policy = AdaptivePolicy(mode="enhanced") if adaptive else None
    result = OntologyGuidedExecutor(
        ontology=snapshot, engine=object(), adapter=NoFactsAdapter(),
        ranking_service=RankingService(
            RankingPolicy(mode="semantic"), model, adaptive_policy=policy,
        ),
        **({"adaptive_policy": policy, "incremental_performance": True,
            "evidence_repair": True,
            "heuristic_policy": HeuristicSearchPolicy.durable(adaptive=True)} if adaptive else {}),
    ).run(
        recognition_run_id="lexical-executor", run_fingerprint="lexical-fingerprint",
        ir=args["index"].ir, metadata=args["metadata"], root_class_iri=REPORT,
        root_class_label="报告", filename="source.docx",
    )
    assert model.pairs
    assert all("记述制剂" in query for query, _ in model.pairs)
    assert all(json.loads(query)["subject_mentions"] == [] for query, _ in model.pairs)
    epochs = result.ranking_state["service"]["epochs"]
    assert epochs and all(query["query_version"] == LEXICAL_QUERY_VERSION
                          for epoch in epochs for query in epoch["queries"])
    assert not result.graph.edges and not result.graph.properties
