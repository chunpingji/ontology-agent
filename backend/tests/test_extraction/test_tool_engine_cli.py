"""Offline assembly keeps references separate and exercises native turn pairing."""

import hashlib
import json
from copy import deepcopy

import pytest

from app.evaluation.ontology_tool_engine import load_run_inputs, probe_responses, run_manifest
from app.services.extraction.ontology_guided.claim_protocol import ExtractionProfile
from app.services.extraction.ontology_guided.metadata import prepare_metadata
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.tool_model_adapter import ToolModelRecognitionAdapter
from app.services.extraction.ontology_guided.tool_runtime import ToolLimits
from app.services.llm.local_client import ResponseTurn
from tests.test_extraction.test_ontology_guided_core import PRODUCT, REPORT, ontology, sample


def turn(items, *, status="completed"):
    return ResponseTurn(response_id="r", output_items=items, response_status=status,
                        incomplete_details=None, error=None, usage={"output_tokens": 1})


def message(value):
    return {"type": "message", "id": "m", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(value)}]}


def test_probe_preserves_full_items_and_uses_call_id(tmp_path):
    requests = []
    output = [
        {"type": "reasoning", "id": "reasoning", "encrypted_content": "opaque"},
        {"type": "message", "id": "preface", "role": "assistant", "content": []},
        {"type": "function_call", "id": "item-1", "call_id": "call-1",
         "name": "inspect_evidence", "arguments": '{"evidence_ids":["probe-source"]}'},
        {"type": "function_call", "id": "item-2", "call_id": "call-2",
         "name": "inspect_evidence", "arguments": '{"evidence_ids":["probe-source"]}'},
    ]

    def invoke(_, **kwargs):
        requests.append(deepcopy(kwargs))
        if len(requests) % 2:
            return turn(output)
        items = kwargs["input_items"]
        assert items[1:5] == output
        assert [row["call_id"] for row in items[5:]] == ["call-1", "call-2"]
        data = json.loads(items[-1]["output"])
        return turn([message({"observed_value": data["text"], "evidence_id": "probe-source"})])

    result, code = probe_responses(
        object(), model="qwen", model_revision="fixed", output=tmp_path / "probe", invoke=invoke,
    )
    assert code == 0 and result["baseline_passed"]
    assert result["actual_model_requests"] == 4
    assert [row["tools"][0]["strict"] for row in requests] == [False, False, True, True]
    assert all(row["instructions"] for row in requests)
    assert all([tool["name"] for tool in row["tools"]] == ["inspect_evidence"] for row in requests)
    assert result["encrypted_reasoning"] == "not_attempted"


@pytest.mark.parametrize("budget", [0, 1])
def test_probe_cannot_start_an_unfinishable_round(tmp_path, budget):
    def invoke(*args, **kwargs):
        pytest.fail("no request allowed")

    result, code = probe_responses(
        None, model="q", model_revision="r", output=tmp_path / "probe",
        max_model_requests=budget, invoke=invoke,
    )
    assert code == 2 and result["actual_model_requests"] == 0


def test_optional_strict_answer_failure_does_not_erase_baseline_or_tool_result(tmp_path):
    def invoke(_, **kwargs):
        if kwargs["tool_choice"] != "none":
            return turn([{"type": "function_call", "id": "item", "call_id": "call",
                          "name": "inspect_evidence",
                          "arguments": '{"evidence_ids":["probe-source"]}'}])
        data = json.loads(kwargs["input_items"][-1]["output"])
        item = message({"observed_value": data["text"], "evidence_id": "probe-source"})
        if kwargs["text_format"]["strict"]:
            item["content"][0]["text"] = "```json\n" + item["content"][0]["text"] + "\n```"
        return turn([item])

    result, code = probe_responses(
        None, model="q", model_revision="r", output=tmp_path / "probe", invoke=invoke,
    )
    assert code == 0 and result["baseline_passed"]
    assert result["strict_parameter_generation"] == "passed"
    assert result["strict_answer_format"] == "failed"


