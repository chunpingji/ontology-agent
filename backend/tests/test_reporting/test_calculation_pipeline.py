from copy import deepcopy
from io import BytesIO

import pytest
from docx import Document

from app.schemas.evidence import DerivedProvenance
from app.services.extraction.evidence_identity import evidence_hash
from app.services.reasoning.pde_calculation import (
    DEV,
    PDE_METHOD_REF,
    calculation_contract,
    default_checks,
    evaluate_candidates,
)
from app.services.reporting.output_ast import plain_text, walk
from app.services.reporting.output_renderer import render_docx, render_snapshot
from app.services.reporting.report_snapshot import resolve_snapshot
from app.services.reporting.template_compiler import compile_template, require_valid
from tests.test_reasoning.test_pde_calculation_v1 import candidates
from tests.test_reporting.test_fact_selector import snapshot
from tests.test_reporting.test_output_contracts import contracts, template


def source_case(rows=None, decisions=()):
    rows = candidates() if rows is None else rows
    t = template()
    t["source_slots"] = [
        {"source_slot_id": "doc", "kind": "document", "class_iri": DEV + "CMCReport"}
    ]
    t["definitions"] = {"bindings": {}, "inputs": {}}
    t["calculation_checks"] = default_checks(t["source_slots"])
    t["sections"][0]["groups"][0]["units"] = [
        {
            "output_id": "conclusion",
            "title": "结论",
            "bindings": [],
            "inputs": [],
            "render": {
                "kind": "narrative",
                "mode": "composed",
                "nodes": [{"kind": "text", "text": "所有风险可接受"}],
            },
        }
    ]
    resources = {**contracts(), PDE_METHOD_REF: calculation_contract()}
    method = calculation_contract()["definition"]
    schema = {
        method["root_class_iri"]: {
            "parents": [],
            "properties": [],
            "relationships": [
                {"iri": method["predicate_path"][0], "range": [method["subject_class_iri"]]}
            ],
        },
        method["subject_class_iri"]: {
            "parents": [],
            "relationships": [],
            "properties": [
                {
                    "iri": p["predicate_iri"],
                    "datatype": "http://www.w3.org/2001/XMLSchema#"
                    + ("string" if "type" in p else "decimal"),
                }
                for p in method["parameters"].values()
            ],
        },
    }
    plan = require_valid(compile_template(t, schema, resources))
    source = {
        "template": plan["template"],
        "schema": schema,
        "contracts": resources,
        "applicable_at": None,
        "sources": {
            "doc": {
                "snapshot": snapshot(*rows),
                "root_entity_id": "urn:evidence:entity:root",
                "candidates": [c.model_dump(mode="json") for c in rows],
                "calculation_decisions": list(decisions),
                "extraction_completion": "complete",
            }
        },
    }
    source["source_bundle_id"] = evidence_hash(source)
    return plan, source


def decision(rows, choice="derived"):
    r = evaluate_candidates(rows)[0]
    return {
        "subject_candidate_id": "study",
        "calculation_id": r["calculation_id"],
        "revision": 1,
        "choice": choice,
        "reason": "复核全部参数",
        "actor": "analyst",
        "decided_at": "2026-09-07T00:00:00Z",
    }


def test_pending_conflict_blocks_conclusion_in_browser_docx_and_publication_material():
    plan, source = source_case()
    result = resolve_snapshot(plan, source)
    rendered = render_snapshot(result)
    text = plain_text(rendered["body_ast"])
    assert result["material_status"] == "conflict"
    assert "待评估" in text and "所有风险可接受" not in text
    assert "1.8 mg/day" in text and "0.05 mg/day" in text and "36" in text
    doc = Document(BytesIO(render_docx(rendered["body_ast"])))
    exported = "\n".join(p.text for p in doc.paragraphs)
    assert "所有风险可接受" not in exported and "0.05 mg/day" in exported
    assert "输入引用 · NOAEL" in exported and "synthetic evidence" in exported
    assert "位置：paragraph:1" in exported and "修订 1" in exported
    citations = [
        n for n in walk(rendered["body_ast"]) if n.input_ref and n.input_ref.get("calculation_id")
    ]
    assert citations and all(n.fact_refs and n.provenance_refs for n in citations)
    assert all(n.input_ref["input_id"] == "pde:doc" and n.input_ref["record_id"] for n in citations)


@pytest.mark.parametrize("choice,expected", [("derived", "0.05"), ("asserted", "1.8")])
def test_decision_selects_effective_value_keeps_both_and_frozen_replay(choice, expected):
    rows = candidates()
    plan, source = source_case(rows, [decision(rows, choice)])
    resolved = resolve_snapshot(plan, source)
    rendered = render_snapshot(resolved)
    text = plain_text(rendered["body_ast"])
    assert resolved["material_status"] == "ready"
    assert "所有风险可接受" in text
    assert "报告采用 PDE：" + expected + " mg/day" in text
    assert (
        "1.8 mg/day" in text
        and "0.05 mg/day" in text
        and "复核全部参数" in text
        and "analyst" in text
    )
    source["sources"]["doc"]["calculation_decisions"][0]["choice"] = "rejected"
    assert render_snapshot(resolved)["body_hash"] == rendered["body_hash"]
    assert resolve_snapshot(plan, source)["material_status"] != "ready"


