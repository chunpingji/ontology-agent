"""Fork dependency and provenance checks never acquire a model or modify a parent."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.evaluation import fork_experiment as fork
from app.services.extraction.docx_structure import ChapterNode


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source = tmp_path / "parent"
    source.mkdir()
    runtime = tmp_path / "current_app"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("# new algorithm\n")
    (source / "source.docx").write_bytes(b"frozen document")
    (source / "ontology").mkdir()
    (source / "ontology" / "ontology.ttl").write_text("# frozen ontology\n")
    fork.write_json(source / "schema.json", {"classes": {"urn:CMC": {}}})
    doc_hash = fork.digest_file(source / "source.docx")
    fork.write_json(source / "ir.json", {"analysis_id": "analysis-1", "document_hash": doc_hash})
    old_settings = {key: f"frozen-{key}" for key in fork.CORE_MODEL_KEYS}
    old_settings["word_tree_summary_prompt_version"] = "summary-v1"
    current = {**old_settings, "evidence_total_timeout_s": 600}
    manifest = {
        "source_filename": "CMC.docx", "document_hash": doc_hash,
        "analysis_id": "analysis-1", "runtime_hash": "old-runtime",
        "schema_hash": fork.digest_file(source / "schema.json"),
        "ir_hash": fork.digest_file(source / "ir.json"),
        "ontology_hash": fork.tree_digest(source / "ontology", ".ttl"),
        "settings": old_settings, "parse_seconds": 3,
    }
    root = ChapterNode(node_id="document", node_type="document", heading="CMC", level=0)
    root.layer_metadata.content_hash = "chapter-content"
    summary = {
        "document_hash": doc_hash,
        "model_identity": old_settings["local_llm_model_revision"],
        "summary_prompt_version": "summary-v1", "generation_seconds": 951.2566180109861,
        "metadata": fork.summary_metadata(SimpleNamespace(section_tree=root)),
    }
    fork.write_json(source / "manifest.json", manifest)
    fork.write_json(source / "summaries.json", summary)
    fork.write_json(source / "reference.json", {"scoring_secret": "must not enter input"})
    analyze = Mock(return_value=SimpleNamespace(
        ir=SimpleNamespace(analysis_id="analysis-1"),
        structure=SimpleNamespace(section_tree=root),
    ))
    monkeypatch.setattr(fork, "current_runtime", lambda: runtime)
    monkeypatch.setattr(fork, "settings_snapshot", lambda: current)
    monkeypatch.setattr(fork, "analyze_source", analyze)
    return SimpleNamespace(source=source, output=tmp_path / "fork", runtime=runtime,
                           settings=current, manifest=manifest, summary=summary, analyze=analyze)


def test_fork_preserves_inputs_parent_and_paid_summary_cost(environment):
    env = environment
    before = {str(path.relative_to(env.source)): fork.digest_file(path)
              for path in env.source.rglob("*") if path.is_file()}
    result = fork.fork_experiment(env.source, env.output)
    for filename in (*fork.FROZEN_FILES, "reference.json", "summaries.json"):
        assert (env.output / filename).read_bytes() == (env.source / filename).read_bytes()
    assert result["runtime_hash"] == fork.tree_digest(env.runtime, ".py")
    assert result["runtime_hash"] != env.manifest["runtime_hash"]
    assert result["settings_provenance"]["newly_frozen_settings"] == {
        "evidence_total_timeout_s": 600,
    }
    summary = fork.read_json(env.output / "summaries.json")
    assert "analysis_id" not in summary
    assert summary["metadata"] == env.summary["metadata"]
    assert summary["generation_seconds"] == 951.2566180109861
    assert result["summary_reuse"]["new_generation_seconds"] == 0
    assert "legacy_missing_identity" in result["summary_reuse"]["analysis_id_validation"]
    assert result["parent_provenance"]["manifest_hash"] == before["manifest.json"]
    assert result["reference"]["usage"] == "scoring_only_never_model_input"
    assert "scoring_secret" not in (env.output / "summaries.json").read_text()
    assert not (env.output / "runtime" / "app" / "reference.json").exists()
    assert before == {str(path.relative_to(env.source)): fork.digest_file(path)
                      for path in env.source.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "filename", ["source.docx", "schema.json", "ir.json", "ontology/ontology.ttl"],
)
def test_fork_refuses_changed_parent_before_parsing(environment, filename):
    env = environment
    (env.source / filename).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        fork.fork_experiment(env.source, env.output)
    env.analyze.assert_not_called()
    assert not env.output.exists()


@pytest.mark.parametrize("key", fork.CORE_MODEL_KEYS)
def test_fork_refuses_different_current_model_identity(environment, key):
    env = environment
    env.settings[key] = "different"
    with pytest.raises(ValueError, match="core model setting differs"):
        fork.fork_experiment(env.source, env.output)
    env.analyze.assert_not_called()
    assert not env.output.exists()


@pytest.mark.parametrize(
    "field", ["document_hash", "analysis_id", "model_identity", "summary_prompt_version"],
)
def test_fork_refuses_summary_identity_mismatch(environment, field):
    env = environment
    env.summary[field] = "wrong"
    fork.write_json(env.source / "summaries.json", env.summary)
    with pytest.raises(ValueError, match="summary"):
        fork.fork_experiment(env.source, env.output)
    env.analyze.assert_not_called()


def test_legacy_summary_requires_reproduced_chapter_content(environment):
    env = environment
    env.summary["metadata"]["document"]["content_hash"] = "wrong"
    fork.write_json(env.source / "summaries.json", env.summary)
    with pytest.raises(ValueError, match="summary content_hash differs"):
        fork.fork_experiment(env.source, env.output)
    assert not env.output.exists()


def test_fork_refuses_changed_parser_identity(environment):
    env = environment
    env.analyze.return_value.ir.analysis_id = "different"
    with pytest.raises(ValueError, match="does not reproduce"):
        fork.fork_experiment(env.source, env.output)


def test_fork_never_overwrites_existing_destination(environment):
    env = environment
    env.output.mkdir()
    sentinel = env.output / "preserve.txt"
    sentinel.write_text("existing result")
    with pytest.raises(FileExistsError):
        fork.fork_experiment(env.source, env.output)
    assert sentinel.read_text() == "existing result"
    env.analyze.assert_not_called()


def test_fork_rejects_output_nested_in_parent(environment):
    env = environment
    with pytest.raises(ValueError, match="outside"):
        fork.fork_experiment(env.source, env.source / "nested")
    env.analyze.assert_not_called()


def test_cli_accepts_source_and_output(monkeypatch):
    action = Mock()
    monkeypatch.setattr(fork, "fork_experiment", action)
    fork.main(["--source", "/old", "--output", "/new"])
    action.assert_called_once_with("/old", "/new")