@pytest.mark.parametrize("failure", ["no_call", "incomplete", "wrong_nonce", "fenced"])
def test_probe_refuses_false_success(tmp_path, failure):
    calls = []

    def invoke(_, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if failure == "no_call":
                return turn([message({})])
            return turn([{"type": "function_call", "id": "item", "call_id": "call",
                          "name": "inspect_evidence",
                          "arguments": '{"evidence_ids":["probe-source"]}'}],
                        status="incomplete" if failure == "incomplete" else "completed")
        if failure == "wrong_nonce":
            return turn([message({"observed_value": "made up", "evidence_id": "probe-source"})])
        data = json.loads(kwargs["input_items"][-1]["output"])
        item = message({"observed_value": data["text"], "evidence_id": "probe-source"})
        item["content"][0]["text"] = "```json\n" + item["content"][0]["text"] + "\n```"
        return turn([item])

    result, code = probe_responses(
        None, model="q", model_revision="r", output=tmp_path / "probe", invoke=invoke,
    )
    assert code == 2 and not result["baseline_passed"]
    assert len(calls) == (1 if failure in {"no_call", "incomplete"} else 2)


@pytest.fixture
def manifest_path(tmp_path):
    analysis = sample(tmp_path)
    metadata = prepare_metadata(analysis.ir, section_tree=analysis.structure.section_tree.to_dict(),
                                summary_version="test")

    def frozen(name, value=None):
        path = tmp_path / name
        if value is not None:
            path.write_text(value.model_dump_json())
        return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    manifest = {
        "run_id": "cli-run", "document": frozen("source.docx"),
        "ir": frozen("ir.json", analysis.ir), "ontology": frozen("ontology.json", ontology()),
        "metadata": frozen("metadata.json", metadata), "root_class_iri": REPORT,
        "model": "q", "model_revision": "fixed",
        "budgets": {"max_model_calls": 1, "max_input_tokens": 1000000, "max_output_tokens": 2000},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_manifest_rejects_reference_and_changed_sources(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({**manifest, "reference": "must-not-read.json"}))
    with pytest.raises(ValueError, match="Extra inputs"):
        load_run_inputs(manifest_path)
    manifest_path.write_text(json.dumps(manifest))
    (manifest_path.parent / "metadata.json").write_text("{}")
    with pytest.raises(ValueError, match="digest_mismatch"):
        load_run_inputs(manifest_path)


def test_run_uses_existing_executor_and_hard_model_budget(manifest_path, monkeypatch):
    calls = []

    def transport(_, **kwargs):
        calls.append(kwargs)
        return turn([message({"entities": [], "properties": [], "relations": [],
                              "external_links": [], "observations": []})])

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)

    def factory(manifest, ir, snapshot, metadata):
        return ToolModelRecognitionAdapter(
            object(), index=RecordIndex(ir), ontology=snapshot, metadata=metadata,
            profile=ExtractionProfile(), token_counter=len, model_identity=manifest.model,
            max_input_tokens=1000000, max_output_tokens=2000, tool_limits=ToolLimits(10000),
        )

    output = manifest_path.parent / "result"
    code = run_manifest(manifest_path, output, adapter_factory=factory)
    assert code == 2 and len(calls) == 1
    ledger = json.loads((output / "calls.json").read_text())
    assert ledger["model_call_state"]["version"] == 2
    assert any(row["field"] == "model_turn" for row in ledger["protocol_results"].values())
    assert json.loads((output / "metrics.json").read_text())["scoring_status"] == "not_scored"
    result = json.loads((output / "evaluation.json").read_text())
    assert "+sparse-candidates-v1" in result["executor_version"]
    assert result["adapter_calls"][0]["reason_code"] == "record_no_claims"
    frozen, *_ = load_run_inputs(output / "manifest.json")
    assert frozen.run_id == "cli-run"
    with pytest.raises(FileExistsError):
        run_manifest(manifest_path, output, adapter_factory=factory)


def test_zero_global_budget_does_not_reserve_or_invoke_a_request(manifest_path, monkeypatch):
    manifest = json.loads(manifest_path.read_text())
    manifest["budgets"]["max_model_calls"] = 0
    manifest_path.write_text(json.dumps(manifest))

    def factory(manifest, ir, snapshot, metadata):
        return ToolModelRecognitionAdapter(
            object(), index=RecordIndex(ir), ontology=snapshot, metadata=metadata,
            profile=ExtractionProfile(), token_counter=len, model_identity=manifest.model,
            max_input_tokens=1000000, max_output_tokens=2000, tool_limits=ToolLimits(10000),
        )

    def forbidden(*args, **kwargs):
        pytest.fail("zero budget cannot start HTTP")

    monkeypatch.setattr("app.services.llm.local_client.responses_create", forbidden)
    output = manifest_path.parent / "zero"
    assert run_manifest(manifest_path, output, adapter_factory=factory) == 2
    ledger = json.loads((output / "calls.json").read_text())
    assert not ledger["model_call_state"].get("reservations")
    checks = json.loads((output / "protocol-checks.json").read_text())
    assert checks["requests_started"] == 0


@pytest.mark.parametrize("max_members", [1, 2, 4])
@pytest.mark.parametrize("request_budget", [1, 2])
def test_batch_manifest_uses_physical_budget_and_records_member_counts(
    manifest_path, monkeypatch, max_members, request_budget,
):
    from app.services.extraction.ontology_guided.recognition_batch import RecognitionBatchPolicy

    manifest = json.loads(manifest_path.read_text())
    manifest["options"] = {"batching": {"max_members": max_members}}
    manifest["reference_resolution_version"] = 1
    manifest["budgets"]["max_model_calls"] = request_budget
    manifest_path.write_text(json.dumps(manifest))
    calls = []

    def transport(_, **kwargs):
        calls.append(kwargs)
        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        from app.services.llm.model_runtime import observe

        observe("model_end", status="completed", usage={"output_tokens": 1})
        units = {unit["unit_id"]: unit for unit in view["evidence_units"]}
        answers = []
        for member in view["members"]:
            source = next(ref for ref in member["evidence_refs"]
                          if ref["fact_eligible"] and any(
                              name in units[ref["unit_id"]]["text"]
                              for name in ("产品甲", "产品乙")))
            text = units[source["unit_id"]]["text"]
            name = "产品甲" if "产品甲" in text else "产品乙"
            quote = {"evidence_id": source["evidence_id"], "text": name, "context_text": None}
            if view["stage"] == "discovery":
                # A real grounded entity requires a separate verification request.
                answer = {"entities": [{
                    "local_id": "product", "class_iri": PRODUCT, "representation": "mention",
                    "mentions": [quote], "record_components": [], "identifier_claims": [],
                }], "properties": [], "relations": [], "external_links": [], "observations": []}
            else:
                targets = member["verification_input"]["targets"]
                assert targets
                answer = {"verifications": [{
                    "target_id": target["target_id"], "content_hash": target["content_hash"],
                    "facets": [{"name": facet, "verdict": "supported", "support": [quote],
                                "counterevidence_support": [], "reason": "原文实体提及支持"}
                               for facet in target["required_facets"]],
                } for target in targets]}
            answers.append({"task_id": member["task_id"], "result": answer})
        return turn([message({"members": answers})])

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)

    def factory(manifest, ir, snapshot, metadata):
        return ToolModelRecognitionAdapter(
            object(), index=RecordIndex(ir), ontology=snapshot, metadata=metadata,
            profile=ExtractionProfile(), token_counter=len, model_identity=manifest.model,
            max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(10000),
            recognition_batching=RecognitionBatchPolicy.model_validate(manifest.options["batching"]),
            reference_resolution=manifest.reference_resolution_version == 1,
        )

    output = manifest_path.parent / f"batch-{max_members}"
    assert run_manifest(manifest_path, output, adapter_factory=factory) == 2
    assert len(calls) == request_budget
    ledger = json.loads((output / "calls.json").read_text())
    assert ledger["model_call_state"]["version"] == 3
    assert ledger["model_call_state"]["reservation_sequence"] == request_budget
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["batching"]["policy"]["max_members"] == max_members
    assert metrics["cost"]["physical_requests_reserved"] == request_budget
    assert len(metrics["cost"]["request_member_counts"]) == request_budget
    assert metrics["cost"]["work_unit_elapsed_seconds"] >= 0
    assert metrics["cost"]["model_elapsed_seconds"] > 0
    assert metrics["cost"]["tool_elapsed_seconds"] == 0
    if request_budget == 2:
        assert metrics["batching"]["members_with_outcomes"] > 0
    else:
        assert metrics["batching"]["members_without_outcomes"] > 0
        assert metrics["batching"]["incomplete_rate"] == 1
    assert metrics["scoring_status"] == "not_scored"


