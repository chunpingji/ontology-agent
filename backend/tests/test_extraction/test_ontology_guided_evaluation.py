"""Active evaluation uses the shared executor and an isolated score protocol."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.evaluation import cmc_benchmark
from app.evaluation.cmc_benchmark import ACTIVE_QUALITY_MODES, parser
from app.evaluation.ontology_guided_scorer import (
    EntityMatcher,
    EvidenceSpan,
    ExpertReview,
    OntologyGuidedReference,
    QualityThresholds,
    ReferenceEntity,
    ReferenceProperty,
    ReferenceRelationship,
    score_evaluation,
)
from app.evaluation.quality_guided_variant import build_quality_guided_variant
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from tests.test_extraction.test_ontology_guided_core import (
    APPEARANCE,
    DESCRIBES,
    PRODUCT,
    REPORT,
    FakeAdapter,
    ontology,
    sample,
)


def _evaluation(tmp_path, *, focus_path=()):
    analysis = sample(tmp_path)
    metadata = prepare_metadata(
        analysis.ir,
        section_tree=analysis.structure.section_tree.to_dict(),
        summary_version="evaluation-test",
    )
    calls = []
    runner = build_quality_guided_variant(
        ontology=ontology(),
        adapter=FakeAdapter(),
        focus_path=focus_path,
        phase1_section_limit=1,
        call_hook=calls.append,
    )
    result = runner.run(
        recognition_run_id="evaluation-run",
        ir=analysis.ir,
        metadata=metadata,
        root_class_iri=REPORT,
        root_class_label="报告",
        filename="source.docx",
    )
    return analysis, runner, result, calls


def test_active_quality_runner_is_a_thin_shared_executor_adapter(tmp_path):
    analysis, runner, result, calls = _evaluation(tmp_path, focus_path=(DESCRIBES,))

    assert isinstance(runner.executor, OntologyGuidedExecutor)
    assert result.executor_version == OntologyGuidedExecutor.version
    assert result.scope_mode == "focus_path"
    assert result.focus_path == [DESCRIBES]
    assert result.metadata_snapshot.analysis_id == analysis.ir.analysis_id
    assert result.adapter_calls == calls
    assert result.graph.progress.model_calls == len(calls)
    assert {item.predicate_iri for item in result.graph.coverage} == {
        DESCRIBES,
        "urn:appearance",
    }
    assert result.graph.progress.completion == "in_scope_complete"


def test_active_builder_rejects_legacy_positional_runner_shape(tmp_path):
    analysis = sample(tmp_path)
    with pytest.raises(TypeError):
        build_quality_guided_variant(object(), analysis.ir)


def test_focus_path_must_exist_in_each_frozen_local_menu(tmp_path):
    analysis = sample(tmp_path)
    metadata = prepare_metadata(
        analysis.ir,
        section_tree=analysis.structure.section_tree.to_dict(),
        summary_version="evaluation-test",
    )
    runner = build_quality_guided_variant(
        ontology=ontology(), adapter=FakeAdapter(), focus_path=("urn:not-declared",)
    )
    with pytest.raises(ValueError, match="outside the frozen local menu"):
        runner.run(
            recognition_run_id="evaluation-run",
            ir=analysis.ir,
            metadata=metadata,
            root_class_iri=REPORT,
            root_class_label="报告",
            filename="source.docx",
        )


def test_approved_reference_scores_exact_tuple_and_forbidden_assertions(tmp_path):
    analysis, _runner, result, _calls = _evaluation(tmp_path, focus_path=(DESCRIBES,))
    unit = next(unit for unit in analysis.ir.evidence_units if "明确描述产品甲" in unit.text)
    anchor = analysis.ir.anchor(unit.evidence_id, 0, len(unit.text))
    evidence_sets = [[EvidenceSpan(evidence_id=anchor.evidence_id,
                                  start=anchor.span_start, end=anchor.span_end)]]
    eligible_edges = [
        edge.model_copy(update={"structural_valid": True, "policy_eligible": True,
                                "evidence_refs": [anchor]})
        for edge in result.graph.edges
    ]
    result = result.model_copy(
        update={"graph": result.graph.model_copy(update={
            "edges": eligible_edges,
            "nodes": [node.model_copy(update={"evidence_refs": [anchor]})
                      for node in result.graph.nodes],
            "properties": [item.model_copy(update={"evidence_refs": [anchor]})
                           for item in result.graph.properties],
        })}
    )
    reference = OntologyGuidedReference(
        reference_id="expert-reference",
        document_hash=analysis.ir.document_hash,
        ontology_hash=result.ontology_snapshot.ontology_hash,
        root_class_iri=REPORT,
        scope_mode="focus_path",
        focus_path=[DESCRIBES],
        scored_predicate_iris=[DESCRIBES, APPEARANCE],
        entities=[
            ReferenceEntity(entity=EntityMatcher(class_iri=PRODUCT, label="产品甲"),
                            allowed_evidence_sets=evidence_sets),
            ReferenceEntity(
                entity=EntityMatcher(class_iri=PRODUCT, label="产品乙"),
                expectation="forbidden",
            ),
        ],
        relationships=[
            ReferenceRelationship(
                subject=EntityMatcher(class_iri=REPORT, document_root=True),
                predicate_iri=DESCRIBES,
                object=EntityMatcher(class_iri=PRODUCT, label="产品甲"),
                allowed_evidence_sets=evidence_sets,
            )
        ],
        properties=[ReferenceProperty(
            subject=EntityMatcher(class_iri=PRODUCT, label="产品甲"),
            predicate_iri=APPEARANCE, value="白色片剂", allowed_evidence_sets=evidence_sets,
        )],
        expert_review=ExpertReview(
            status="approved",
            reviewer="independent-domain-expert",
            reviewed_at="2026-09-08T00:00:00Z",
            reference_hash="a" * 64,
        ),
        quality_thresholds=QualityThresholds(
            source="test-only threshold fixture",
            minimum_overall_precision=1,
            minimum_overall_recall=1,
            minimum_overall_f1=1,
        ),
    )

    score = score_evaluation(result, reference, ir=analysis.ir)

    assert score["metrics"]["overall"] == {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert score["forbidden_assertions"]["accepted_count"] == 0
    assert score["formal_quality_gate"] == "pass"

    draft = reference.model_copy(update={"expert_review": ExpertReview(status="draft")})
    with pytest.raises(ValueError, match="approved expert reference"):
        score_evaluation(result, draft, ir=analysis.ir)


def test_benchmark_defaults_and_active_aliases_never_name_legacy_runner():
    default = parser().parse_args(["run", "--prepared", "/prepared", "--output", "/output"])
    assert default.mode == "quality_guided"
    assert ACTIVE_QUALITY_MODES == {"quality_guided", "quality_guided_summary"}
    legacy = parser().parse_args(
        [
            "run",
            "--prepared",
            "/prepared",
            "--output",
            "/output",
            "--mode",
            "legacy_quality_guided_summary",
        ]
    )
    assert legacy.mode.startswith("legacy_")


def test_active_benchmark_persists_versioned_unscored_manifest(tmp_path, monkeypatch):
    analysis = sample(tmp_path)
    snapshot = ontology()
    prepared = tmp_path / "prepared"
    output = tmp_path / "output"
    prepared.mkdir()
    output.mkdir()
    cmc_benchmark.write_json(prepared / "ontology_snapshot.json", snapshot.model_dump(mode="json"))
    cmc_benchmark.write_json(prepared / "ir.json", analysis.ir.model_dump(mode="json"))
    cmc_benchmark.write_json(prepared / "manifest.json", {"fixture": True})
    manifest = {
        "source_filename": "source.docx",
        "document_hash": analysis.ir.document_hash,
        "ontology_snapshot_id": snapshot.snapshot_id,
        "class_iri": REPORT,
        "ontology_semantic_hash": snapshot.ontology_hash,
        "ontology_snapshot_file_hash": cmc_benchmark.digest_file(
            prepared / "ontology_snapshot.json"
        ),
        "ir_hash": cmc_benchmark.digest_file(prepared / "ir.json"),
        "runtime_hash": "frozen-runtime",
        "settings": {"word_tree_summary_prompt_version": "test-summary-v1"},
    }
    args = SimpleNamespace(
        mode="quality_guided",
        timeout=5,
        timeout_retries=0,
        deadline_seconds=None,
        pause_after=None,
        stop_file=None,
        run_id="benchmark-active-run",
        focus_path=None,
        max_sections_per_predicate=1,
    )
    monkeypatch.setattr(cmc_benchmark, "CMC_CLASS", REPORT)
    monkeypatch.setattr(cmc_benchmark, "slot_snapshot", lambda: [])
    monkeypatch.setattr(cmc_benchmark, "emit", lambda *args, **kwargs: None)
    from app.services.extraction.ontology_guided import model_adapter

    monkeypatch.setattr(model_adapter, "configured_model_adapter", lambda: FakeAdapter())

    cmc_benchmark._run_ontology_guided_segment(
        args,
        prepared,
        manifest,
        output,
        [],
        analysis,
        time.perf_counter(),
        0.01,
    )

    run_manifest = cmc_benchmark.read_json(output / "result.json")
    assert run_manifest["schema_version"] == "ontology-guided-evaluation-manifest-v1"
    assert run_manifest["legacy_runner_used"] is False
    ablation = cmc_benchmark.read_json(output / "ablation.json")
    assert ablation["schema_version"] == "semantic-ranking-ablation-v1"
    assert ablation["shared"]["document_hash"] == analysis.ir.document_hash
    assert ablation["factors"]["mode"] == "deterministic"
    assert "ranking.json" in run_manifest["output_hashes"]
    assert "costs.json" in run_manifest["output_hashes"]
    assert (
        run_manifest["run_fingerprint"]
        == cmc_benchmark.read_json(output / "run.json")["run_fingerprint"]
    )
    assert run_manifest["quality_gate"] == {
        "engineering_run": "executed",
        "expert_gold": "pending_not_supplied",
        "independent_real_runs": {"required": 3, "represented_here": 1},
        "formal_score": "pending",
        "release": "blocked_pending_expert_gold_and_three_independent_runs",
    }
    assert run_manifest["reference_is_recognition_input"] is False

    cmc_benchmark.score(SimpleNamespace(prepared=str(prepared), run=str(output), reference=None))
    pending_score = cmc_benchmark.read_json(output / "metrics.json")
    assert pending_score["status"] == "pending_expert_reference"
    assert pending_score["formal_quality_gate"] == "not_run"


def test_run_segment_dispatches_active_mode_before_legacy_runner_setup(tmp_path, monkeypatch):
    analysis = sample(tmp_path)
    observed = []
    from app.services.extraction import word_analysis

    monkeypatch.setattr(word_analysis, "analyze_word_core", lambda *args, **kwargs: analysis)
    monkeypatch.setattr(
        cmc_benchmark,
        "_run_ontology_guided_segment",
        lambda *values: observed.append(values),
    )
    args = SimpleNamespace(mode="quality_guided")
    manifest = {
        "source_filename": "source.docx",
        "analysis_id": analysis.ir.analysis_id,
    }

    cmc_benchmark._run_segment(
        args,
        tmp_path,
        manifest,
        tmp_path / "output",
        [],
    )

    assert len(observed) == 1
