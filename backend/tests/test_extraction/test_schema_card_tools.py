"""Integration contracts for the bounded tool experiment; no real model calls."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.evaluation import schema_card_tools as runner
from app.evaluation import schema_card_tools_finalize as completion
from app.evaluation.schema_card_evidence import build_sources
from app.evaluation.schema_card_tightened import CMC, validate_schema
from app.services.extraction.tool_validation import metric

BASE = "https://ontology.pharma-gmp.cn/slpra/drug-development/"
STUDY = BASE + "ToxicologyStudy"
XSD = "http://www.w3.org/2001/XMLSchema#"
EMPTY_SPAN = {"ref": "", "quote": "", "span_id": ""}
CLAIM_DIMENSIONS = ["role", "predicate", "binding", "applicability", "counterevidence", "unit"]


def citation(ref, quote, span_id=""):
    return {"ref": ref, "quote": quote, "span_id": span_id}


def blank(schema):
    result = {"subjects": {}, "observations": []}
    subjects = schema["properties"]["subjects"]["properties"]
    for sid, definition in subjects.items():
        result["subjects"][sid] = {
            group: {key: [] for key in definition["properties"][group]["properties"]}
            for group in ("attributes", "relations")
        }
        result["subjects"][sid]["anchor"] = deepcopy(EMPTY_SPAN)
    return result


def supported(dimensions, ref="u2", quote="大鼠试验"):
    return {**{dimension: "supported" for dimension in dimensions},
            "support": [{"ref": ref, "quote": quote}], "counterevidence_support": [],
            "reason": "原文引用支持该冻结候选。"}


@pytest.fixture
def example():
    rows = [["试验", "PDE（mg/天）"], ["大鼠试验", "100"], ["犬试验", "7.5"]]
    table = {"table_path": ["t"], "header_row_count": 1, "source_cells": [], "grid": []}
    units = []
    for row, values in enumerate(rows):
        cells = []
        for column, text in enumerate(values):
            identity = f"cell:{row}:{column}"
            cells.append(identity)
            table["source_cells"].append({
                "cell_id": identity, "row_index": row, "column_index": column,
                "row_span": 1, "column_span": 1,
                "blocks": [{"kind": "paragraph", "text": text}],
            })
            units.append({"evidence_id": identity, "text": text, "table_path": ["t"],
                          "source_cell_id": identity, "row_index": row, "column_index": column})
        table["grid"].append(cells)
    case = {"scope_id": "pde", "source_units": units,
            "content": {"text": "大鼠试验 PDE 100，犬试验 PDE 7.5", "section_path": "毒理试验"}}
    ir = {"tables": [table]}
    catalog = {
        CMC: {"class_iri": CMC, "label": "报告", "properties": [], "allowed_relations": [
            {"iri": BASE + "hasStudy", "label": "包含试验", "kind": "relationship",
             "constraint_status": "resolved", "range_class_iris": [STUDY]},
        ]},
        STUDY: {"class_iri": STUDY, "label": "毒理试验", "allowed_relations": [], "properties": [
            {"iri": BASE + "pde", "label": "PDE", "kind": "property",
             "constraint_status": "resolved", "datatype_iris": [XSD + "decimal"],
             "canonical_unit": "mg/day"},
        ]},
    }
    sources = build_sources(case, ir)
    plan = {"get_schema_card": [{"class_iri": STUDY}],
            "inspect_evidence": [{"ref": "u3"}], "propose_mentions": []}
    spans = runner.span_catalog(sources, {"spans": []})
    schema, subjects, bindings, menus = runner.candidate_schema(plan, sources, catalog, spans)
    proposal = blank(schema)
    proposal["subjects"]["s1"]["anchor"] = citation("u2", "大鼠试验", "source:u2")
    proposal["subjects"]["s1"]["attributes"]["pde"] = [{
        "raw": "100", "source": citation("u3", "100", "source:u3"),
        "field": citation("u1", "PDE"), "unit": citation("u1", "mg/天"),
        "condition": deepcopy(EMPTY_SPAN),
    }]
    return {"case": case, "ir": ir, "sources": sources, "catalog": catalog,
            "plan": plan, "spans": spans, "schema": schema, "subjects": subjects,
            "bindings": bindings, "menus": menus, "proposal": proposal}


def freeze(example):
    validate_schema(example["proposal"], example["schema"])
    proposal = runner.replay_citations(example["proposal"], example["sources"], example["spans"])
    return runner.freeze_candidates(
        proposal, example["subjects"], example["bindings"], example["sources"],
    )


def verification(subjects, claims, observations):
    return {
        "types": {sid: supported(["type"]) for sid in subjects if sid != "document"},
        "claims": {claim["id"]: supported(CLAIM_DIMENSIONS) for claim in claims},
        "observations": {item["id"]: supported(["state", "field", "legend", "condition"])
                         for item in observations},
    }


def finalize(example, edit=None):
    subjects, claims, observations, _ = freeze(example)
    verdicts = verification(subjects, claims, observations)
    if edit:
        edit(verdicts)
    return runner.finalize(
        subjects, claims, observations, verdicts, example["bindings"], example["sources"],
    )


@pytest.mark.parametrize("mutation", ["new_subject", "extra_predicate", "unknown_span"])
def test_candidate_schema_fixes_subjects_predicates_and_span_ids(example, mutation):
    result = deepcopy(example["proposal"])
    if mutation == "new_subject":
        result["subjects"]["invented"] = deepcopy(result["subjects"]["s1"])
    elif mutation == "extra_predicate":
        result["subjects"]["s1"]["attributes"]["maximumDailyDose"] = []
    else:
        result["subjects"]["s1"]["anchor"]["span_id"] = "invented-span"
    with pytest.raises(ValueError):
        validate_schema(result, example["schema"])


def test_verification_schema_only_accepts_exact_frozen_ids(example):
    subjects, claims, observations, _ = freeze(example)
    schema = runner.verification_schema(subjects, claims, observations, example["sources"])
    result = verification(subjects, claims, observations)
    validate_schema(result, schema)
    result["claims"]["invented-candidate"] = deepcopy(result["claims"]["c1"])
    with pytest.raises(ValueError, match="object_keys_mismatch"):
        validate_schema(result, schema)


def test_unused_empty_subject_slot_cannot_supply_facts_or_relation_endpoints(example):
    example["proposal"]["subjects"]["s1"]["anchor"] = deepcopy(EMPTY_SPAN)
    example["proposal"]["subjects"]["document"]["relations"]["hasStudy"] = [{
        "target_id": "s1", "evidence": citation("u2", "大鼠试验"),
        "condition": deepcopy(EMPTY_SPAN), "polarity": "affirmed",
    }]
    subjects, claims, observations, _ = freeze(example)
    assert set(subjects) == {"document"}
    assert len(claims) == 2
    assert all("inactive_subject_slot" in claim["binding"]["issues"] for claim in claims)
    result = runner.finalize(
        subjects, claims, observations, verification(subjects, claims, observations),
        example["bindings"], example["sources"],
    )
    assert result["accepted"] == []


def test_span_id_selects_exact_repeated_occurrence_and_rejects_mismatch():
    sources = {"u0": {"text": "25℃及30℃"}}
    spans = {"second-unit": {"ref": "u0", "start": 6, "end": 7, "text": "℃"}}
    cite = runner.replay_citations(citation("u0", "℃", "second-unit"), sources, spans)
    assert cite == {"ref": "u0", "quote": "℃", "start": 6, "end": 7}
    assert runner.citation_issue(cite, sources) is None
    assert runner.citation_issue({"ref": "u0", "quote": "℃"}, sources) == "source_quote_not_unique"
    with pytest.raises(ValueError, match="citation_span_id_mismatch"):
        runner.replay_citations(citation("u0", "30", "second-unit"), sources, spans)
    spans["second-unit"]["start"] = 0
    with pytest.raises(ValueError, match="tool_span_source_mismatch"):
        runner.replay_citations(citation("u0", "℃", "second-unit"), sources, spans)


@pytest.mark.parametrize("span_text,quote", [
    ("2026", "2026年6月"),
    ("生产日期2026年6月，复验日期2027年6月。", "2026年6月"),
])
def test_span_context_can_converge_to_a_unique_containing_or_contained_quote(span_text, quote):
    text = "生产日期2026年6月，复验日期2027年6月。"
    start = text.index(span_text)
    spans = {"context": {"ref": "u0", "text": span_text,
                         "start": start, "end": start + len(span_text)}}
    result = runner.replay_citations(
        citation("u0", quote, "context"), {"u0": {"text": text}}, spans,
    )
    assert result == {"ref": "u0", "quote": quote, "start": text.index(quote),
                      "end": text.index(quote) + len(quote)}


@pytest.mark.parametrize("text,span_text,quote", [
    ("abcdef", "bcd", "cde"),
    ("aaa", "a", "aa"),
    ("abcabc", "abcabc", "abc"),
    ("生产日期2026年6月。", "2026", ""),
    ("生产日期2026年6月。", "2026", "20266月"),
])
def test_context_convergence_rejects_intersection_ambiguous_or_nonverbatim_quotes(
    text, span_text, quote,
):
    start = text.index(span_text)
    spans = {"context": {"ref": "u0", "text": span_text,
                         "start": start, "end": start + len(span_text)}}
    sources = {"u0": {"text": text}}
    with pytest.raises(ValueError, match="citation_span_id_mismatch"):
        runner.replay_citations(citation("u0", quote, "context"), sources, spans)
    isolated = runner.replay_citations(
        citation("u0", quote, "context"), sources, spans, isolate_errors=True,
    )
    assert isolated["quote"] == quote and isolated["ref"] == "u0"
    assert isolated["start"] == isolated["end"] == -1
    assert isolated["replay_issue"] == "citation_span_id_mismatch"


def test_context_convergence_never_switches_source_units():
    sources = {"u0": {"text": "2026年6月"}, "u1": {"text": "2026年6月"}}
    spans = {"other-source": {"ref": "u1", "text": "2026", "start": 0, "end": 4}}
    with pytest.raises(ValueError, match="citation_span_id_mismatch"):
        runner.replay_citations(citation("u0", "2026年6月", "other-source"), sources, spans)


@pytest.mark.parametrize("start,end,span_text", [
    (-2, 6, "ef"),
    (0, 99, "abcdef"),
    (True, 2, "b"),
    (1.0, 2, "b"),
    (2, 1, ""),
    (1, 1, ""),
    (0, 2, "wrong"),
])
def test_bad_tool_span_coordinates_fail_even_when_python_slice_looks_equal(start, end, span_text):
    sources = {"u0": {"text": "abcdef"}}
    spans = {"bad": {"ref": "u0", "text": span_text, "start": start, "end": end}}
    quote = span_text or "abcdef"
    with pytest.raises(ValueError, match="tool_span_source_mismatch"):
        runner.replay_citations(citation("u0", quote, "bad"), sources, spans)
    result = runner.replay_citations(
        citation("u0", quote, "bad"), sources, spans, isolate_errors=True,
    )
    assert result["quote"] == quote
    assert result["start"] == result["end"] == -1
    assert result["replay_issue"] == "tool_span_source_mismatch"


def test_isolated_bad_span_preserves_candidate_and_does_not_discard_other_scope_facts(example):
    proposal = deepcopy(example["proposal"])
    valid = proposal["subjects"]["s1"]["attributes"]["pde"][0]
    invalid = deepcopy(valid)
    invalid.update(raw="7.5", source=citation("u3", "7.5", "source:u3"))
    proposal["subjects"]["s1"]["attributes"]["pde"].append(invalid)
    replayed = runner.replay_citations(
        proposal, example["sources"], example["spans"], isolate_errors=True,
    )
    invalid_source = replayed["subjects"]["s1"]["attributes"]["pde"][1]["source"]
    assert invalid_source["quote"] == "7.5"
    assert invalid_source["start"] == invalid_source["end"] == -1
    active, claims, observations, rejected = runner.freeze_candidates(
        replayed, example["subjects"], example["bindings"], example["sources"],
    )
    assert rejected == [] and len(claims) == 2
    assert claims[0]["binding"]["issues"] == []
    assert claims[1]["binding"]["issues"]
    result = runner.finalize(
        active, claims, observations, verification(active, claims, observations),
        example["bindings"], example["sources"],
    )
    assert len(result["accepted"]) == len(result["rejected"]) == 1
    assert result["accepted"][0]["candidate_id"] == "c1"
    assert result["accepted"][0]["value"] == "100"
    assert result["rejected"][0]["proposal"]["source"]["quote"] == "7.5"


@pytest.mark.parametrize("verdict", ["unsupported", "undetermined"])
@pytest.mark.parametrize("dimension", ["type", *CLAIM_DIMENSIONS])
def test_any_unsupported_or_unresolved_semantic_dimension_blocks_acceptance(
    example, verdict, dimension,
):
    def edit(result):
        item = result["types"]["s1"] if dimension == "type" else result["claims"]["c1"]
        item[dimension] = verdict

    result = finalize(example, edit)
    assert result["accepted"] == []
    blocked = result["rejected"] if verdict == "unsupported" else result["unresolved"]
    assert blocked
    assert any(f"semantic_{dimension}_{verdict}" in item["issues"] for item in blocked)


def test_supported_pde_has_real_header_unit_and_preserves_source_value(example):
    result = finalize(example)
    assert result["unresolved"] == result["rejected"] == []
    assert len(result["accepted"]) == 1
    fact = result["accepted"][0]
    assert fact["value"] == "100"
    assert fact["raw_value"] == "100"
    assert fact["subject_text"] == "大鼠试验"
    assert fact["unit"]["ref"] == "u1" and fact["unit"]["quote"] == "mg/天"
    checked = result["metric_checks"][0]["metric"]
    assert checked["conversion_record"]["to"] == "mg/day"
    assert checked["shacl"]["coverage"]["complete"] is True


def test_supported_semantics_cannot_supply_missing_unit_or_cross_trial_owner(example):
    missing = deepcopy(example)
    missing["proposal"]["subjects"]["s1"]["attributes"]["pde"][0]["unit"] = deepcopy(EMPTY_SPAN)
    assert finalize(missing)["accepted"] == []
    wrong_row = deepcopy(example)
    raw = wrong_row["proposal"]["subjects"]["s1"]["attributes"]["pde"][0]
    raw.update(raw="7.5", source=citation("u5", "7.5", "source:u5"))
    result = finalize(wrong_row)
    assert result["accepted"] == []
    assert any("owner_row_mismatch" in item["issues"] for item in result["rejected"])


def test_shacl_vacuous_conformance_does_not_bypass_final_gate(example, monkeypatch):
    original = metric.validate_graph

    def uncovered(data_graph, **kwargs):
        kwargs["expected_focus_nodes"] = ["urn:missing-candidate"]
        return original(data_graph, **kwargs)

    monkeypatch.setattr(metric, "validate_graph", uncovered)
    result = finalize(example)
    assert result["accepted"] == []
    assert result["unresolved"]
    checked = result["metric_checks"][0]["metric"]["shacl"]
    assert checked["conforms"] is True
    assert checked["coverage"]["complete"] is False
    assert result["metric_checks"][0]["metric"]["validation_status"] == "incomplete"


def test_shacl_technical_failure_remains_unresolved_not_rejected_data(example, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr("pyshacl.validate", unavailable)
    result = finalize(example)
    assert result["accepted"] == []
    assert result["rejected"] == []
    assert len(result["unresolved"]) == 1
    assert result["metric_checks"][0]["metric"]["execution_status"] == "failed"


def test_invoke_has_three_request_cap_explicit_no_retry_settings_and_records_failure(
    tmp_path, monkeypatch,
):
    calls, options = [], []

    def chat(recorder, **kwargs):
        options.append(kwargs)
        recorder.calls.append({"http_call": 1, "seconds": 0})
        if len(options) == 2:
            raise ValueError("simulated_request_failure")
        return {}

    monkeypatch.setattr("app.services.llm.local_client.chat_with_schema", chat)
    monkeypatch.setattr(
        "app.services.llm.model_runtime.model_scope", lambda **kwargs: nullcontext(),
    )
    client = SimpleNamespace(base_url="http://unused.invalid")
    for index in range(3):
        if index == 1:
            with pytest.raises(ValueError, match="simulated_request_failure"):
                runner.invoke(client, tmp_path / str(index), "run", "scope", str(index),
                              "prompt", {}, runner.obj({}), calls)
        else:
            runner.invoke(client, tmp_path / str(index), "run", "scope", str(index),
                          "prompt", {}, runner.obj({}), calls)
    assert len(calls) == 3
    with pytest.raises(ValueError, match="scope_request_budget_exhausted"):
        runner.invoke(client, tmp_path / "fourth", "run", "scope", "4", "prompt", {},
                      runner.obj({}), calls)
    assert len(options) == 3
    assert not (tmp_path / "fourth").exists()
    assert all(item["max_attempts"] == 1 and item["timeout_retries"] == 0
               and item["truncation_max_tokens"] is None for item in options)
    assert json.loads((tmp_path / "1" / "calls.json").read_text())[0]["stage"] == "1"


def test_three_stage_mock_model_workflow_produces_verified_pde(example, tmp_path, monkeypatch):
    stages = []

    def chat(recorder, **kwargs):
        stage = kwargs["schema_name"]
        stages.append(stage)
        recorder.calls.append({"http_call": 1, "seconds": 0, "finish_reason": "stop"})
        if stage == "tools_plan":
            return deepcopy(example["plan"])
        if stage == "tools_candidates":
            return deepcopy(example["proposal"])
        payload = json.loads(kwargs["user"])["input"]
        return verification(payload["subjects"], payload["claims"], payload["observations"])

    monkeypatch.setattr("app.services.llm.local_client.chat_with_schema", chat)
    monkeypatch.setattr(
        "app.services.llm.model_runtime.model_scope", lambda **kwargs: nullcontext(),
    )
    result = runner.run_case(
        SimpleNamespace(base_url="http://unused.invalid"), example["case"], example["catalog"],
        example["ir"], tmp_path, "isolated-test", ner_enabled=False,
    )
    assert result["status"] == "complete", result
    assert stages == ["tools_plan", "tools_candidates", "tools_verification"]
    assert result["contract_passes"] == 3 and len(result["calls"]) == 3
    assert result["accepted"][0]["value"] == "100"
    assert result["ner"]["execution_status"] == "disabled"
    assert (tmp_path / "frozen-candidates.json").is_file()


def preserved_scope(example, directory, *, before=2):
    for name in ("plan", "candidates"):
        (directory / name).mkdir()
    runner.write(directory / "sources.json", example["sources"])
    runner.write(directory / "plan" / "proposal.json", example["plan"])
    runner.write(directory / "plan" / "schema.json", runner.plan_schema(
        example["sources"], [STUDY],
    ))
    runner.write(directory / "candidates" / "proposal.json", example["proposal"])
    runner.write(directory / "tools.json", {
        "source_spans": example["spans"],
        "propose_mentions": {"execution_status": "disabled", "spans": []},
    })
    return {"scope_id": "pde", "arm": "A", "status": "failed",
            "calls": [{"http_call": 1, "seconds": 0, "stage": stage}
                      for stage in ("plan", "candidates", "verification")[:before]]}


def fake_semantic_request(called):
    def invoke(client, directory, run_id, scope, stage, prompt, payload, schema, calls):
        called.append(stage)
        assert stage == "verification"
        assert len(calls) == 2
        directory.mkdir()
        result = verification(payload["subjects"], payload["claims"], payload["observations"])
        validate_schema(result, schema)
        calls.append({"http_call": 1, "stage": stage, "seconds": 0, "finish_reason": "stop"})
        runner.write(directory / "proposal.json", result)
        runner.write(directory / "schema.json", schema)
        runner.write(directory / "calls.json", [calls[-1]])
        return result

    return invoke


def test_completion_uses_only_unused_third_request_without_regenerating_candidates(
    example, tmp_path, monkeypatch,
):
    old = preserved_scope(example, tmp_path)
    original = {(stage, name): (tmp_path / stage / name).read_bytes()
                for stage, name in [("plan", "proposal.json"), ("candidates", "proposal.json")]}
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "complete", result
    assert called == ["verification"]
    assert result["new_model_calls"] == 1 and len(result["calls"]) == 3
    assert result["calls"][:2] == old["calls"]
    assert result["accepted"][0]["value"] == "100"
    assert all((tmp_path / stage / name).read_bytes() == data
               for (stage, name), data in original.items())


@pytest.mark.parametrize("before,prior_error", [(3, False), (2, True)])
def test_completion_never_retries_used_or_failed_requests(example, tmp_path, monkeypatch,
                                                         before, prior_error):
    old = preserved_scope(example, tmp_path, before=before)
    if prior_error:
        old["calls"][-1]["error_type"] = "TimeoutError"
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "failed"
    assert result["error_code"] == "third_request_not_available_without_retry"
    assert result["new_model_calls"] == 0 and result["calls"] == old["calls"]
    assert called == []
    assert not (tmp_path / "verification").exists()


def preserve_semantic_response(example, directory):
    subjects, claims, observations, _ = freeze(example)
    frozen = {"subjects": subjects, "claims": claims, "observations": observations}
    runner.write(directory / "frozen-candidates.json", frozen)
    (directory / "verification").mkdir()
    runner.write(directory / "verification" / "proposal.json", verification(
        subjects, claims, observations,
    ))
    return frozen


def test_completion_reuses_identical_semantic_target_without_a_model_request(
    example, tmp_path, monkeypatch,
):
    old = preserved_scope(example, tmp_path, before=3)
    frozen = preserve_semantic_response(example, tmp_path)
    original_target = completion.semantic_target(frozen)
    # New deterministic checker output does not change the frozen semantic request.
    frozen["claims"][0]["binding"] = {"changed_checker_only": True}
    frozen["claims"][0]["metric_precheck"] = {"changed_checker_only": True}
    assert completion.semantic_target(frozen) == original_target
    runner.write(tmp_path / "frozen-candidates.json", frozen)
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "complete", result
    assert len(result["accepted"]) == 1
    assert result["new_model_calls"] == 0 and len(result["calls"]) == 3
    assert called == []
    persisted = runner.read(tmp_path / "frozen-candidates.json")
    assert completion.semantic_target(persisted) == original_target
    assert "changed_checker_only" not in persisted["claims"][0]["binding"]
    assert persisted["claims"][0]["binding"]["validation_status"] == "passed"


def test_changed_candidate_cannot_reuse_existing_review_or_request_an_extra_one(
    example, tmp_path, monkeypatch,
):
    old = preserved_scope(example, tmp_path, before=3)
    frozen = preserve_semantic_response(example, tmp_path)
    changed = deepcopy(example["proposal"])
    changed["subjects"]["s1"]["attributes"]["pde"][0].update(
        raw="7.5", source=citation("u5", "7.5", "source:u5"),
    )
    runner.write(tmp_path / "candidates" / "proposal.json", changed)
    changed_target = deepcopy(frozen)
    changed_target["claims"][0]["proposal"]["raw"] = "7.5"
    assert completion.semantic_target(changed_target) != completion.semantic_target(frozen)
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "failed"
    assert result["error_code"] == "changed_candidate_cannot_reuse_semantic_review"
    assert result["new_model_calls"] == 0 and len(result["calls"]) == 3
    assert result["accepted"] == [] and called == []


def test_completion_preserves_bad_citation_locally_and_completes_other_candidate(
    example, tmp_path, monkeypatch,
):
    invalid = deepcopy(example["proposal"]["subjects"]["s1"]["attributes"]["pde"][0])
    invalid.update(raw="7.5", source=citation("u3", "7.5", "source:u3"))
    example["proposal"]["subjects"]["s1"]["attributes"]["pde"].append(invalid)
    old = preserved_scope(example, tmp_path)
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "complete", result
    assert len(result["accepted"]) == len(result["rejected"]) == 1
    assert result["accepted"][0]["value"] == "100"
    rejected_source = result["rejected"][0]["proposal"]["source"]
    assert rejected_source["quote"] == "7.5"
    assert rejected_source["start"] == rejected_source["end"] == -1
    assert result["new_model_calls"] == 1 and len(result["calls"]) == 3
    assert called == ["verification"]


@pytest.mark.parametrize("dimension", ["state", "field", "legend", "condition"])
@pytest.mark.parametrize("verdict", ["unsupported", "undetermined", "supported"])
def test_observation_semantic_rejection_is_distinct_from_unresolved(example, dimension, verdict):
    observation = {"value": citation("u3", "100"), "field": citation("u1", "PDE"),
                   "legend": deepcopy(EMPTY_SPAN), "condition": deepcopy(EMPTY_SPAN),
                   "state": "unknown", "reason": "隔离的分类测试对象"}
    example["proposal"]["observations"] = [observation]

    def edit(result):
        result["observations"]["o1"][dimension] = verdict

    result = finalize(example, edit)
    if verdict == "supported":
        assert len(result["observations"]) == 1
        assert result["unresolved"] == result["rejected"] == []
    else:
        destination = "rejected" if verdict == "unsupported" else "unresolved"
        opposite = "unresolved" if destination == "rejected" else "rejected"
        assert result["observations"] == []
        assert result[opposite] == []
        assert len(result[destination]) == 1
        assert f"semantic_{dimension}_{verdict}" in result[destination][0]["issues"]


def test_completion_preserves_candidate_timeout_stage_and_valid_plan_contract(
    example, tmp_path, monkeypatch,
):
    old = preserved_scope(example, tmp_path)
    old.update(error_type="StructuredModelError", error_code="model_timeout", contract_passes=1)
    (tmp_path / "candidates" / "proposal.json").unlink()
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "completion-test", example["catalog"])
    assert result["status"] == "failed"
    assert result["error_code"] == "model_timeout"
    assert result["error_type"] == "StructuredModelError"
    assert result["failure_stage"] == "candidates"
    assert result["completion_error_type"] == "FileNotFoundError"
    assert result["contract_passes"] == 1
    assert result["new_model_calls"] == 0 and result["calls"] == old["calls"]
    assert called == []


def test_repeated_completion_preserves_timeout_from_original_error(
    example, tmp_path, monkeypatch,
):
    old = preserved_scope(example, tmp_path)
    old.update(error_type="FileNotFoundError", original_error="model_timeout", contract_passes=0)
    (tmp_path / "candidates" / "proposal.json").unlink()
    called = []
    monkeypatch.setattr(completion, "invoke", fake_semantic_request(called))
    result = completion.complete_case(None, old, tmp_path, "recheck-test", example["catalog"])
    assert result["status"] == "failed"
    assert result["original_error"] == result["error_code"] == "model_timeout"
    assert result["failure_stage"] == "candidates"
    assert result["completion_error_type"] == "FileNotFoundError"
    assert result["contract_passes"] == 1
    assert result["new_model_calls"] == 0 and result["calls"] == old["calls"]
    assert called == []


@pytest.mark.parametrize("status,expected", [("failed", False), ("complete", True)])
def test_extended_acceptance_never_passes_a_technically_failed_scope(status, expected):
    result = {"scope_id": "pde", "status": status, "accepted": [], "calls": [],
              "error_code": "model_timeout" if status == "failed" else None}
    reference = {"forbidden_types": {}, "manual_review_dimensions": ["predicate_meaning"]}
    score = runner.score_extended(result, reference)
    assert score["evaluated"] is expected
    assert score["all_pass"] is expected
    assert score["manual_review_status"] == "not_performed"
    if not expected:
        assert score["checks"] == []
        assert score["reason"] == "technical_failure"
        assert score["error_code"] == "model_timeout"
