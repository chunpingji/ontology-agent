"""Section authoring must reach the shared renderer with real input references."""

from copy import deepcopy
from io import BytesIO

import pytest
from docx import Document

from app.services.extraction.evidence_identity import evidence_hash
from app.services.reporting.demo_sources import PROMPT_REF, builtin_contract, contract_ref
from app.services.reporting.narrative_renderer import local_provider
from app.services.reporting.output_renderer import render_docx, render_snapshot
from app.services.reporting.section_narrative import CUSTOM_PROMPT_REF, is_custom_draft
from app.services.reporting.template_compiler import compile_template
from app.services.reporting.template_v2 import ReportingError, TemplateV2
from tests.test_reporting.test_finder_report_compatibility import finder_source, resolve_finder
from tests.test_reporting.test_output_contracts import contracts, ontology, template


def configured():
    value = template()
    value["definitions"]["inputs"]["rows"]["projection"] = {
        "kind": "property", "property_iri": "urn:code",
    }
    section = value["sections"][0]
    section["title"] = "设备说明"
    section["narrative"] = {
        "enabled": True, "instructions": "以两段说明本节设备",
        "policy_ref": CUSTOM_PROMPT_REF,
        "input_refs": [{"input_id": "rows"}], "required_refs": [{"input_id": "rows"}],
    }
    section["groups"][0]["units"][0]["render"] = {
        "kind": "narrative", "nodes": [{"kind": "input_ref", "input_id": "rows"}],
    }
    return value


def compile_section(value):
    return compile_template(value, ontology(), {
        **contracts(), CUSTOM_PROMPT_REF: builtin_contract(CUSTOM_PROMPT_REF),
        PROMPT_REF: builtin_contract(PROMPT_REF),
        contract_ref("equipment"): builtin_contract(contract_ref("equipment")),
    })


def test_section_authoring_identity_and_single_expansion():
    legacy = TemplateV2.model_validate(template()).model_dump(mode="json")
    assert "narrative" not in legacy["sections"][0]
    with_null = deepcopy(legacy)
    with_null["sections"][0]["narrative"] = None
    assert evidence_hash(TemplateV2.model_validate(with_null)) == evidence_hash(legacy)
    value = configured()
    original = deepcopy(value)
    plan = compile_section(value)
    assert plan["valid"], plan["diagnostics"]
    assert value == original
    assert plan["schema_hash"] == evidence_hash(TemplateV2.model_validate(value))
    assert len(plan["template"]["sections"][0]["groups"]) == 2
    again = compile_section(plan["template"])
    assert again["valid"], again["diagnostics"]
    assert len(again["template"]["sections"][0]["groups"]) == 2
    value["sections"][0]["narrative"]["enabled"] = False
    assert len(compile_section(value)["template"]["sections"][0]["groups"]) == 1
    assert not is_custom_draft(value)


@pytest.mark.parametrize("field,value", [("instructions", " "), ("input_refs", [])])
def test_unconfigured_section_is_a_visible_compilation_gap(field, value):
    data = configured()
    data["sections"][0]["narrative"][field] = value
    plan = compile_section(data)
    assert not plan["valid"]
    assert "SECTION_NARRATIVE_UNCONFIGURED" in str(plan["diagnostics"])


def test_deleted_section_input_is_diagnosed_without_crashing():
    value = configured()
    value["sections"][0]["narrative"]["input_refs"] = [{"input_id": "deleted"}]
    plan = compile_section(value)
    assert not plan["valid"]
    assert "deleted" in str(plan["diagnostics"])


@pytest.mark.parametrize("own_prompt", ["", "保留此内容项的要求"])
def test_child_prompt_overrides_section_but_empty_prompt_inherits(own_prompt):
    value = configured()
    value["sections"][0]["groups"][0]["units"][0]["render"] = {
        "kind": "narrative", "mode": "assisted", "prompt": {
            **value["sections"][0]["narrative"], "instructions": own_prompt,
            "policy_ref": PROMPT_REF,
        },
    }
    del value["sections"][0]["groups"][0]["units"][0]["render"]["prompt"]["enabled"]
    plan = compile_section(value)
    assert plan["valid"], plan["diagnostics"]
    prompt = plan["template"]["sections"][0]["groups"][1]["units"][0]["render"]["prompt"]
    assert prompt["instructions"] == (own_prompt or "以两段说明本节设备")
    assert prompt["policy_ref"] == PROMPT_REF


