"""Boundary model identity, request budget and frozen GLiNER2.5 tool inputs."""

import hashlib
import shutil
from copy import deepcopy

import pytest

from app.evaluation import schema_card_gliner2 as runner
from app.evaluation.schema_card_probe import CMC, write
from app.evaluation.schema_card_tools import plan_schema


def prepared_scope(tmp_path, *, ner_status="completed"):
    directory = tmp_path / "scope"
    directory.mkdir()
    (directory / "plan").mkdir()
    write(directory / "sources.json", {})
    write(directory / "plan/proposal.json", {
        "get_schema_card": [], "inspect_evidence": [], "propose_mentions": [],
    })
    write(directory / "plan/schema.json", plan_schema({}, []))
    write(directory / "tools.json", {
        "source_spans": {}, "propose_mentions": {"execution_status": ner_status, "spans": []},
    })
    catalog = {CMC: {"class_iri": CMC, "label": "CMC报告", "properties": [],
                     "allowed_relations": []}}
    return directory, catalog


def model_reply(called, *, fail_stage=None):
    def invoke(client, directory, run_id, scope, stage, prompt, payload, schema, calls):
        called.append(stage)
        calls.append({"stage": stage, "seconds": 0.1})
        if stage == fail_stage:
            raise ValueError("model_timeout")
        calls[-1].update(finish_reason="stop", usage={"prompt_tokens": 10,
                                                    "completion_tokens": 5})
        if stage == "candidates":
            return {"subjects": {"document": {
                "anchor": {"ref": "", "quote": "", "span_id": ""},
                "attributes": {}, "relations": {},
            }}, "observations": []}
        assert stage == "verification"
        return {"types": {}, "claims": {}, "observations": {}}
    return invoke


def test_frozen_plan_is_reused_and_only_two_new_stages_run(tmp_path, monkeypatch):
    directory, catalog = prepared_scope(tmp_path)
    plan_bytes = (directory / "plan/proposal.json").read_bytes()
    called = []
    monkeypatch.setattr(runner, "invoke", model_reply(called))
    result = runner.run_candidate_case(None, directory, "test", catalog)
    assert result["status"] == "complete", result
    assert called == ["candidates", "verification"]
    assert result["contract_passes"] == len(result["calls"]) == 2
    assert result["plan_reused"] is True
    assert (directory / "plan/proposal.json").read_bytes() == plan_bytes


@pytest.mark.parametrize("stage,expected", [
    ("candidates", ["candidates"]), ("verification", ["candidates", "verification"]),
])
def test_timeout_is_not_retried_and_remains_in_call_budget(tmp_path, monkeypatch, stage, expected):
    directory, catalog = prepared_scope(tmp_path)
    called = []
    monkeypatch.setattr(runner, "invoke", model_reply(called, fail_stage=stage))
    result = runner.run_candidate_case(None, directory, "test", catalog)
    assert result["status"] == "failed"
    assert result["failure_stage"] == stage and result["error_code"] == "model_timeout"
    assert called == expected and len(result["calls"]) == len(expected)
    assert result["accepted"] == []
    summary = runner.summarize_c([result])
    assert summary["usage_missing_calls"] == 1
    assert summary["new_http_calls"] == len(expected)


def test_incomplete_ner_prevents_qwen_requests(tmp_path, monkeypatch):
    directory, catalog = prepared_scope(tmp_path, ner_status="partial")
    called = []
    monkeypatch.setattr(runner, "invoke", model_reply(called))
    result = runner.run_candidate_case(None, directory, "test", catalog)
    assert result["error_code"] == "gliner2_requested_but_incomplete"
    assert result["status"] == "failed" and result["calls"] == called == []


