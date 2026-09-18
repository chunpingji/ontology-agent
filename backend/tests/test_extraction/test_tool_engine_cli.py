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
from tests.test_extraction.test_ontology_guided_core import REPORT, ontology, sample


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