def test_section_expansion_includes_derived_and_mock_filter_dependencies():
    value = configured()
    defs = value["definitions"]
    defs["bindings"]["mock"] = {
        "binding_id": "mock", "kind": "context", "contract_ref": contract_ref("equipment"),
        "scope": {"record_slot": "mock"},
    }
    defs["inputs"]["mock-rows"] = {
        "input_id": "mock-rows", "name": "mock-rows", "binding_ref": "mock",
        "projection": {"kind": "identity"},
    }
    value["record_sources"] = {"mock": {
        "provider": "equipment", "contract_ref": contract_ref("equipment"),
        "input_filters": {"equipment_id": {"input_id": "rows"}},
    }}
    defs["bindings"]["sorted"] = {
        "binding_id": "sorted", "kind": "derived", "provider": "view",
        "contract_ref": "auto:view", "operation": {
            "kind": "sort", "source": {"input_id": "mock-rows"},
            "order_by": [{"field_ref": "equipment_id"}],
        },
    }
    defs["inputs"]["sorted-rows"] = {
        "input_id": "sorted-rows", "name": "sorted-rows", "binding_ref": "sorted",
        "projection": {"kind": "identity"},
    }
    value["sections"][0]["narrative"].update(
        input_refs=[{"input_id": "sorted-rows"}], required_refs=[{"input_id": "sorted-rows"}],
    )
    plan = compile_section(value)
    assert plan["valid"], plan["diagnostics"]
    unit = plan["template"]["sections"][0]["groups"][0]["units"][0]
    assert {b["binding_ref"] for b in unit["bindings"]} == {"equipment", "mock", "sorted"}


def snapshot():
    source = finder_source()
    source["relationships"] = source["relationships"][:1]
    return resolve_finder(configured(), source, {
        CUSTOM_PROMPT_REF: builtin_contract(CUSTOM_PROMPT_REF),
    })


def mock_model(monkeypatch, text):
    monkeypatch.setattr("app.services.llm.local_client.get_local_llm", lambda: object())

    def response(_client, **kwargs):
        assert "设备" in kwargs["user"]
        assert "EQ-A" in kwargs["user"]
        return {"text": text}

    monkeypatch.setattr("app.services.llm.local_client.chat_with_schema", response)


def test_custom_section_is_rendered_with_citations_and_real_docx_paragraphs(monkeypatch):
    mock_model(monkeypatch, "本节采用的设备为[[input:0]]。\n\n请结合原文核对设备说明。")
    result = render_snapshot(snapshot(), provider=local_provider)
    assert result["execution_status"] == "completed", result
    first = result["output_results"][0]
    assert first["citations"][0]["provenance_refs"][0]["kind"] == "finder_demo"
    doc = Document(BytesIO(render_docx(result["body_ast"])))
    paragraphs = [p.text for p in doc.paragraphs]
    assert "本节采用的设备为EQ-A。" in paragraphs
    assert "请结合原文核对设备说明。" in paragraphs
    assert "[[input:" not in str(paragraphs)


@pytest.mark.parametrize("text", ["设备说明", "[[input:9]]", "[[input:0]] [[unknown]]"])
def test_custom_text_cannot_omit_or_invent_input_references(monkeypatch, text):
    mock_model(monkeypatch, text)
    result = render_snapshot(snapshot(), provider=local_provider)
    assert result["execution_status"] == "failed"
    assert "OUTPUT_REFERENCE_INVALID" in str(result)