@pytest.mark.parametrize("version", [True, 1.0, "1", 0, 2])
def test_manifest_reference_resolution_version_is_exact(manifest_path, version):
    manifest = json.loads(manifest_path.read_text())
    manifest["reference_resolution_version"] = version
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="invalid_reference_resolution_version"):
        load_run_inputs(manifest_path)


def record_factory(manifest, ir, snapshot, metadata):
    return ToolModelRecognitionAdapter(
        object(), index=RecordIndex(ir), ontology=snapshot, metadata=metadata,
        profile=ExtractionProfile(), token_counter=len, model_identity=manifest.model,
        max_input_tokens=1000000, max_output_tokens=20000, tool_limits=ToolLimits(10000),
        recognition_pipeline=manifest.recognition_pipeline,
        record_discovery=manifest.options.get("record_discovery"),
        reference_resolution=manifest.reference_resolution_version == 1,
    )


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_record_and_relation_requests_share_the_total_budget_without_fake_members(
    manifest_path, monkeypatch, budget,
):
    manifest = json.loads(manifest_path.read_text())
    manifest.update(recognition_pipeline="record-entity-first-v1", reference_resolution_version=1)
    manifest["budgets"]["max_model_calls"] = budget
    manifest_path.write_text(json.dumps(manifest))
    calls = []

    def transport(_, **kwargs):
        from app.services.llm.model_runtime import observe

        view = json.loads(kwargs["input_items"][0]["content"][0]["text"])
        calls.append(view)
        observe("model_end", status="completed", usage={"output_tokens": 1})
        if "members" in view:
            return turn([message({"members": [{"task_id": member["task_id"], "result": {
                "entities": [], "properties": [], "relations": [], "external_links": [],
                "observations": [],
            }} for member in view["members"]]})])
        unit = next(unit for unit in view["evidence_units"] if unit["fact_eligible"] and any(
            name in unit["text"] for name in ("产品甲", "产品乙")))
        name = "产品甲" if "产品甲" in unit["text"] else "产品乙"
        quote = {"evidence_id": unit["evidence_id"], "text": name, "context_text": None}
        if view["stage"] == "discovery":
            answer = {"entities": [{
                "local_id": "product", "class_iri": PRODUCT, "representation": "mention",
                "mentions": [quote], "record_components": [], "identifier_claims": [],
            }], "properties": [], "relations": [], "external_links": [], "observations": []}
        else:
            assert view["verification_input"]["targets"]
            answer = {"verifications": [{
                "target_id": target["target_id"], "content_hash": target["content_hash"],
                "facets": [{"name": facet, "verdict": "supported", "support": [quote],
                            "counterevidence_support": [], "reason": "原文明确提及该产品"}
                           for facet in target["required_facets"]],
            } for target in view["verification_input"]["targets"]]}
        return turn([message(answer)])

    monkeypatch.setattr("app.services.llm.local_client.responses_create", transport)
    output = manifest_path.parent / f"record-budget-{budget}"
    assert run_manifest(manifest_path, output, adapter_factory=record_factory) == 2
    assert len(calls) == budget
    frozen, *_ = load_run_inputs(output / "manifest.json")
    assert frozen.options["record_discovery"]["endpoint_page_size"] == 8
    assert frozen.options["batching"] == {"version": "predicate-batch-v1", "max_members": 4}
    ledger = json.loads((output / "calls.json").read_text())
    state = ledger["model_call_state"]
    assert state.get("reservation_sequence", 0) == budget
    receipts = state.get("reservations", [])
    record_receipts = [receipt for receipt in receipts if "record_task_id" in receipt]
    assert all(not {"member_task_ids", "member_lineage_ids", "subject_ref"} & set(receipt)
               for receipt in record_receipts)
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["cost"]["physical_requests_reserved"] == budget
    assert metrics["cost"]["record_model_requests"] == len(record_receipts)
    assert metrics["cost"]["request_task_counts"] == [1] * budget
    assert len(metrics["cost"]["request_member_counts"]) == budget - len(record_receipts)
    assert metrics["cost"]["record_responses_returned"] == len(record_receipts)
    assert metrics["batching"]["policy"] == frozen.options["batching"]
    coverage = metrics["record_discovery"]["coverage"]
    assert coverage["tasks_planned"] == sum(coverage[key] for key in (
        "tasks_examined", "tasks_incomplete", "tasks_unattempted",
    ))
    assert metrics["record_discovery"]["unfinished_tasks"] > 0
    assert json.loads((output / "coverage.json").read_text())["record_discovery"] == coverage
    if budget == 0:
        assert coverage["tasks_unattempted"] == coverage["tasks_planned"] > 0
        assert metrics["cost"]["record_discovery_calls"] == 0
    elif budget == 1:
        assert coverage["tasks_incomplete"] == 1 and coverage["tasks_examined"] == 0
        assert metrics["record_discovery"]["tasks_without_outcomes"] == 1
        evaluation = json.loads((output / "evaluation.json").read_text())
        assert evaluation["record_discovery_calls"][0]["status"] == "paused"
    else:
        assert coverage["tasks_examined"] == 1
        assert metrics["record_discovery"]["tasks_with_outcomes"] == 1
    if budget == 3:
        assert len(record_receipts) == 2
        assert any("members" in call for call in calls)


