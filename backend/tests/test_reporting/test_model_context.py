from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
from docx import Document
from sqlalchemy import select

from app.models.extraction import AstTemplate
from app.models.ontology_meta import OntologyRelease
from app.models.reporting import ContractDecision, OntologySchemaSnapshot
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.literal_normalizer import normalize_literal
from app.services.ontology_model_context import MODEL_PREFIX, ModelContext, capture_schema
from app.services.reporting.contract_registry import ContractRegistry
from app.services.reporting.output_ast import plain_text
from app.services.reporting.output_renderer import render_docx, render_snapshot
from app.services.reporting.report_run_service import ReportRunService, schema_hash
from app.services.reporting.report_snapshot import resolve_snapshot
from app.services.reporting.template_compiler import compile_template
from app.services.reporting.template_preparation import prepare_template
from app.services.reporting.template_v2 import ReportingError, TemplateV2
from tests.test_reporting.test_fact_selector import property_value
from tests.test_reporting.test_output_contracts import contracts, ontology, template
from tests.test_reporting.test_output_resolution import bundle


def automatic_template():
    t = template()
    t.update(
        ontology_release_ref="auto:ontology",
        style_profile_ref="auto:style",
        publication_policy_ref="auto:policy",
    )
    t["definitions"]["bindings"]["equipment"]["contract_ref"] = {"kind": "ontology"}
    return t


def test_model_capture_auto_binding_types_and_publication_are_separate(db):
    service = ReportRunService(db, model_schema=ontology())
    t = automatic_template()
    plan, classes, resources = service.compile(t, "analyst")
    assert plan["valid"], plan["diagnostics"]
    ref = plan["template"]["ontology_release_ref"]
    assert ref == MODEL_PREFIX + evidence_hash(ontology())
    assert plan["schema_hash"] == schema_hash(t)
    bound = plan["template"]["definitions"]["bindings"]["equipment"]["contract_ref"]
    assert bound["release_ref"] == ref
    assert bound["root_class_iri"] == "urn:Report"
    assert bound["result_class_iri"] == "urn:Equipment"
    assert resources[ref]["status"] == "captured"
    assert not list(db.scalars(select(ContractDecision)))
    assert len(list(db.scalars(select(OntologySchemaSnapshot)))) == 1
    row = AstTemplate(
        id=uuid4(),
        name="Automatic",
        version="v1",
        schema_json=t,
        status="draft",
        schema_hash=schema_hash(t),
    )
    db.add(row)
    db.flush()
    saved, _, _ = service.compile(t, "analyst", row.id)
    with pytest.raises(ReportingError) as unpublished:
        service.publish(row, schema_hash(t), saved["compilation_id"], "analyst")
    assert unpublished.value.code == "ONTOLOGY_MODEL_NOT_PUBLISHED"
    db.add(
        OntologyRelease(
            release_no="R-1",
            title="Reviewed model",
            status="published",
            semantic_snapshot_ref=ref[len(MODEL_PREFIX) :],
        )
    )
    db.flush()
    assert ModelContext(db).resolve(ref)["status"] == "published"
    # Publication state invalidates compilation but never changes the captured content.
    newer, _, _ = service.compile(t, "analyst", row.id)
    assert newer["compilation_id"] != saved["compilation_id"]
    assert classes == ontology()


def test_automatic_resolution_reuses_published_contract_but_never_repairs_explicit_refs(db):
    registry = ContractRegistry(db)
    row = registry.create(
        kind="ontology",
        family_id="model",
        revision_no=1,
        definition={"classes": ontology()},
        actor="analyst",
        server_ontology=True,
    )
    for decision in ("reviewed", "published"):
        registry.decide(
            row.id,
            expected_hash=row.content_hash,
            decision=decision,
            reason="Synthetic reviewed model",
            actor="analyst",
        )
    assert (
        prepare_template(db, automatic_template(), classes=ontology()).ontology_release_ref
        == row.id
    )
    with pytest.raises(ReportingError, match="CONTRACT_NOT_FOUND"):
        ModelContext(db, ontology()).resolve("explicit-wrong-ref")
    invalid = template()
    invalid["definitions"]["bindings"]["equipment"]["contract_ref"]["release_ref"] = "other"
    plan = compile_template(invalid, ontology(), contracts())
    assert any(d["code"] == "ONTOLOGY_RELEASE_MISMATCH" for d in plan["diagnostics"])


def test_model_compatibility_ignores_unrelated_fields_but_detects_units_and_missing_history(db):
    schema = ontology()
    old = deepcopy(schema)
    old["urn:Other"] = {"parents": [], "properties": [], "relationships": []}
    compatible_ref = capture_schema(db, old)
    incompatible = deepcopy(schema)
    incompatible["urn:Equipment"]["properties"][0]["datatype"] = "decimal"
    incompatible["urn:Equipment"]["properties"][0]["canonical_unit"] = "kg"
    incompatible_ref = capture_schema(db, incompatible)
    refs = [evidence_hash(schema), compatible_ref, incompatible_ref, "unknown-old-version"]
    result = ModelContext(db).compatibility(
        [{"ontology_release": ref} for ref in refs], template(), schema, contracts()
    )
    states = {r["ontology_release"]: r for r in result}
    assert [states[r]["status"] for r in refs] == [
        "identical",
        "compatible",
        "incompatible",
        "unknown",
    ]
    assert any("urn:code" in key for key in states[incompatible_ref]["affected_fields"])
    data = bundle(property_value("code", "E", "urn:code", "E-01"))
    data["sources"]["doc"]["model_compatibility"] = [states["unknown-old-version"]]
    plan = compile_template(template(), schema, contracts())
    resolved = resolve_snapshot(plan, data)
    assert resolved["material_status"] == "incomplete"
    assert not resolved["inputs"]["rows"]["items"]
    assert any(p["code"] == "SOURCE_ONTOLOGY_VERSION_UNKNOWN" for p in resolved["blocking_issues"])