def test_custom_section_requires_draft_source_bundle():
    value = snapshot()
    value["source_bundle"]["demonstration"] = False
    result = render_snapshot(value, provider=lambda *_: pytest.fail("must not call model"))
    assert result["execution_status"] == "failed"
    assert "DEMO_REPORT_ONLY" in str(result)


def test_custom_schema_cannot_start_formal_report(db):
    from app.models.extraction import AstTemplate
    from app.services.reporting.report_run_service import ReportRunService

    row = AstTemplate(
        name="Custom", version="v1", schema_json=configured(), status="published",
        created_by="tester", recognition_mode="ontology_guided",
    )
    db.add(row)
    db.commit()
    with pytest.raises(ReportingError) as error:
        ReportRunService(db).start({
            "template_id": str(row.id), "purpose": "formal", "idempotency_key": "formal",
        }, "tester")
    assert error.value.code == "DEMO_REPORT_ONLY"


def test_prompt_design_endpoint_calls_model_and_preserves_role_gate(
    client, analyst_headers, operator_headers, monkeypatch,
):
    from app.config import settings
    from tests.test_reporting.test_template_meta_and_section_prompt import _one_shot_client

    monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
    model = _one_shot_client({"prompt": "使用设备编号变量组织两段说明，保留来源。"})
    monkeypatch.setattr("app.services.llm.local_client.get_local_llm", lambda: model)
    payload = {"section_title": "设备说明", "slot_labels": ["设备编号"], "instructions": "两段"}
    path = "/api/ast-templates/generate-section-prompt"
    denied = client.post(path, headers=operator_headers, json=payload)
    assert denied.status_code == 403
    response = client.post(path, headers=analyst_headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["prompt"].startswith("使用设备编号")
    monkeypatch.setattr(settings, "llm_suggest_slots_enabled", False)
    assert client.post(path, headers=analyst_headers, json=payload).status_code == 503
    monkeypatch.setattr(settings, "llm_suggest_slots_enabled", True)
    monkeypatch.setattr("app.services.llm.local_client.get_local_llm", lambda: None)
    assert client.post(path, headers=analyst_headers, json=payload).status_code == 503


def test_section_layout_includes_prose_without_model_execution(
    client, analyst_headers, monkeypatch,
):
    monkeypatch.setattr(
        "app.services.llm.local_client.get_local_llm", lambda: pytest.fail("layout called model"),
    )
    value = configured()
    response = client.post("/api/report-previews", headers=analyst_headers, json={
        "mode": "layout", "draft_schema": value, "idempotency_key": "layout-section",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert "设备说明 · AI 行文" in str(body["body_ast"])
    assert body["business_values"] is False
    assert len(value["sections"][0]["groups"]) == 1


def test_section_prompt_revision_round_trip_and_conflict(client, db, analyst_headers):
    from app.models.extraction import AstTemplate
    from app.services.reporting.report_run_service import schema_hash

    original = configured()
    original["sections"][0].pop("narrative")
    row = AstTemplate(
        name="Section round trip", version="v2.1", schema_json=original,
        schema_version=2, template_family_id="family", revision_no=1,
        status="draft", created_by="analyst",
    )
    db.add(row)
    db.commit()
    payload = {
        "expected_hash": schema_hash(original), "expected_revision": 1, "schema": configured(),
    }
    path = f"/api/ast-templates/{row.id}/revisions"
    saved = client.post(path, headers=analyst_headers, json=payload)
    assert saved.status_code == 201, saved.text
    got = client.get(f"/api/ast-templates/{saved.json()['id']}", headers=analyst_headers)
    assert got.status_code == 200
    schema = got.json()["schema_json"]
    assert schema["sections"][0]["narrative"]["instructions"] == "以两段说明本节设备"
    assert schema["sections"][0]["narrative"]["enabled"]
    assert len(schema["sections"][0]["groups"]) == 1  # Save only authoring, no compiled copy.
    assert got.json()["schema_hash"] == schema_hash(schema)
    db.refresh(row)
    assert "narrative" not in row.schema_json["sections"][0]
    assert client.post(path, headers=analyst_headers, json=payload).status_code == 409