def test_evaluation_fingerprint_freezes_record_policy_and_preserves_legacy_inputs(
    manifest_path, monkeypatch,
):
    baseline = json.loads(manifest_path.read_text())
    baseline.update(reference_resolution_version=1)
    baseline["budgets"]["max_model_calls"] = 0
    captured = []
    from app.evaluation import quality_guided_variant

    original = quality_guided_variant.evidence_hash

    def capture(value):
        captured.append(deepcopy(value))
        return original(value)

    monkeypatch.setattr(quality_guided_variant, "evidence_hash", capture)
    monkeypatch.setattr("app.services.llm.local_client.responses_create",
                        lambda *a, **kw: pytest.fail("fingerprint validation has no model budget"))
    fingerprints = []
    variants = (None, {"max_classes_per_card": 1}, {"max_classes_per_card": 4},
                {"max_classes_per_card": 4, "endpoint_page_size": 2})
    for number, policy in enumerate(variants):
        manifest = deepcopy(baseline)
        if policy is not None:
            manifest.update(recognition_pipeline="record-entity-first-v1", options={
                "record_discovery": policy,
            })
        manifest_path.write_text(json.dumps(manifest))
        output = manifest_path.parent / f"fingerprint-{number}"
        run_manifest(manifest_path, output, adapter_factory=record_factory)
        result = json.loads((output / "evaluation.json").read_text())
        fingerprints.append(result["run_fingerprint"])
    assert len(set(fingerprints)) == 4
    assert "recognition_pipeline" not in captured[0]
    assert "record_discovery" not in captured[0]
    assert [entry["record_discovery"]["max_classes_per_card"]
            for entry in captured[1:]] == [1, 4, 4]
    assert [entry["record_discovery"]["endpoint_page_size"] for entry in captured[1:]] == [8, 8, 2]
    assert all(entry["recognition_pipeline"] == "record-entity-first-v1"
               for entry in captured[1:])