def test_missing_parameters_unpublished_edits_and_open_discovery_cannot_pass():
    plan, source = source_case(candidates(pde="0.05"))
    assert resolve_snapshot(plan, source)["material_status"] == "ready"
    for edit in ("not_published", "not_finished", "no_entities"):
        data = deepcopy(source)
        slot = data["sources"]["doc"]
        if edit == "not_published":
            slot["candidates"][-1]["revision"] += 1
        elif edit == "not_finished":
            slot["extraction_completion"] = "incomplete"
        else:
            slot["snapshot"]["assertions"] = []
        result = resolve_snapshot(plan, data)
        assert result["material_status"] != "ready"
        assert "所有风险可接受" not in plain_text(render_snapshot(result)["body_ast"])


@pytest.mark.parametrize("edit", ["rejected", "revision", "removed", "root_changed"])
def test_unpublished_source_binding_changes_block_even_when_numbers_still_match(edit):
    plan, source = source_case(candidates(pde="0.05"))
    slot = source["sources"]["doc"]
    relation = next(c for c in slot["candidates"] if c["kind"] == "relationship")
    if edit == "rejected":
        relation["review_status"] = "rejected"
    elif edit == "revision":
        relation["revision"] += 1
    elif edit == "removed":
        slot["candidates"].remove(relation)
    else:
        relation["subject"]["candidate_id"] = "another_root"
        relation["bindings"][0]["subject_candidate_id"] = "another_root"
    result = resolve_snapshot(plan, source)
    assert result["material_status"] == "incomplete"
    assert any(p["code"] == "CALCULATION_INPUTS_NOT_PUBLISHED" for p in result["blocking_issues"])
    assert "所有风险可接受" not in plain_text(render_snapshot(result)["body_ast"])


def test_explicit_oeb_disagreement_has_an_explanation_even_when_pde_matches():
    plan, source = source_case(candidates(pde="0.05", oebBand=("OEB 1", "text")))
    result = resolve_snapshot(plan, source)
    text = plain_text(render_snapshot(result)["body_ast"])
    assert result["material_status"] == "conflict"
    assert "原文明确 OEB：1" in text
    assert "原文明确给出的 OEB 与计算分档不同" in text


def test_typed_calculation_provider_can_supply_report_input():
    rows = candidates(pde="0.05")
    plan, source = source_case(rows)
    t = plan["template"]
    t["definitions"]["bindings"]["calculated"] = {
        "binding_id": "calculated",
        "kind": "derived",
        "provider": "calculation",
        "contract_ref": PDE_METHOD_REF,
        "check_ref": "pde:doc",
    }
    t["definitions"]["inputs"]["pde_rows"] = {
        "input_id": "pde_rows",
        "name": "pde_rows",
        "binding_ref": "calculated",
        "projection": {"kind": "identity"},
    }
    unit = t["sections"][0]["groups"][0]["units"][0]
    unit["bindings"] = [{"binding_ref": "calculated"}]
    unit["inputs"] = [{"input_ref": "pde_rows", "alias": "pde_rows"}]
    unit["render"] = {
        "kind": "table",
        "rows": {"input_id": "pde_rows"},
        "columns": [{"column_id": "effective", "title": "采用 PDE", "field_ref": "effective_pde"}],
    }
    plan = require_valid(compile_template(t, source["schema"], source["contracts"]))
    source["template"] = plan["template"]
    resolved = resolve_snapshot(plan, source)
    assert resolved["inputs"]["pde_rows"]["items"][0]["fields"]["effective_pde"]["value"] == {
        "value": "0.05",
        "unit": "mg/day",
        "dimension": "mass/time",
    }
    assert "0.05 mg/day" in plain_text(render_snapshot(resolved)["body_ast"])
    values = resolved["inputs"]["pde_rows"]["items"][0]["fields"]["effective_pde"]
    trace = DerivedProvenance.model_validate(values["provenance_refs"][0])
    assert trace.value["effective_pde"]["value"] == "0.05"
    assert trace.parameters["method_hash"] == calculation_contract()["definition_hash"]
    assert any(p.get("anchors") for p in values["provenance_refs"])


def test_tampered_method_and_wrong_source_cannot_compile():
    plan, source = source_case()
    contract = source["contracts"][PDE_METHOD_REF]
    contract["definition"]["defaults"]["bw"] = "999"
    contract["definition_hash"] = evidence_hash(contract["definition"])
    assert not compile_template(plan["template"], source["schema"], source["contracts"])["valid"]
    plan["template"]["calculation_checks"][0]["source_slot"] = "other_document"
    assert not compile_template(plan["template"], source["schema"], source["contracts"])["valid"]


def test_removing_mandatory_check_cannot_bypass_report_gate():
    plan, source = source_case()
    plan["template"]["calculation_checks"] = []
    result = compile_template(plan["template"], source["schema"], source["contracts"])
    assert result["valid"]
    assert result["template"]["calculation_checks"]
    snapshot = resolve_snapshot(result, source)
    assert snapshot["calculation_checks"]