def test_third_request_is_blocked_before_shared_client(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(runner, "invoke", model_reply(called))
    with pytest.raises(ValueError, match="request_budget_exhausted"):
        runner.invoke_bounded(None, tmp_path, "test", "scope", "verification", "", {}, {},
                              [{"stage": "candidates"}, {"stage": "verification"}])
    assert called == []


def test_qwen_projection_keeps_all_mentions_without_batch_diagnostic_duplication():
    spans = [{"id": "m1", "ref": "r1", "start": 0, "end": 2, "text": "药物", "score": 0.6}]
    tools = {"source_spans": {"m1": spans[0]}, "inspect_evidence": {"units": []},
             "propose_mentions": {"spans": spans, "groups": {"entities": spans},
                                  "chunks": [{"labels": ["药物名称"]}] * 100,
                                  "backend": "gliner2.5", "architecture": "boundary",
                                  "execution_status": "completed", "fact_eligible": False}}
    before = deepcopy(tools)
    view = runner.model_tool_view(tools)
    assert view["propose_mentions"]["spans"] == spans
    assert view["source_spans"] == tools["source_spans"]
    assert "chunks" not in view["propose_mentions"] and "groups" not in view["propose_mentions"]
    assert view["propose_mentions"]["backend"] == "gliner2.5"
    assert view["propose_mentions"]["architecture"] == "boundary"
    assert tools == before


def local_checkpoint(tmp_path, *, architecture="boundary"):
    for name in runner.REQUIRED_MODEL_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "config.json":
            write(path, {"architecture": architecture})
        elif name.endswith(".json"):
            write(path, {})
        else:
            path.write_bytes(b"frozen test weight placeholder")
    manifest = {"repo": runner.MODEL_REPO, "revision": runner.MODEL_REVISION, "files": [
        {"path": name, "bytes": (tmp_path / name).stat().st_size,
         "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
        for name in sorted(runner.REQUIRED_MODEL_FILES)
    ]}
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    return manifest


def test_local_model_manifest_rejects_file_changes_and_path_escape(tmp_path):
    manifest = local_checkpoint(tmp_path)
    assert runner.verify_model_files(tmp_path) == manifest
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest_mismatch"):
        runner.verify_model_files(tmp_path)
    changed = deepcopy(manifest)
    changed["files"][0]["path"] = "../outside"
    write(tmp_path / "DOWNLOAD-MANIFEST.json", changed)
    with pytest.raises(ValueError, match="outside_directory"):
        runner.verify_model_files(tmp_path)


def test_model_identity_is_fixed_to_multilingual_boundary_checkpoint(tmp_path):
    assert runner.MODEL_REPO == "fastino/gliner2.5-multi-v1"
    assert runner.MODEL_REVISION == "aaecfe45db1d828c963717054ccb868e8ad1f1d5"
    assert runner.BACKEND == "gliner2.5" and runner.ARCHITECTURE == "boundary"
    assert runner.MAX_MODEL_CALLS == 16 and runner.LABELS_PER_BATCH == 1
    manifest = local_checkpoint(tmp_path)
    manifest["repo"] = "fastino/gliner2-multi-v1"
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="frozen_model_identity_mismatch"):
        runner.verify_model_files(tmp_path)


@pytest.mark.parametrize("files", [[], None, {}])
def test_model_manifest_cannot_validate_without_file_inventory(tmp_path, files):
    manifest = local_checkpoint(tmp_path)
    manifest["files"] = files
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="manifest_files_empty"):
        runner.verify_model_files(tmp_path)


@pytest.mark.parametrize("required", sorted(runner.REQUIRED_MODEL_FILES))
def test_required_artifacts_must_be_in_manifest_even_if_present_on_disk(tmp_path, required):
    manifest = local_checkpoint(tmp_path)
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != required]
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="required_files_missing"):
        runner.verify_model_files(tmp_path)


def test_manifest_rejects_repeated_and_alias_paths(tmp_path):
    manifest = local_checkpoint(tmp_path)
    manifest["files"].append(deepcopy(manifest["files"][0]))
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="duplicate_path"):
        runner.verify_model_files(tmp_path)
    manifest["files"][-1]["path"] = "./config.json"
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="path_not_canonical"):
        runner.verify_model_files(tmp_path)


def test_manifest_rejects_missing_actual_file(tmp_path):
    local_checkpoint(tmp_path)
    (tmp_path / "tokenizer.json").unlink()
    with pytest.raises(ValueError, match="model_file_missing:tokenizer.json"):
        runner.verify_model_files(tmp_path)