@pytest.mark.parametrize("literal,expected", [("1500 g", "1.5"), ("2 kg", "2"), ("3", "3")])
def test_ontology_canonical_unit_reaches_values_browser_and_docx(literal, expected):
    schema = ontology()
    schema["urn:Equipment"]["properties"][0].update(datatype="decimal", canonical_unit="kg")
    value = property_value("code", "E", "urn:code", "placeholder")
    value.literal = normalize_literal(literal, datatype="decimal")
    data = bundle(value)
    data["schema"] = schema
    plan = compile_template(template(), schema, contracts())
    assert plan["valid"], plan["diagnostics"]
    typ = plan["input_types"]["rows"]["item_type"]["fields"]["code"]["type"]
    assert (typ["kind"], typ["unit"], typ["dimension"]) == ("quantity", "kg", "mass")
    resolved = resolve_snapshot(plan, data)
    cell = resolved["inputs"]["rows"]["items"][0]["fields"]["code"]
    assert cell["state"] == "ready", cell["issues"]
    assert cell["value"] == {"value": expected, "unit": "kg", "dimension": "mass"}
    resolved["source_bundle"]["template"] = plan["template"]
    rendered = render_snapshot(resolved)
    assert expected + " kg" in plain_text(rendered["body_ast"])
    document = Document(BytesIO(render_docx(rendered["body_ast"])))
    assert any(
        expected + " kg" in cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )


def test_union_endpoint_requires_explicit_choice_and_retains_narrowing():
    schema = ontology()
    schema["urn:Report"]["relationships"][0]["range"].append("urn:Material")
    t = template()
    t["definitions"]["bindings"]["equipment"]["contract_ref"]["result_class_iri"] = "auto:class"
    assert not compile_template(t, schema, contracts())["valid"]
    t["definitions"]["bindings"]["equipment"]["contract_ref"]["result_class_iri"] = "urn:Equipment"
    assert compile_template(t, schema, contracts())["valid"]


def test_new_optional_style_does_not_change_legacy_schema_identity():
    old = TemplateV2.model_validate(template()).model_dump(mode="json")
    assert "style" not in old
    assert schema_hash(old) == evidence_hash(old)


def test_generated_range_view_reuses_ontology_units_and_requires_business_boundaries():
    t, schema = template(), ontology()
    for prop in schema["urn:Equipment"]["properties"]:
        prop.update(datatype="decimal", canonical_unit="kg")
    t["definitions"]["inputs"]["rows"]["projection"]["kind"] = "record"
    t["definitions"]["bindings"]["range"] = {
        "binding_id": "range",
        "kind": "derived",
        "provider": "view",
        "contract_ref": "auto:view",
        "operation": {
            "kind": "range",
            "source": {"input_id": "rows"},
            "lower_field": "code",
            "upper_field": "spec",
        },
    }
    t["definitions"]["inputs"]["range"] = {
        "input_id": "range",
        "name": "range",
        "label": "范围",
        "binding_ref": "range",
        "projection": {"kind": "identity"},
    }
    unit = t["sections"][0]["groups"][0]["units"][0]
    unit["bindings"].append({"binding_ref": "range"})
    unit["inputs"].append({"input_ref": "range", "alias": "range"})
    unit["render"] = {
        "kind": "narrative",
        "mode": "composed",
        "nodes": [{"kind": "input_ref", "input_id": "range"}],
    }
    blocked = compile_template(t, schema, contracts())
    codes = {d["code"] for d in blocked["diagnostics"]}
    assert "RANGE_BOUNDARY_UNRESOLVED" in codes
    assert "UNKNOWN_CONTRACT" not in codes
    t["definitions"]["bindings"]["range"]["operation"].update(
        lower_inclusive=True, upper_inclusive=False
    )
    plan = compile_template(t, schema, contracts())
    assert plan["valid"], plan["diagnostics"]
    generated = next(iter(plan["generated_contracts"].values()))
    assert generated["status"] == "compiled"
    assert generated["definition"]["output_type"]["item_type"]["unit"] == "kg"
    values = [
        property_value("code", "E", "urn:code", "placeholder"),
        property_value("spec", "E", "urn:spec", "placeholder"),
    ]
    for value, raw in zip(values, ("1500 g", "3 kg")):
        value.literal = normalize_literal(raw, datatype="decimal")
    data = bundle(*values)
    data.update(schema=schema, template=plan["template"])
    resolved = resolve_snapshot(plan, data)
    assert resolved["inputs"]["range"]["state"] == "ready"
    value = resolved["inputs"]["range"]["value"]
    assert value["lower"]["value"] == "1.5"
    assert value["upper"]["unit"] == "kg"
    assert value["lower_inclusive"] is True and value["upper_inclusive"] is False


def test_explicit_binding_output_constraint_is_checked():
    t = template()
    t["definitions"]["bindings"]["equipment"]["output_type"] = {"kind": "string"}
    result = compile_template(t, ontology(), contracts())
    assert not result["valid"]
    assert any(d["code"] == "INPUT_TYPE_MISMATCH" for d in result["diagnostics"])
