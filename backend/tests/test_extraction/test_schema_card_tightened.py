"""Regression contracts for subject-bound extraction, without model calls."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.evaluation import schema_card_tightened
from app.evaluation.schema_card_evidence import build_sources
from app.evaluation.schema_card_tightened import (
    CMC,
    EMPTY,
    check_claims,
    compile_subject_schema,
    field_issue,
    invoke,
    raw_value_issue,
    recheck_run,
    score_case,
    unit_issue,
    validate_discovery,
    validate_schema,
)

BASE = "https://example.test/"
STUDY, PRODUCT = BASE + "Study", BASE + "Product"
XSD = "http://www.w3.org/2001/XMLSchema#"


def prop(name, label, datatype="string", unit=None):
    return {"iri": BASE + name, "label": label, "kind": "property",
            "constraint_status": "resolved", "datatype_iris": [XSD + datatype],
            "canonical_unit": unit}


def relation(name, target):
    return {"iri": BASE + name, "label": name, "kind": "relationship",
            "constraint_status": "resolved", "range_class_iris": [target]}


def cite(ref, text):
    return {"ref": ref, "quote": text}


def attribute(raw, source, field=EMPTY, unit=EMPTY, condition=EMPTY):
    return {"raw": raw, "source": source, "field": deepcopy(field),
            "unit": deepcopy(unit), "condition": deepcopy(condition)}


def blank_result(schema):
    return {subject: {group: {field: [] for field in fields["properties"]}
                     for group, fields in definition["properties"].items()}
            for subject, definition in schema["properties"].items()}


@pytest.fixture
def sample():
    rows = [["试验", "剂量（mg）", "F1"], ["试验甲", "12", "5"], ["试验乙", "34", "7"]]
    table = {"table_path": ["t"], "header_row_count": 1, "source_cells": [], "grid": []}
    units = []
    for row, values in enumerate(rows):
        identities = []
        for column, text in enumerate(values):
            identity = f"cell:{row}:{column}"
            identities.append(identity)
            table["source_cells"].append({
                "cell_id": identity, "row_index": row, "column_index": column,
                "row_span": 1, "column_span": 1,
                "blocks": [{"kind": "paragraph", "text": text}],
            })
            units.append({"evidence_id": identity, "text": text, "table_path": ["t"],
                          "source_cell_id": identity, "row_index": row, "column_index": column})
        table["grid"].append(identities)
    for text in ["产品甲", "是否有标志：否", "阈值甲0.2%，阈值乙2.0%", "N/A", "—",
                 "备注：“—”代表相关的研究数据不充分；“×”代表没有相应的毒性。", "×"]:
        units.append({"evidence_id": f"paragraph:{len(units)}", "text": text, "table_path": None})
    sources = build_sources({"source_units": units}, {"tables": [table]})
    catalog = {
        CMC: {"class_iri": CMC, "label": "报告", "properties": [],
              "allowed_relations": [relation("describes", PRODUCT), relation("hasStudy", STUDY)]},
        STUDY: {"class_iri": STUDY, "label": "试验", "properties": [
            prop("dose_mg", "剂量", "decimal", "mg"), prop("f5", "F5", "decimal"),
            prop("description", "描述")], "allowed_relations": [relation("about", PRODUCT)]},
        PRODUCT: {"class_iri": PRODUCT, "label": "产品", "properties": [
            prop("flagged", "是否有标志", "boolean"), prop("description", "描述")],
            "allowed_relations": []},
    }
    frames = {"s1": {"class_iri": STUDY, "anchor": cite("u3", "试验甲")},
              "s2": {"class_iri": STUDY, "anchor": cite("u6", "试验乙")},
              "s3": {"class_iri": PRODUCT, "anchor": cite("u9", "产品甲")}}
    schema, subjects, bindings, menus = compile_subject_schema(frames, sources, catalog)
    return {"sources": sources, "catalog": catalog, "frames": frames, "schema": schema,
            "subjects": subjects, "bindings": bindings, "menus": menus}


def run_attribute(sample, value, *, subject="s1", key="dose_mg"):
    result = blank_result(sample["schema"])
    result[subject]["attributes"][key] = [value]
    validate_schema(result, sample["schema"])
    return check_claims(result, sample["subjects"], sample["bindings"], sample["sources"])


def test_schema_has_fixed_root_subject_predicates_and_relation_targets(sample):
    schema = sample["schema"]
    assert set(schema["properties"]) == {"document", "s1", "s2", "s3"}
    root = schema["properties"]["document"]["properties"]
    assert root["attributes"]["properties"] == {}
    target = root["relations"]["properties"]["describes"]["items"]["properties"]["target_id"]
    assert target["enum"] == ["s3"]
    assert sample["subjects"]["document"]["class_iri"] == CMC
    assert "dose_mg" not in schema["properties"]["s3"]["properties"]["attributes"]["properties"]
    validate_schema(blank_result(schema), schema)


@pytest.mark.parametrize("mutation", ["new_subject", "unknown_property", "wrong_target",
                                     "wrong_direction", "missing_root", "extra_claim_field"])
def test_schema_rejects_subject_or_predicate_contract_escape(sample, mutation):
    result = blank_result(sample["schema"])
    edge = {"target_id": "s3", "evidence": cite("u9", "产品甲"),
            "condition": deepcopy(EMPTY), "polarity": "affirmed"}
    if mutation == "new_subject":
        result["invented"] = {"attributes": {}, "relations": {}}
    elif mutation == "unknown_property":
        result["s3"]["attributes"]["dose_mg"] = []
    elif mutation == "wrong_target":
        edge["target_id"] = "s2"
        result["document"]["relations"]["describes"] = [edge]
    elif mutation == "wrong_direction":
        edge["target_id"] = "document"
        result["s3"]["relations"]["describes"] = [edge]
    elif mutation == "missing_root":
        del result["document"]
    else:
        edge["subject_id"] = "s1"
        result["document"]["relations"]["describes"] = [edge]
    with pytest.raises(ValueError):
        validate_schema(result, sample["schema"])


def test_unresolved_predicate_is_absent_from_subject_schema(sample):
    catalog = deepcopy(sample["catalog"])
    catalog[STUDY]["properties"][0]["constraint_status"] = "constraint_unresolved"
    schema, _, _, _ = compile_subject_schema(sample["frames"], sample["sources"], catalog)
    assert "dose_mg" not in schema["properties"]["s1"]["properties"]["attributes"]["properties"]


def test_table_quantity_keeps_typed_value_and_source_unit(sample):
    accepted, rejected = run_attribute(sample, attribute(
        "12", cite("u4", "12"), cite("u1", "剂量（mg）"), cite("u1", "mg")))
    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0]["value"] == "12"
    assert accepted[0]["subject_text"] == "试验甲"


@pytest.mark.parametrize("unit_quote,compatible", [("(mg/天)", True), ("(mg/kg/day)", False)])
def test_parenthesized_header_unit_preserves_quote_and_quantity_dimension(
    sample, unit_quote, compatible,
):
    sample["sources"]["u1"]["text"] = "PDE"
    sample["sources"]["u16"] = {
        **deepcopy(sample["sources"]["u1"]), "ref": "u16", "evidence_id": "header-unit",
        "text": unit_quote,
    }
    sample["sources"]["u4"]["text"] = "100"
    sample["sources"]["u4"]["column_header_refs"] = ["u1", "u16"]
    sample["catalog"][STUDY]["properties"][0] = prop(
        "pde_mg_per_day", "PDE", "decimal", "mg/day")
    schema, subjects, bindings, _ = compile_subject_schema(
        sample["frames"], sample["sources"], sample["catalog"])
    sample.update(schema=schema, subjects=subjects, bindings=bindings)
    accepted, rejected = run_attribute(sample, attribute(
        "100", cite("u4", "100"), cite("u1", "PDE"), cite("u16", unit_quote)),
        key="pde_mg_per_day")
    if compatible:
        assert rejected == []
        assert accepted[0]["value"] == "100"
        assert accepted[0]["unit"] == cite("u16", unit_quote)
    else:
        assert accepted == []
        assert rejected[0]["reason"] == "unit_missing_or_incompatible"


@pytest.mark.parametrize("mode,reason", [
    ("cross_row", "owner_row_mismatch"), ("missing_unit", "unit_source_missing"),
    ("wrong_field", "field_column_mismatch"), ("wrong_unit_column", "unit_column_mismatch"),
])
def test_table_claims_reject_role_and_ownership_errors(sample, mode, reason):
    value = attribute("12", cite("u4", "12"), cite("u1", "剂量（mg）"), cite("u1", "mg"))
    if mode == "cross_row":
        value.update(raw="34", source=cite("u7", "34"))
    elif mode == "missing_unit":
        value["unit"] = deepcopy(EMPTY)
    elif mode == "wrong_field":
        value["field"] = cite("u2", "F1")
    else:
        sample["sources"]["u2"]["text"] = "另一列（mg）"
        value["unit"] = cite("u2", "mg")
    accepted, rejected = run_attribute(sample, value)
    assert accepted == []
    assert rejected[0]["reason"] == reason


def test_boolean_preserves_source_no_and_normalizes_once(sample):
    accepted, rejected = run_attribute(sample, attribute(
        "否", cite("u10", "是否有标志：否"), cite("u10", "是否有标志")),
        subject="s3", key="flagged")
    assert rejected == []
    assert accepted[0]["value"] is False
    assert "polarity" not in accepted[0]


@pytest.mark.parametrize("raw,text", [
    ("RE123", "设备编号：RE123"),
    ("单个杂质不得过0.2%，总杂不得过2.0%。", "单个杂质不得过0.2%，总杂不得过2.0%。"),
])
def test_non_boolean_value_can_overlap_complete_field_quote(raw, text):
    proposal = attribute(raw, cite("u0", text), field=cite("u0", text))
    assert raw_value_issue(proposal, {"u0": {"text": text}}) is None


def test_boolean_no_cannot_be_borrowed_from_question_label_with_yes_answer():
    text = "是否是肿瘤药物：是"
    proposal = attribute("否", cite("u0", text), field=cite("u0", "是否是肿瘤药物"))
    assert raw_value_issue(proposal, {"u0": {"text": text}}) == "raw_value_not_in_quote"


@pytest.mark.parametrize("raw,quote,valid", [
    ("3.8", "13.8", False), ("13", "-13", False), ("3", "3e2", False),
    ("25", "不超过25℃", False), ("12", "12-18 mg", False),
    ("12", "剂量12 mg", True), ("-13", "-13", True), ("3.8", "3.8", True),
])
def test_raw_numeric_value_cannot_drop_sign_exponent_or_comparator(raw, quote, valid):
    issue = raw_value_issue(attribute(raw, cite("u0", quote)))
    assert (issue is None) is valid


@pytest.mark.parametrize("text,value_quote,unit_quote,valid", [
    ("剂量12 mg", "12", "mg", True),
    ("剂量12mg", "12", "mg", True),
    ("剂量12；另一设备重8 mg", "12", "mg", False),
    ("剂量12 mg/kg/day", "12", "mg", False),
    ("剂量12 mg/kg/day", "12 mg/kg/day", "mg", False),
    ("质量为8 mg，剂量12", "12", "mg", False),
])
def test_inline_unit_must_modify_this_value_and_keep_complete_unit(
    text, value_quote, unit_quote, valid,
):
    sources = {"u0": {"text": text, "column_header_refs": []}}
    proposal = attribute("12", cite("u0", value_quote), unit=cite("u0", unit_quote))
    issue = unit_issue(proposal, sources, prop("dose_mg", "剂量", "decimal", "mg"))
    assert (issue is None) is valid


def test_short_field_quote_cannot_change_f1_into_f5(sample):
    proposal = attribute("5", cite("u5", "5"), cite("u2", "F"))
    assert field_issue(proposal, sample["sources"], prop("f5", "F5", "decimal")) is not None


def test_short_numeric_quote_cannot_hide_the_surrounding_negative_number():
    proposal = attribute("13", cite("u0", "13"))
    assert raw_value_issue(proposal, {"u0": {"text": "数值为-13"}}) is not None


@pytest.mark.parametrize("class_iri,anchor", [
    (CMC, cite("u9", "产品甲")), (STUDY, cite("u0", "试验")),
    (STUDY, cite("u12", "N/A")), (STUDY, cite("u13", "—")),
])
def test_discovery_cannot_create_document_header_or_marker_entity(sample, class_iri, anchor):
    frames, observations, rejected = validate_discovery(
        {"frames": [{"class_iri": class_iri, "anchor": anchor}], "observations": []},
        sample["sources"], sample["catalog"])
    assert frames == {} and observations == []
    assert len(rejected) == 1


def observation(ref, quote, state, *, legend=EMPTY, field=EMPTY, condition=EMPTY):
    return {"value": cite(ref, quote), "field": deepcopy(field), "legend": deepcopy(legend),
            "condition": deepcopy(condition), "state": state, "reason": "原文状态"}


@pytest.mark.parametrize("state,with_legend,accepted", [
    ("unknown", True, True), ("unknown", False, False), ("negated", True, False),
])
def test_unknown_marker_needs_legend_and_cannot_be_negated(sample, state, with_legend, accepted):
    legend = cite("u14", sample["sources"]["u14"]["text"]) if with_legend else EMPTY
    result = {"frames": [], "observations": [observation("u13", "—", state, legend=legend)]}
    _, observations, rejected = validate_discovery(result, sample["sources"], sample["catalog"])
    assert bool(observations) is accepted
    assert bool(rejected) is not accepted


@pytest.mark.parametrize("state", ["not_applicable", "negated", "unknown"])
def test_normal_sentence_cannot_acquire_unsupported_observation_state(state):
    text = "本次备样用于Ⅰ期临床。"
    sources = {"u0": {"text": text, "table_path": None}}
    proposed = observation("u0", text, state, field=cite("u0", "备样"),
                           legend=cite("u0", text))
    _, observations, rejected = validate_discovery(
        {"frames": [], "observations": [proposed]}, sources, {})
    assert observations == []
    assert len(rejected) == 1


@pytest.mark.parametrize("text,accepted", [("N/A", True), ("不适用", True), ("本项适用", False)])
def test_not_applicable_observation_requires_actual_applicability_marker(text, accepted):
    sources = {"u0": {"text": text, "table_path": None}}
    _, observations, rejected = validate_discovery(
        {"frames": [], "observations": [observation("u0", text, "not_applicable")]}, sources, {})
    assert bool(observations) is accepted
    assert bool(rejected) is not accepted


@pytest.mark.parametrize("source_text,accepted", [("200", True), ("200 mg/天", False)])
def test_numeric_unknown_requires_unit_absence_in_actual_source_expression(source_text, accepted):
    sources = {"u0": {"text": source_text, "table_path": None},
               "u1": {"text": "NOAEL", "table_path": None}}
    proposed = observation("u0", "200", "unknown", field=cite("u1", "NOAEL"))
    _, observations, rejected = validate_discovery(
        {"frames": [], "observations": [proposed]}, sources, {})
    assert bool(observations) is accepted
    assert bool(rejected) is not accepted


def test_numeric_unknown_cannot_ignore_unit_in_its_own_column_header(sample):
    proposed = observation("u4", "12", "unknown", field=cite("u1", "剂量（mg）"))
    _, observations, rejected = validate_discovery(
        {"frames": [], "observations": [proposed]}, sample["sources"], sample["catalog"])
    assert observations == []
    assert len(rejected) == 1


def test_unknown_and_missing_markers_cannot_become_string_facts(sample):
    for ref, text in (("u12", "N/A"), ("u13", "—")):
        accepted, rejected = run_attribute(
            sample, attribute(text, cite(ref, text)), subject="s3", key="description")
        assert accepted == []
        assert rejected[0]["reason"] == "unknown_or_missing_is_not_fact"


def test_empty_output_does_not_satisfy_required_facts_or_observations():
    result = {"accepted": [], "observations": []}
    reference = {"required_properties": [{"predicate": "dose_mg", "value": "12"}],
                 "required_observations": [{"quote": "N/A", "state": "not_applicable"}],
                 "forbidden_predicates": ["wrong"]}
    score = score_case(result, {}, reference)
    assert score["total"] == 3 and score["passed"] == 1
    assert not score["all_pass"]
    assert not score_case(result, {}, {})["all_pass"]


def test_no_fact_scope_still_requires_original_missing_observation(sample):
    reference = {"no_facts": True,
                 "required_observations": [{"quote": "N/A", "state": "not_applicable"}]}
    result = {"accepted": [], "observations": []}
    assert not score_case(result, sample["sources"], reference)["all_pass"]
    result["observations"] = [observation("u12", "N/A", "not_applicable")]
    assert score_case(result, sample["sources"], reference)["all_pass"]


def test_value_contains_requires_all_thresholds_in_one_retained_value():
    reference = {"required_properties": [{"predicate": "control",
                                           "value_contains": ["甲0.2%", "乙2.0%"]}]}

    def fact(value):
        return {"kind": "property", "predicate_iri": BASE + "control",
                "subject_text": "步骤甲", "value": value}

    result = {"accepted": [fact("甲0.2%"), fact("乙2.0%")], "observations": []}
    assert not score_case(result, {}, reference)["all_pass"]
    result["accepted"] = [fact("同时满足甲0.2%和乙2.0%")]
    assert score_case(result, {}, reference)["all_pass"]


def test_reference_owner_and_row_are_checked_together(sample):
    fact = {"kind": "property", "predicate_iri": BASE + "dose_mg",
            "subject_text": "试验甲", "value": "34", "evidence": cite("u7", "34")}
    expected = {"predicate": "dose_mg", "owner_contains": "试验乙", "row": 2, "value": "34"}
    result = {"accepted": [fact], "observations": []}
    score = score_case(result, sample["sources"], {"required_properties": [expected]})
    assert not score["all_pass"]


@pytest.mark.parametrize("kind,polarity", [("property", "affirmed"), ("relation", "negated")])
def test_positive_relation_reference_cannot_match_property_or_negated_edge(kind, polarity):
    fact = {"kind": kind, "predicate_iri": BASE + "describes", "subject_id": "document",
            "subject_text": "", "object_text": "产品甲", "polarity": polarity}
    reference = {"required_relations": [{"predicate": "describes", "subject_id": "document"}]}
    assert not score_case({"accepted": [fact], "observations": []}, {}, reference)["all_pass"]


@pytest.mark.parametrize("mode,passes", [
    ("valid", True), ("split_group", False), ("wrong_anchor_column", False),
    ("merged_groups", False),
])
def test_subject_groups_require_shared_identity_exact_anchor_and_distinct_trials(
    sample, mode, passes,
):
    expected = [{"predicate": "dose_mg", "value": "12"},
                {"predicate": "description", "value": "试验甲"},
                {"predicate": "dose_mg", "value": "34"},
                {"predicate": "description", "value": "试验乙"}]
    facts = [{"kind": "property", "predicate_iri": BASE + item["predicate"],
              "subject_id": "s1" if index < 2 else "s2", "subject_class_iri": STUDY,
              "subject_text": "试验甲" if index < 2 else "试验乙", "value": item["value"]}
             for index, item in enumerate(expected)]
    groups = [{"subject_class": "Study", "required_property_indices": [0, 1],
               "anchor": {"table_path": ["t"], "row": 1, "column": 0}},
              {"subject_class": "Study", "required_property_indices": [2, 3],
               "anchor": {"table_path": ["t"], "row": 2, "column": 0}}]
    frames = deepcopy(sample["frames"])
    if mode == "split_group":
        facts[1]["subject_id"] = "s2"
    elif mode == "wrong_anchor_column":
        frames["s1"]["anchor"] = cite("u4", "12")
    elif mode == "merged_groups":
        for fact in facts:
            fact["subject_id"] = "s1"
        for group in groups:
            del group["anchor"]
    reference = {"required_properties": expected, "subject_groups": groups,
                 "distinct_subject_groups": True}
    result = {"accepted": facts, "observations": [], "frames": frames}
    score = score_case(result, sample["sources"], reference)
    assert score["all_pass"] is passes
    assert all(check["pass"] for check in score["checks"]
               if check["check"] == "required_properties")


def test_failed_model_attempt_is_retained_in_call_log(monkeypatch, tmp_path):
    from app.services.llm import local_client, model_runtime

    recorder = SimpleNamespace(calls=[])
    monkeypatch.setattr(schema_card_tightened, "Recorder", lambda *_: recorder)
    monkeypatch.setattr(model_runtime, "model_scope", lambda **_: nullcontext())

    def failing_call(recorder, **kwargs):
        recorder.calls.append({"http_call": 1, "error_type": "TimeoutError"})
        raise TimeoutError("test timeout")

    monkeypatch.setattr(local_client, "chat_with_schema", failing_call)
    calls = []
    output = tmp_path / "attempt"
    with pytest.raises(TimeoutError):
        invoke(None, output, "run", "scope", "claims", "prompt", {},
               {"type": "object", "properties": {}, "required": [],
                "additionalProperties": False}, call_log=calls)
    assert calls == [{"http_call": 1, "error_type": "TimeoutError", "stage": "claims"}]
    assert json.loads((output / "calls.json").read_text()) == calls
    assert not (output / "proposal.json").exists()


def test_offline_recheck_rejects_old_state_without_changing_response_or_calling_model(
    monkeypatch, tmp_path,
):
    from app.services.llm import local_client

    source, output = tmp_path / "original", tmp_path / "rechecked"
    source.mkdir()
    (source / "source.docx").write_bytes(b"fixed test document")
    source_hash = schema_card_tightened.digest(source / "source.docx")
    monkeypatch.setattr(schema_card_tightened, "SOURCE_SHA256", source_hash)

    def no_model(*args, **kwargs):
        raise AssertionError("offline recheck must not access a model")

    monkeypatch.setattr(schema_card_tightened, "invoke", no_model)
    monkeypatch.setattr(local_client, "get_local_llm", no_model)
    sentence = "本次备样用于Ⅰ期临床。"
    original_observation = observation("u0", sentence, "not_applicable")
    catalog = {CMC: {"class_iri": CMC, "label": "报告", "properties": [],
                     "allowed_relations": []}}
    files = {
        "result.json": {"run_id": "original-run", "status": "completed", "results": [
            {"scope_id": "sample", "status": "complete", "observations": [original_observation]}
        ]},
        "cards.json": catalog,
        "ir.json": {"tables": []},
        "cases.json": [{"scope_id": "sample", "content": {"text": sentence, "section_path": ""},
                        "source_units": [{"evidence_id": "p0", "text": sentence,
                                          "table_path": None}]}],
        "reference.json": {"source_sha256": source_hash, "cases": {"sample": {"no_facts": True}}},
        "sample/discovery/proposal.json": {"frames": [], "observations": [original_observation]},
        "sample/claims/proposal.json": {"document": {"attributes": {}, "relations": {}}},
    }
    for name, value in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
    before = {str(path.relative_to(source)): path.read_bytes()
              for path in source.rglob("*") if path.is_file()}
    recheck_run(source, output)
    after = {str(path.relative_to(source)): path.read_bytes()
             for path in source.rglob("*") if path.is_file()}
    assert after == before
    manifest = json.loads((output / "result.json").read_text())
    assert manifest["execution_mode"] == "offline_recheck"
    assert manifest["new_model_calls"] == manifest["max_model_calls"] == 0
    assert manifest["source_run_id"] == "original-run"
    result = manifest["results"][0]
    assert result["status"] == "complete" and result["calls"] == []
    assert result["observations"] == []
    assert result["rejected"][0]["proposal"] == original_observation
    assert result["rejected"][0]["reason"] == "observation_state_not_supported"
    assert not result["acceptance"]["passed_with_no_rejections"]
