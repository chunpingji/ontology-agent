"""Three-arm isolation, frozen tool reuse, and actual request-budget boundaries."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from docx import Document

from app.evaluation import schema_card_mock_tools as runner
from app.evaluation.schema_card_mock_protocol import EQUIPMENT, PROPERTIES, REPORT, USES
from app.evaluation.schema_card_probe import digest, read, write
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.word_analysis import analyze_word_core


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    baseline = tmp_path / "D-baseline"
    baseline.mkdir()
    document = Document()
    for text in ("使用设备 RE100 离心机", "使用设备 RE200 反应釜", "使用设备 RE300 干燥箱",
                 "RE400 过滤器"):
        document.add_paragraph(text)
    document.save(baseline / "source.docx")
    analysis = analyze_word_core(baseline / "source.docx")
    ir = analysis.ir
    props = [{"iri": iri, "label": label, "kind": "property",
              "datatype_iris": ["http://www.w3.org/2001/XMLSchema#string"]}
             for iri, label in zip(PROPERTIES, ("设备编号", "设备名称", "规格"), strict=True)]
    catalog = {
        REPORT: {"class_iri": REPORT, "label": "CMC 报告", "parents": [], "properties": [],
                 "allowed_relations": [{"iri": USES, "kind": "relationship", "label": "使用设备",
                                        "range_class_iris": [EQUIPMENT]}]},
        EQUIPMENT: {"class_iri": EQUIPMENT, "label": "工艺设备", "parents": [],
                    "properties": props, "allowed_relations": []},
        runner.EQ + "Equipment": {"class_iri": runner.EQ + "Equipment", "label": "设备",
                                  "parents": [], "properties": props, "allowed_relations": []},
    }
    for name, value in (("ir.json", ir.model_dump(mode="json")), ("cards.json", catalog)):
        write(baseline / name, value)
    metadata_dir = baseline / "metadata"
    metadata_dir.mkdir()
    tree = analysis.structure.section_tree.to_dict()
    tree.setdefault("layer_metadata", {}).update(content_summary="摘要专有但不可作证据",
                                                  summary_status="completed", summary_source="llm")
    metadata = prepare_metadata(ir, section_tree=tree, summary_version="test-summary-v1")
    write(metadata_dir / "metadata.json", {"metadata_snapshot": metadata.model_dump(mode="json")})
    write(metadata_dir / "section-tree.json", tree)
    write(metadata_dir / "summaries.json", {"summary": "摘要专有但不可作证据"})
    write(metadata_dir / "manifest.json", {
        "document_ref": "upload-test", "source_sha256": ir.document_hash,
        "analysis_id": ir.analysis_id, "structure_hash": ir.structure_hash,
        "metadata_snapshot_id": metadata.snapshot_id,
        "metadata_dependency_hash": metadata.dependency_hash,
        "input_hashes": {"ir": digest(baseline / "ir.json")},
        "output_hashes": {name: digest(metadata_dir / name)
                          for name in runner.summary_runner.METADATA_FILES[:-1]},
    })
    frozen_code = tmp_path / "frozen.py"
    frozen_code.write_text("# Fixed dependency used by the D baseline.\n")
    (baseline / "code").mkdir()
    (baseline / "code/frozen.py").write_bytes(frozen_code.read_bytes())
    old = {
        "version": runner.summary_runner.VERSION, "status": "completed", "run_id": "frozen-D",
        "source_sha256": ir.document_hash, "model": "Qwen-fixed",
        "declared_model_revision": "qwen-fixed-revision",
        "gliner2_model": {"repo": "fixed", "revision": "fixed"},
        "packages": {"gliner2": "fixed"}, "ner_device": "cuda:0",
        "input_hashes": {name: digest(baseline / name) for name in ("ir.json", "cards.json")},
        "code_hashes": {"frozen.py": digest(frozen_code)},
    }
    write(baseline / "result.json", old)
    for name in ("reference.json", "acceptance.json", "cases.json"):
        (baseline / name).write_text("MUST NOT READ HISTORICAL SELECTORS OR REFERENCES")
    records = [{"equipment_id": key, "name": name, "specification": "外部独有规格"}
               for key, name in (("RE100", "离心机"), ("RE200", "反应釜"),
                                 ("RE300", "干燥箱"), ("RE400", "过滤器"))]
    mock_file = tmp_path / "archive.json"
    write(mock_file, records)
    vocabulary = {
        "groups": {"entities": ["设备"], "values": [p["label"] for p in props], "units": []},
        "entries": {"设备": {"iri": runner.EQ + "Equipment", "role": "entity_name",
                             "description": "设备提及"}} | {
            p["label"]: {"iri": p["iri"], "role": "property_value", "description": p["label"]}
            for p in props},
    }
    monkeypatch.setattr(runner.summary_runner, "SOURCE_SHA256", ir.document_hash)
    monkeypatch.setattr(runner, "code_paths", lambda: {"frozen.py": frozen_code})
    monkeypatch.setattr(runner.ner_runner, "verify_model_files", lambda _: old["gliner2_model"])
    monkeypatch.setattr(runner.ner_runner, "package_versions", lambda: old["packages"])
    monkeypatch.setattr(runner.ner_runner, "build_extraction_vocabulary",
                        lambda *a, **kw: deepcopy(vocabulary))
    args = SimpleNamespace(
        baseline=baseline, output=tmp_path / "prepared", mock_file=mock_file,
        ontology_dir=tmp_path / "ontology", model_path=tmp_path / "model", device="cuda:0",
        prepare_only=True, ner_only=False, prepared_tools=None,
    )
    return SimpleNamespace(args=args, ir=ir, catalog=catalog, vocabulary=vocabulary,
                           old=old, code=frozen_code)


def fake_ner(monkeypatch):
    loaded, inputs = [], []

    def extractor(path, **kwargs):
        loaded.append(kwargs)
        return object()

    def mentions(sources, *, groups, extractor, labels_per_batch):
        assert labels_per_batch == 1
        assert "设备" in groups["entities"]
        inputs.append(deepcopy(sources))
        return {"execution_status": "completed", "semantic_status": "not_checked",
                "fact_eligible": False, "spans": [
                    {"ref": ref, "start": 0, "end": len(source["text"]), "text": source["text"],
                     "label": "设备", "score": 0.9, "id": "mention-" + ref, "group": "entities"}
                    for ref, source in sources.items()]}

    monkeypatch.setattr(runner.ner_runner, "Gliner2Extractor", extractor)
    monkeypatch.setattr(runner.ner_runner, "propose_mentions", mentions)
    return loaded, inputs


def ner_run(experiment, monkeypatch):
    loaded, inputs = fake_ner(monkeypatch)
    args = deepcopy(experiment.args)
    args.prepare_only, args.ner_only = False, True
    manifest = runner.execute(args)
    assert manifest["status"] == "ner_completed"
    return args, manifest, loaded, inputs


def test_prepare_does_not_call_models_and_e0_has_no_mock_records(experiment, monkeypatch):
    monkeypatch.setattr(runner.ner_runner, "Gliner2Extractor",
                        lambda *a, **kw: pytest.fail("model during prepare-only"))
    manifest = runner.execute(experiment.args)
    assert manifest["status"] == "prepared" and manifest["results"] == []
    assert manifest["max_calls"] == 18 and len(manifest["case_hashes"]) == 9
    assert manifest["reference_is_input"] is manifest["summary_is_evidence"] is False
    assert manifest["new_summary_calls"] == 0
    for case in manifest["case_hashes"]:
        directory = experiment.args.output / case["directory"]
        search = read(directory / "mock-search.json")
        if case["arm"] == "E0":
            assert search["execution_status"] == "not_requested"
            assert search["candidates"] == []
            assert "外部独有规格" not in str(search)
        else:
            assert search["execution_status"] == "completed"
            assert search["candidates"] and search["catalog_snapshot_hash"]
        assert "摘要专有" not in str(read(directory / "sources.json"))
    assert not (experiment.args.output / "reference.json").exists()
    assert not (experiment.args.output / "acceptance.json").exists()


def test_e0_e1_same_sources_and_real_tool_reuse_has_fixed_source_hash(experiment, monkeypatch):
    args, manifest, loaded, inputs = ner_run(experiment, monkeypatch)
    assert len(loaded) == 1 and loaded[0]["max_len"] == 160
    assert len(inputs) == manifest["ner_unique_inputs"] < 9
    for case in [row for row in manifest["case_hashes"] if row["arm"] == "E0"]:
        left = args.output / case["directory"]
        right = args.output / "E1" / case["scope_id"]
        for filename in ("sources.json", "ner.json", "ner-model-view.json"):
            assert digest(left / filename) == digest(right / filename)
        rows = [row for row in manifest["ner_results"] if row["record_id"] == case["record_id"]]
        assert len({row["source_input_hash"] for row in rows}) == 1
    assert {span["label"] for span in read(
        args.output / manifest["case_hashes"][0]["directory"] / "ner.json")["spans"]} == {"设备"}


def test_prepared_reuse_no_model_and_preserves_hashes_and_origin(experiment, monkeypatch):
    first, before, _, _ = ner_run(experiment, monkeypatch)
    args = deepcopy(first)
    args.output, args.prepared_tools = first.output.parent / "reused", first.output
    monkeypatch.setattr(runner.ner_runner, "Gliner2Extractor",
                        lambda *a, **kw: pytest.fail("repeated NER load"))
    monkeypatch.setattr(runner.ner_runner, "propose_mentions",
                        lambda *a, **kw: pytest.fail("repeated NER inference"))
    after = runner.execute(args)
    assert after["ner_reused_from"] == before["run_id"]
    assert after["ner_hashes"] == before["ner_hashes"]
    assert after["ner_wall_seconds"] == before["ner_wall_seconds"]
    assert runner.prepared_identity(after) == runner.prepared_identity(before)


def test_qwen_receives_same_original_sources_and_ner_but_only_e1_mock_records(
    experiment, monkeypatch,
):
    args, manifest, _, _ = ner_run(experiment, monkeypatch)
    mock = runner.FrozenEquipmentCatalog.from_records(read(args.mock_file))
    captured = []

    def invoke(client, directory, run_id, case_id, stage, prompt, payload, schema, calls):
        captured.append({"case": case_id, "stage": stage, "payload": deepcopy(payload),
                         "schema": schema})
        calls.append({"http_call": 1, "stage": stage})
        return ({"entities": [], "relations": [], "observations": []}
                if stage == "candidates" else {})

    monkeypatch.setattr(runner, "invoke", invoke)
    first = next(case for case in manifest["case_hashes"] if case["arm"] == "E0")
    for arm in ("E0", "E1"):
        case = next(row for row in manifest["case_hashes"]
                    if row["arm"] == arm and row["record_id"] == first["record_id"])
        result = runner.run_case(None, args.output, case, manifest, experiment.catalog, mock)
        assert result["status"] == "complete" and result["contracts_passed"] == 2
    candidates = [row for row in captured if row["stage"] == "candidates"]
    before, after = [row["payload"] for row in candidates]
    for field in ("source", "ner", "cards", "record_roles"):
        assert before[field] == after[field]
    assert before["external_candidates"]["candidates"] == []
    assert "外部独有规格" not in str(before)
    assert "外部独有规格" in str(after["external_candidates"])
    assert "外部独有规格" not in str(after["source"])
    assert len(captured) == 4


def test_candidate_technical_failure_never_starts_verification(experiment, monkeypatch):
    args, manifest, _, _ = ner_run(experiment, monkeypatch)
    mock = runner.FrozenEquipmentCatalog.from_records(read(args.mock_file))
    stages = []

    def invoke(client, directory, run_id, case_id, stage, prompt, payload, schema, calls):
        stages.append(stage)
        calls.append({"http_call": 1, "stage": stage, "status_code": 400})
        raise RuntimeError("provider failure")

    monkeypatch.setattr(runner, "invoke", invoke)
    case = manifest["case_hashes"][0]
    result = runner.run_case(None, args.output, case, manifest, experiment.catalog, mock)
    assert stages == ["candidates"]
    assert result["status"] == "failed" and result["failure_stage"] == "candidates"
    assert result["contracts_passed"] == 0 and len(result["calls"]) == 1
    assert "accepted" not in result
    assert read(args.output / case["directory"] / "result.json") == result


def test_compact_ner_keeps_strongest_unique_spans_bounded_without_mutating_raw():
    spans = [{"ref": "u0", "start": i, "end": i + 1, "text": str(i), "label": "设备",
              "score": (i + 1) / 100} for i in range(60)]
    spans.append({**spans[-1], "score": 0.01})
    original = {"execution_status": "completed", "spans": spans}
    before = deepcopy(original)
    compact = runner.compact_ner(original)
    assert len(compact["spans"]) == 48 and compact["available_unique_spans"] == 60
    assert compact["omitted_spans"] == 12 and compact["spans"][0]["score"] == 0.6
    assert original == before and compact["semantic_status"] == "not_checked"


@pytest.mark.parametrize("copy", ["current", "baseline"])
def test_frozen_dependency_copies_are_verified_before_ner(experiment, monkeypatch, copy):
    target = (experiment.code if copy == "current"
              else experiment.args.baseline / "code/frozen.py")
    target.write_text(target.read_text() + "# changed\n")
    monkeypatch.setattr(runner.ner_runner, "Gliner2Extractor",
                        lambda *a, **kw: pytest.fail("model despite dependency tampering"))
    with pytest.raises(ValueError, match="D_dependency_changed"):
        runner.execute(experiment.args)


@pytest.mark.parametrize("kind,error", [
    ("source", "prepared_case_changed"), ("ner", "prepared_ner_changed"),
    ("metadata", "prepared_metadata_changed"), ("code", "prepared_code_changed"),
    ("input", "prepared_input_changed"), ("mock_search", "prepared_case_changed"),
])
def test_prepared_files_rechecked_not_only_manifest_hash_claims(
    experiment, monkeypatch, kind, error,
):
    first, before, _, _ = ner_run(experiment, monkeypatch)
    relative = before["case_hashes"][0]["directory"]
    paths = {"source": relative + "/sources.json", "ner": relative + "/ner.json",
             "metadata": "metadata/metadata.json", "code": "code/frozen.py",
             "input": "retrieval.json", "mock_search": relative + "/mock-search.json"}
    target = first.output / paths[kind]
    target.write_text(target.read_text() + "\n ")
    args = deepcopy(first)
    args.output, args.prepared_tools = first.output.parent / "reused", first.output
    with pytest.raises(ValueError, match=error):
        runner.execute(args)
    assert read(args.output / "result.json")["status"] == "failed"


@pytest.mark.parametrize("field", ["model", "declared_model_revision", "device", "max_tokens"])
def test_prepared_runtime_contract_cannot_change(experiment, monkeypatch, field):
    first, _, _, _ = ner_run(experiment, monkeypatch)
    prior = read(first.output / "result.json")
    prior[field] = "changed"
    write(first.output / "result.json", prior)
    args = deepcopy(first)
    args.output, args.prepared_tools = first.output.parent / "reused", first.output
    with pytest.raises(ValueError, match="prepared_ner_identity_mismatch"):
        runner.execute(args)


@pytest.mark.parametrize("mutation", ["wrong_text", "negative_start", "end_outside", "wrong_label"])
def test_rehashed_ner_still_must_match_valid_original_span(experiment, monkeypatch, mutation):
    first, before, _, _ = ner_run(experiment, monkeypatch)
    relative = before["case_hashes"][0]["directory"]
    target = first.output / relative / "ner.json"
    ner = read(target)
    span = ner["spans"][0]
    if mutation == "wrong_text":
        span["text"] = "不存在的原文"
    elif mutation == "negative_start":
        span["start"] = -len(span["text"])
    elif mutation == "end_outside":
        span["end"] += 1
    else:
        span["label"] = "档案实例名不能作标签"
    write(target, ner)
    prior = read(first.output / "result.json")
    prior["ner_hashes"][relative] = digest(target)
    write(first.output / "result.json", prior)
    args = deepcopy(first)
    args.output, args.prepared_tools = first.output.parent / "reused", first.output
    error = "ner_(source_span_mismatch|label_outside_frozen_vocabulary)"
    with pytest.raises(ValueError, match=error):
        runner.execute(args)


def test_input_character_budget_prevents_http_and_creating_stage(tmp_path, monkeypatch):
    from app.services.llm import local_client

    monkeypatch.setattr(local_client, "chat_with_schema",
                        lambda *a, **kw: pytest.fail("over-budget HTTP request"))
    calls = []
    directory = tmp_path / "stage"
    with pytest.raises(ValueError, match="model_input_character_budget_exceeded"):
        runner.invoke(SimpleNamespace(base_url="http://local.test"), directory, "run", "case",
                      "candidates", "x" * runner.MAX_INPUT_CHARACTERS, {}, {}, calls)
    assert calls == [] and not directory.exists()


def test_case_request_budget_prevents_third_http(tmp_path, monkeypatch):
    from app.services.llm import local_client

    monkeypatch.setattr(local_client, "chat_with_schema",
                        lambda *a, **kw: pytest.fail("third request"))
    calls = [{"http_call": 1}, {"http_call": 1}]
    with pytest.raises(ValueError, match="case_request_budget_exhausted"):
        runner.invoke(SimpleNamespace(base_url="http://local.test"), tmp_path / "third", "run",
                      "case", "verification", "prompt", {}, {}, calls)
    assert len(calls) == 2


def test_failed_request_ledger_retained_with_no_retry_settings(tmp_path, monkeypatch):
    from app.services.llm import local_client

    observed = []

    def fail(recorder, **kwargs):
        observed.append(kwargs)
        recorder.calls.append({"http_call": 1, "status_code": 400, "error_type": "RuntimeError"})
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(local_client, "chat_with_schema", fail)
    calls = []
    directory = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        runner.invoke(SimpleNamespace(base_url="http://local.test"), directory, "run", "case",
                      "candidates", "prompt", {}, {}, calls)
    assert len(observed) == len(calls) == 1
    assert observed[0]["max_attempts"] == 1 and observed[0]["timeout_retries"] == 0
    assert observed[0]["truncation_max_tokens"] is None
    assert calls[0]["stage"] == "candidates" and calls[0]["status_code"] == 400
    assert read(directory / "calls.json") == calls
    assert not (directory / "proposal.json").exists()


def fake_qwen_runtime(monkeypatch, manifest):
    import sys

    from app.config import settings
    from app.services.llm import local_client

    monkeypatch.setitem(sys.modules, "pyshacl", SimpleNamespace(__version__="test"))
    monkeypatch.setattr(settings, "local_llm_model", manifest["model"])
    monkeypatch.setattr(settings, "local_llm_model_revision", manifest["declared_model_revision"])
    monkeypatch.setattr(local_client, "get_local_llm", lambda: object())
    monkeypatch.setattr(runner.ner_runner, "model_identity", lambda *a: [manifest["model"]])


def test_full_execution_obeys_18_calls_and_e0_e1_alternating_order(experiment, monkeypatch):
    fake_ner(monkeypatch)
    fake_qwen_runtime(monkeypatch, experiment.old)
    order = []

    def run_case(client, root, case, manifest, catalog, mock):
        order.append((case["arm"], case["record_id"]))
        return {"arm": case["arm"], "scope_id": case["scope_id"], "status": "complete",
                "contracts_passed": 2, "calls": [{"http_call": 1}, {"http_call": 1}]}

    monkeypatch.setattr(runner, "run_case", run_case)
    experiment.args.prepare_only = False
    manifest = runner.execute(experiment.args)
    assert manifest["status"] == "completed"
    assert sum(len(row["calls"]) for row in manifest["results"]) == 18
    assert [arm for arm, _ in order] == ["E0", "E1"] * 3 + ["E2"] * 3
    assert all(order[i][1] == order[i + 1][1] for i in (0, 2, 4))


def test_global_budget_rejects_next_case_before_any_new_call(experiment, monkeypatch):
    fake_ner(monkeypatch)
    fake_qwen_runtime(monkeypatch, experiment.old)
    order = []

    def run_case(client, root, case, manifest, catalog, mock):
        order.append(case["arm"])
        return {"arm": case["arm"], "scope_id": case["scope_id"], "status": "complete",
                "contracts_passed": 2, "calls": [{"http_call": 1}, {"http_call": 1}]}

    monkeypatch.setattr(runner, "run_case", run_case)
    monkeypatch.setattr(runner, "MAX_CALLS", 17)
    experiment.args.prepare_only = False
    with pytest.raises(ValueError, match="experiment_request_budget_exhausted"):
        runner.execute(experiment.args)
    assert len(order) == 8
    assert sum(len(row["calls"]) for row in read(
        experiment.args.output / "result.json")["results"]) == 16
