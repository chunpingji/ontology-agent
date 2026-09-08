"""Benchmark artifact persistence and dependency guards without invoking a model."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config import settings
from app.evaluation import cmc_benchmark
from app.services.extraction import local_semantic_model, word_analysis, word_tree_summarizer
from app.services.extraction.docx_structure import ChapterNode, PageNode
from app.services.llm import local_client


def test_focus_path_cli_preserves_order_and_requires_quality_mode():
    args = cmc_benchmark.parser().parse_args([
        "run", "--prepared", "/unused/source", "--output", "/unused/result",
        "--mode", "quality_guided_summary", "--focus-path", "urn:describes", "urn:hasAPI",
    ])
    assert args.focus_path == ["urn:describes", "urn:hasAPI"]
    args.mode = "baseline"
    with pytest.raises(ValueError, match="focus_path_requires_quality_guided_mode"):
        cmc_benchmark.run(args)  # Must reject before reading/creating any artifacts.


@pytest.fixture
def summary_environment(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "source.docx").write_bytes(b"source for mocked parser")
    cmc_benchmark.write_json(prepared / "schema.json", {"urn:OriginalClass": {}})
    cmc_benchmark.write_json(prepared / "ir.json", {"analysis_id": "frozen-analysis"})
    document_hash = cmc_benchmark.digest_file(prepared / "source.docx")
    cmc_benchmark.write_json(prepared / "manifest.json", {
        "document_hash": document_hash, "source_filename": "source.docx",
        "runtime_hash": "frozen-runtime", "analysis_id": "frozen-analysis",
        "schema_hash": cmc_benchmark.digest_file(prepared / "schema.json"),
        "ir_hash": cmc_benchmark.digest_file(prepared / "ir.json"),
        "settings": {"local_llm_model_revision": "mock-model-revision"},
    })
    root = ChapterNode(node_id="root", node_type="document", heading="CMC", level=0)
    child = ChapterNode(node_id="child", node_type="section", heading="设备", level=1)
    root.children = [child]
    structure = SimpleNamespace(section_tree=root)
    monkeypatch.setattr(cmc_benchmark, "restore_settings", lambda manifest: [])
    monkeypatch.setattr(cmc_benchmark, "tree_digest", lambda *args: "frozen-runtime")
    monkeypatch.setattr(cmc_benchmark, "slot_snapshot", lambda: [])
    monkeypatch.setattr(cmc_benchmark, "emit", lambda *args, **kwargs: None)
    # The harness assigns these settings directly; register their prior values for cleanup.
    monkeypatch.setattr(settings, "llm_word_tree_summary_enabled", False)
    monkeypatch.setattr(settings, "word_tree_summary_timeout_s", 60)
    analyze = Mock(return_value=SimpleNamespace(
        structure=structure, ir=SimpleNamespace(analysis_id="frozen-analysis"),
    ))
    monkeypatch.setattr(word_analysis, "analyze_word_core", analyze)
    client = object()  # LocalModelClient factories are not required to expose close().
    factory = Mock(return_value=client)
    monkeypatch.setattr(local_client, "get_local_llm", factory)
    return SimpleNamespace(
        prepared=prepared, root=root, child=child, structure=structure, client=client,
        factory=factory, analyze=analyze, document_hash=document_hash,
        args=SimpleNamespace(prepared=str(prepared), timeout=120),
    )


def complete_batch(client, targets):
    for target in targets:
        target["metadata"].content_summary = f"summary of {target['node_id']}"
        target["metadata"].summary_status = "completed"
        target["metadata"].summary_source = "llm"


def target(node):
    return {"node_id": node.node_id, "metadata": node.layer_metadata, "material": node.heading}


def test_summary_factory_without_close_persists_each_batch_and_final_artifact(
    summary_environment, monkeypatch,
):
    env = summary_environment
    monkeypatch.setattr(word_tree_summarizer, "_apply_batch", complete_batch)

    def fake_summarize(structure, client, progress_fn):
        assert structure is env.structure
        assert client is env.client
        word_tree_summarizer._apply_batch(client, [target(env.child)])
        partial = cmc_benchmark.read_json(env.prepared / "summaries.partial.json")
        assert partial["metadata"]["child"]["content_summary"] == "summary of child"
        assert len(partial["batches"]) == 1
        assert partial["metadata"]["root"]["content_summary"] is None
        progress_fn("parent")
        word_tree_summarizer._apply_batch(client, [target(env.root)])

    monkeypatch.setattr(word_tree_summarizer, "summarize_word_tree", fake_summarize)
    cmc_benchmark.summarize(env.args)

    result = cmc_benchmark.read_json(env.prepared / "summaries.json")
    partial = cmc_benchmark.read_json(env.prepared / "summaries.partial.json")
    assert result["document_hash"] == env.document_hash
    assert result["model_identity"] == "mock-model-revision"
    assert result["source_counts"] == {"llm": 2}
    assert result["metadata"] == partial["metadata"]
    assert [batch["node_ids"] for batch in result["batches"]] == [["child"], ["root"]]
    assert all(batch["wall_seconds"] >= 0 for batch in result["batches"])
    assert word_tree_summarizer._apply_batch is complete_batch
    env.factory.assert_called_once_with()


@pytest.mark.parametrize("failure_location", ["batch", "orchestration"])
def test_summary_exception_preserves_partial_outputs_and_restores_batch_hook(
    summary_environment, monkeypatch, failure_location,
):
    env = summary_environment

    def original_batch(client, targets):
        complete_batch(client, targets)
        if failure_location == "batch":
            raise RuntimeError("unexpected summary failure")

    def fake_summarize(structure, client, progress_fn):
        word_tree_summarizer._apply_batch(client, [target(env.child)])
        raise RuntimeError("unexpected summary failure")

    monkeypatch.setattr(word_tree_summarizer, "_apply_batch", original_batch)
    monkeypatch.setattr(word_tree_summarizer, "summarize_word_tree", fake_summarize)
    with pytest.raises(RuntimeError, match="unexpected summary failure"):
        cmc_benchmark.summarize(env.args)

    assert word_tree_summarizer._apply_batch is original_batch
    assert not (env.prepared / "summaries.json").exists()
    partial = cmc_benchmark.read_json(env.prepared / "summaries.partial.json")
    assert partial["metadata"]["child"]["content_summary"] == "summary of child"
    assert partial["metadata"]["root"]["content_summary"] is None
    assert partial["source_counts"] == {"none": 1, "llm": 1}
    assert len(partial["batches"]) == 1
    assert partial["batches"][0]["node_ids"] == ["child"]


def test_summary_preserves_frozen_result_without_acquiring_model(summary_environment):
    env = summary_environment
    sentinel = {"metadata": {"root": "already frozen"}}
    cmc_benchmark.write_json(env.prepared / "summaries.json", sentinel)
    with pytest.raises(FileExistsError, match="already frozen"):
        cmc_benchmark.summarize(env.args)
    assert cmc_benchmark.read_json(env.prepared / "summaries.json") == sentinel
    env.factory.assert_not_called()
    env.analyze.assert_not_called()


def test_run_refuses_changed_prepared_schema_before_parser_or_model(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "source.docx").write_bytes(b"frozen source; parser must not run")
    cmc_benchmark.write_json(prepared / "schema.json", {"urn:OriginalClass": {}})
    cmc_benchmark.write_json(prepared / "ir.json", {"analysis_id": "frozen-analysis"})
    cmc_benchmark.write_json(prepared / "manifest.json", {
        "settings": {}, "runtime_hash": "frozen-runtime",
        "analysis_id": "frozen-analysis",
        "document_hash": cmc_benchmark.digest_file(prepared / "source.docx"),
        "schema_hash": cmc_benchmark.digest_file(prepared / "schema.json"),
        "ir_hash": cmc_benchmark.digest_file(prepared / "ir.json"),
    })
    cmc_benchmark.write_json(prepared / "schema.json", {"urn:ChangedClass": {}})
    monkeypatch.setattr(cmc_benchmark, "restore_settings", lambda manifest: None)
    monkeypatch.setattr(cmc_benchmark, "tree_digest", lambda *args: "frozen-runtime")
    analyze = Mock(side_effect=AssertionError("parser invoked before source validation"))
    configure = Mock(side_effect=AssertionError("model invoked before source validation"))
    monkeypatch.setattr(word_analysis, "analyze_word_core", analyze)
    monkeypatch.setattr(local_semantic_model, "configured_generic_runner", configure)
    args = SimpleNamespace(prepared=str(prepared), output=str(tmp_path / "run"), resume=False)

    with pytest.raises(ValueError, match="prepared ontology schema was modified"):
        cmc_benchmark.run(args)

    analyze.assert_not_called()
    configure.assert_not_called()


@pytest.mark.parametrize("modified", ["source.docx", "schema.json", "ir.json", "runtime"])
def test_summary_refuses_modified_dependencies_before_model_or_parser(
    summary_environment, monkeypatch, modified,
):
    env = summary_environment
    if modified == "runtime":
        monkeypatch.setattr(cmc_benchmark, "tree_digest", lambda *args: "different-runtime")
    else:
        (env.prepared / modified).write_bytes(b"modified after preparation")
    with pytest.raises(ValueError, match="differs|modified"):
        cmc_benchmark.summarize(env.args)
    env.analyze.assert_not_called()
    env.factory.assert_not_called()


def test_summary_refuses_changed_analysis_identity_before_model(summary_environment):
    env = summary_environment
    env.analyze.return_value.ir.analysis_id = "different-analysis"
    with pytest.raises(ValueError, match="no longer reproduces"):
        cmc_benchmark.summarize(env.args)
    env.factory.assert_not_called()


def test_partial_summary_preserves_page_work_before_any_chapter_is_processed(
    summary_environment, monkeypatch,
):
    env = summary_environment
    page = PageNode(node_id="child:page:1", ordinal_in_leaf=1,
                    physical_page_number=None, break_source=None)
    env.child.pages.append(page)
    monkeypatch.setattr(word_tree_summarizer, "_apply_batch", complete_batch)

    def fake_summarize(structure, client, progress_fn):
        word_tree_summarizer._apply_batch(client, [{
            "node_id": page.node_id, "metadata": page.page_metadata, "material": "page source",
        }])
        partial = cmc_benchmark.read_json(env.prepared / "summaries.partial.json")
        assert partial["page_metadata"][page.node_id]["summary_source"] == "llm"
        raise RuntimeError("interrupted before chapter summaries")

    monkeypatch.setattr(word_tree_summarizer, "summarize_word_tree", fake_summarize)
    with pytest.raises(RuntimeError, match="interrupted before chapter"):
        cmc_benchmark.summarize(env.args)
    partial = cmc_benchmark.read_json(env.prepared / "summaries.partial.json")
    assert partial["page_source_counts"] == {"llm": 1}
    assert partial["chapter_source_counts"] == {"none": 2}
    assert partial["source_counts"] == {"none": 2, "llm": 1}
    assert partial["page_metadata"][page.node_id]["content_summary"] == (
        "summary of child:page:1"
    )
    assert page.node_id not in partial["metadata"]


def test_prepare_settings_capture_effective_summary_and_total_timeout_controls():
    snapshot = cmc_benchmark.settings_snapshot()
    for key in (
        "evidence_total_timeout_s", "word_tree_summary_max_input_chars_per_node",
        "word_tree_summary_max_batch_chars", "word_tree_summary_max_nodes_per_batch",
    ):
        assert snapshot[key] == getattr(settings, key)


def test_legacy_manifest_explicitly_reports_unfrozen_settings(monkeypatch):
    events = Mock()
    monkeypatch.setattr(cmc_benchmark, "emit", events)
    monkeypatch.setattr(settings, "local_llm_enabled", settings.local_llm_enabled)
    missing = cmc_benchmark.restore_settings({"settings": {
        "local_llm_enabled": settings.local_llm_enabled,
    }})
    assert "evidence_total_timeout_s" in missing
    assert "word_tree_summary_max_nodes_per_batch" in missing
    assert "local_llm_enabled" not in missing
    assert events.call_args.args[0] == "legacy_manifest_settings_missing"
    assert events.call_args.kwargs["keys"] == missing


def test_interrupted_segment_marker_refuses_resume_even_with_previous_result(
    summary_environment, monkeypatch,
):
    env = summary_environment
    output = env.prepared / "experiment"
    args = SimpleNamespace(prepared=str(env.prepared), output=str(output), resume=False)

    def interrupted_segment(args, prepared, manifest, output, missing):
        assert (output / cmc_benchmark.RUN_IN_PROGRESS).is_file()
        cmc_benchmark.write_json(output / "checkpoint.json", {"attempt_count": 2})
        cmc_benchmark.write_json(output / "result.json", {"elapsed_seconds_total": 1})
        raise KeyboardInterrupt("hard interruption simulation")

    monkeypatch.setattr(cmc_benchmark, "_run_segment", interrupted_segment)
    with pytest.raises(KeyboardInterrupt):
        cmc_benchmark.run(args)
    assert (output / cmc_benchmark.RUN_IN_PROGRESS).is_file()
    segment = Mock(side_effect=AssertionError("unfinished segment must not resume"))
    monkeypatch.setattr(cmc_benchmark, "_run_segment", segment)
    args.resume = True
    with pytest.raises(ValueError, match="unfinished segment.*active process"):
        cmc_benchmark.run(args)
    segment.assert_not_called()


def test_successfully_persisted_segment_removes_marker_and_allows_resume(
    summary_environment, monkeypatch,
):
    env = summary_environment
    output = env.prepared / "experiment"
    args = SimpleNamespace(prepared=str(env.prepared), output=str(output), resume=False)
    calls = []

    def successful_segment(args, prepared, manifest, output, missing):
        assert (output / cmc_benchmark.RUN_IN_PROGRESS).is_file()
        calls.append(args.resume)
        cmc_benchmark.write_json(output / "checkpoint.json", {"attempt_count": len(calls)})
        cmc_benchmark.write_json(output / "result.json", {"elapsed_seconds_total": len(calls)})

    monkeypatch.setattr(cmc_benchmark, "_run_segment", successful_segment)
    cmc_benchmark.run(args)
    assert not (output / cmc_benchmark.RUN_IN_PROGRESS).exists()
    args.resume = True
    cmc_benchmark.run(args)
    assert calls == [False, True]
    assert not (output / cmc_benchmark.RUN_IN_PROGRESS).exists()


def test_legacy_checkpoint_without_finished_result_cannot_resume(
    summary_environment, monkeypatch,
):
    env = summary_environment
    output = env.prepared / "interrupted"
    output.mkdir()
    cmc_benchmark.write_json(output / "checkpoint.json", {"attempt_count": 3})
    segment = Mock(side_effect=AssertionError("incomplete legacy segment must not resume"))
    monkeypatch.setattr(cmc_benchmark, "_run_segment", segment)
    args = SimpleNamespace(prepared=str(env.prepared), output=str(output), resume=True)
    with pytest.raises(ValueError, match="successfully finalized checkpoint and result"):
        cmc_benchmark.run(args)
    segment.assert_not_called()