@pytest.mark.parametrize("entry_change", [
    {"bytes": 0}, {"bytes": True}, {"sha256": ""}, {"sha256": "z" * 64}, {"path": ""},
])
def test_manifest_rejects_empty_or_invalid_file_identity(tmp_path, entry_change):
    manifest = local_checkpoint(tmp_path)
    manifest["files"][0].update(entry_change)
    write(tmp_path / "DOWNLOAD-MANIFEST.json", manifest)
    with pytest.raises(ValueError, match="manifest_entry_invalid"):
        runner.verify_model_files(tmp_path)


@pytest.mark.parametrize("architecture", ["span", None, ""])
def test_verified_hashes_do_not_let_wrong_architecture_through(tmp_path, architecture):
    local_checkpoint(tmp_path, architecture=architecture)
    with pytest.raises(ValueError, match="model_architecture_mismatch"):
        runner.verify_model_files(tmp_path)


def prepared_identity():
    return {
        "status": "ner_completed", "source_sha256": "source", "ontology_hash": "ontology",
        "input_hashes": {}, "code_hashes": {}, "gliner2_model": {}, "ner_device": "cpu",
        "labels_per_batch": 1, "reused_plan_arm": "B", "version": runner.VERSION,
        "backend": runner.BACKEND, "architecture": runner.ARCHITECTURE,
        "packages": {name: "fixed-test-version" for name in runner.PACKAGES},
    }


def test_package_identity_includes_tokenizer_compatibility_dependencies(monkeypatch):
    called = []

    def version(name):
        called.append(name)
        return "fixed-test-version"

    monkeypatch.setattr(runner.importlib.metadata, "version", version)
    assert runner.package_versions() == prepared_identity()["packages"]
    assert set(called) == {
        "gliner2", "transformers", "huggingface_hub", "torch", "protobuf", "tokenizers",
    }


@pytest.mark.parametrize("dependency", runner.PACKAGES)
def test_prepared_tools_cannot_be_reused_across_dependency_change(dependency):
    current = prepared_identity()
    prepared = deepcopy(current)
    runner.verify_prepared_identity(prepared, current)
    prepared["packages"][dependency] = "different-version"
    with pytest.raises(ValueError, match="prepared_ner_identity_changed:packages"):
        runner.verify_prepared_identity(prepared, current)


def test_legacy_prepared_manifest_without_packages_cannot_be_reused():
    current = prepared_identity()
    prepared = deepcopy(current)
    del prepared["packages"]
    with pytest.raises(ValueError, match="prepared_ner_identity_changed:packages"):
        runner.verify_prepared_identity(prepared, current)


@pytest.mark.parametrize("changed", ["tools.json", "sources.json", "plan/proposal.json"])
def test_prepared_reuse_rechecks_real_artifacts_and_frozen_inputs(tmp_path, changed):
    prepared, baseline = tmp_path / "prepared", tmp_path / "baseline"
    (prepared / "C").mkdir(parents=True)
    directory, _ = prepared_scope(prepared / "C")
    write(directory / "vocabulary.json", {})
    write(prepared / "vocabularies.json", {})
    shutil.copytree(directory / "plan", baseline / "B/scope/plan")
    cases, ir = [{"scope_id": "scope", "source_units": []}], {"tables": []}
    manifest = {"vocabulary_sha256": runner.digest(prepared / "vocabularies.json"),
                "prepared_tool_hashes": runner.prepared_tool_hashes(prepared, cases)}
    runner.verify_prepared_tools(prepared, manifest, baseline, cases, ir)
    write(directory / changed, {"changed": True})
    with pytest.raises(ValueError, match="prepared_tool_digest_mismatch"):
        runner.verify_prepared_tools(prepared, manifest, baseline, cases, ir)
    # Merely updating claimed file hashes cannot replace frozen source or plan.
    if changed != "tools.json":
        manifest["prepared_tool_hashes"] = runner.prepared_tool_hashes(prepared, cases)
        with pytest.raises(ValueError, match="does_not_match"):
            runner.verify_prepared_tools(prepared, manifest, baseline, cases, ir)
