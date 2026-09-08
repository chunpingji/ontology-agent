import pytest

from app.services.reporting.claim_catalog import claim_eligibility
from app.services.reporting.contract_registry import validate_contract
from app.services.reporting.input_resolver import typed_value
from app.services.reporting.narrative_renderer import assisted_nodes
from app.services.reporting.template_compiler import compile_template
from app.services.reporting.template_v2 import Budget, NarrativeRender, ReportingError
from tests.test_reporting.test_output_contracts import contracts, ontology, template


@pytest.mark.parametrize("mismatch", [None, "subject", "time", "nature", "scope", "disabled"])
def test_claim_requires_exact_observed_subject_time_and_scope(mismatch):
    declaration = {
        "category": "observed_fact",
        "text": "该设备已有确认记录。",
        "review_ref": "approved",
        "precondition": {"op": "literal", "value": True},
        "applicable_scope": {},
        "subject_parameter": "equipment",
        "evidence_parameters": ["record"],
    }
    data = {
        "applicable_at": "2026-01",
        "contracts": {"claim": {"kind": "claim", "status": "published", "definition": declaration}},
    }
    equipment = typed_value(
        "equipment",
        {"kind": "entity", "class_iris": ["urn:Equipment"]},
        {"entity_id": "urn:E1", "class_iri": "urn:Equipment"},
        scope="s",
    )
    evidence = typed_value(
        "record",
        {"kind": "string"},
        "已确认",
        scope="s",
        subject=["urn:E1"],
        refs=["published-fact"],
        derivation={"business_nature": "observed_fact"},
    )
    evidence.applicable_scope = {"applicable_at": "2026-01"}
    if mismatch == "subject":
        evidence.subject_refs = ["urn:E2"]
    elif mismatch == "time":
        evidence.applicable_scope["applicable_at"] = "2026-02"
    elif mismatch == "nature":
        evidence.derivation["business_nature"] = "planned_control"
    elif mismatch == "scope":
        evidence.execution_scope_id = "other"
    elif mismatch == "disabled":
        data["contracts"]["claim"]["is_disabled"] = True
    result = claim_eligibility(
        "claim", data, {"equipment": equipment, "record": evidence}, "rule-evaluation", "s"
    )
    assert result["eligible"] is (mismatch is None)


@pytest.mark.parametrize("lower,upper,expected", [("2", "10", "ready"), ("10", "2", "invalid")])
def test_quantity_range_compares_numeric_endpoints(lower, upper, expected):
    typ = {
        "kind": "range",
        "lower_inclusive": True,
        "upper_inclusive": False,
        "item_type": {"kind": "quantity", "unit": "kg", "dimension": "mass"},
    }
    value = {
        "lower": {"value": lower, "unit": "kg", "dimension": "mass"},
        "upper": {"value": upper, "unit": "kg", "dimension": "mass"},
        "lower_inclusive": True,
        "upper_inclusive": False,
    }
    assert typed_value("batch", typ, value).state == expected


def test_table_format_is_compiled_against_field_type():
    value = template()
    value["sections"][0]["groups"][0]["units"][0]["render"]["columns"][0]["format"] = {
        "kind": "date"
    }
    plan = compile_template(value, ontology(), contracts())
    assert "OUTPUT_FORMAT_INCOMPATIBLE" in {d["code"] for d in plan["diagnostics"]}


def test_view_cannot_relabel_string_as_boolean():
    value, registered = template(), contracts()
    registered["view"] = {
        "kind": "view",
        "status": "published",
        "definition": {
            "allowed_operations": ["project"],
            "output_type": {
                "kind": "list",
                "item_identity": "entity_id",
                "item_type": {
                    "kind": "record",
                    "fields": {"code": {"type": {"kind": "boolean"}, "semantic_ref": "urn:code"}},
                },
            },
        },
    }
    value["definitions"]["bindings"]["view"] = {
        "binding_id": "view",
        "kind": "derived",
        "provider": "view",
        "contract_ref": "view",
        "operation": {
            "kind": "project",
            "source": {"input_id": "rows"},
            "fields": {"code": ["code"]},
        },
    }
    value["definitions"]["inputs"]["projected"] = {
        "input_id": "projected",
        "name": "projected",
        "binding_ref": "view",
        "projection": {"kind": "identity"},
    }
    unit = value["sections"][0]["groups"][0]["units"][0]
    unit["bindings"].append({"binding_ref": "view"})
    unit["inputs"].append({"input_ref": "projected", "alias": "projected"})
    plan = compile_template(value, ontology(), registered)
    assert "INPUT_TYPE_MISMATCH" in {d["code"] for d in plan["diagnostics"]}


def test_assisted_rejects_invented_words_even_when_references_are_valid():
    render = NarrativeRender.model_validate(
        {
            "kind": "narrative",
            "mode": "assisted",
            "prompt": {
                "policy_ref": "policy",
                "input_refs": [{"input_id": "code"}],
                "required_refs": [{"input_id": "code"}],
            },
        }
    )
    nodes = [
        {"kind": "input_ref", "input_id": "code"},
        {"kind": "text", "text": "已确认风险可接受。"},
    ]
    with pytest.raises(ReportingError, match="unapproved generated wording"):
        assisted_nodes(
            render,
            {"code": typed_value("code", {"kind": "string"}, "E-01")},
            {},
            {"max_input_tokens": 8192, "model": "fixture"},
            Budget(),
            lambda *args: {"nodes": nodes},
        )


def test_vocabulary_requires_distinct_codes_and_matching_labels():
    good = {
        "values": ["unknown", "low"],
        "labels": {"unknown": "待评估"},
        "description": "reviewed",
    }
    assert validate_contract("vocabulary", good)["values"] == ["unknown", "low"]
    with pytest.raises(ReportingError, match="VOCABULARY_TYPE_MISMATCH"):
        validate_contract("vocabulary", {**good, "values": ["unknown", "low", "low"]})


@pytest.mark.parametrize("mismatch", [None, "property", "class", "release", "unpublished"])
def test_property_type_contract_requires_exact_registered_scope(mismatch):
    from app.services.reporting.report_snapshot import resolve_snapshot
    from app.services.reporting.template_compiler import require_valid
    from tests.test_reporting.test_fact_selector import property_value
    from tests.test_reporting.test_output_resolution import bundle

    value, registered, schema = template(), contracts(), ontology()
    schema["urn:Equipment"]["properties"][0]["datatype"] = (
        "http://www.w3.org/2001/XMLSchema#decimal"
    )
    declared = {
        "ontology_release_ref": "ontology-1",
        "subject_class_iri": "urn:Equipment",
        "property_iri": "urn:code",
        "description": "Reviewed mass meaning",
        "output_type": {"kind": "quantity", "unit": "kg", "dimension": "mass"},
    }
    if mismatch in {"property", "class", "release"}:
        declared[
            {
                "property": "property_iri",
                "class": "subject_class_iri",
                "release": "ontology_release_ref",
            }[mismatch]
        ] = "urn:wrong"
    registered["mass-type"] = {
        "kind": "property_type",
        "status": "draft" if mismatch == "unpublished" else "published",
        "definition": declared,
    }
    value["definitions"]["inputs"]["rows"]["projection"]["fields"]["code"]["value"][
        "type_contract_ref"
    ] = "mass-type"
    plan = compile_template(value, schema, registered)
    assert plan["valid"] is (mismatch is None), plan["diagnostics"]
    if mismatch is None:
        mass_fact = property_value("mass", "E", "urn:code", "10.123456789")
        mass_fact.literal.kind = "number"
        mass_fact.literal.datatype_iri = "http://www.w3.org/2001/XMLSchema#decimal"
        data = bundle(mass_fact)
        data.update(schema=schema, contracts=registered)
        resolved = resolve_snapshot(require_valid(plan), data)
        mass = resolved["inputs"]["rows"]["items"][0]["fields"]["code"]
        assert mass["value"] == {"value": "10.123456789", "unit": "kg", "dimension": "mass"}
        assert mass["derivation"]["type_contract_ref"] == "mass-type"
        assert mass["fact_refs"]


@pytest.mark.parametrize(
    "factor,offset", [("NaN", "0"), ("-1", "0"), ("0", "0"), ("1", "Infinity")]
)
def test_nonfinite_or_nonpositive_conversion_contracts_are_rejected(factor, offset):
    with pytest.raises(ReportingError, match="UNIT_INCOMPATIBLE"):
        validate_contract(
            "conversion",
            {
                "from_unit": "g",
                "to_unit": "kg",
                "dimension": "mass",
                "factor": factor,
                "offset": offset,
                "description": "invalid fixture",
            },
        )


def test_default_action_cannot_fabricate_parameter_values():
    from pydantic import ValidationError

    from app.services.reporting.template_v2 import StatusPolicy

    with pytest.raises(ValidationError, match="PARAMETER_DEFAULT_CONTRACT_REQUIRED"):
        StatusPolicy(missing="parameter_default")


def test_discovery_reference_cannot_borrow_another_selector_proof():
    from tests.test_reporting.test_fact_selector import property_value
    from tests.test_reporting.test_output_resolution import bundle, resolve

    data = bundle(property_value("code", "E", "urn:code", "E-01"))
    proofs = data["sources"]["doc"]["discovery"]
    proofs["unrelated-selector"] = next(iter(proofs.values()))
    del proofs[next(key for key in proofs if key != "unrelated-selector")]
    value = template()
    value["definitions"]["bindings"]["equipment"]["scope"]["discovery_ref"] = "unrelated-selector"
    result = resolve(data, value)
    assert result["inputs"]["rows"]["discovery"]["status"] == "open"


def test_frozen_coverage_tasks_keep_nested_subjects_and_optional_issues_independent(monkeypatch):
    from app.services.fact_selector import FactSelector
    from app.services.reporting.coverage_v2 import coverage_tasks
    from app.services.reporting.report_snapshot import resolve_snapshot
    from app.services.reporting.template_compiler import require_valid
    from tests.test_extraction.test_fact_commit import entity
    from tests.test_reporting.test_fact_selector import iri, property_value, relation
    from tests.test_reporting.test_output_resolution import bundle

    value = template()
    value["definitions"]["inputs"]["rows"]["projection"]["fields"]["material"] = {
        "required": True,
        "value": {
            "kind": "record",
            "predicate_path": [{"predicate_iri": "urn:material"}],
            "fields": {
                "name": {
                    "required": True,
                    "value": {"kind": "property", "property_iri": "urn:label"},
                }
            },
        },
    }
    data = bundle(
        property_value("code", "E", "urn:code", "E-01"),
        property_value("s1", "E", "urn:spec", "one"),
        property_value("s2", "E", "urn:spec", "two"),
        entity("M", "urn:Material"),
        relation("em", "E", "M", "urn:material"),
    )
    data["sources"]["doc"]["job_id"] = "job"
    plan = require_valid(compile_template(value, ontology(), contracts()))
    data["template"] = plan["template"]
    frozen = resolve_snapshot(plan, data)
    monkeypatch.setattr(
        FactSelector,
        "select",
        lambda *a, **k: pytest.fail("coverage must use frozen selection traces"),
    )
    tasks = coverage_tasks(frozen, "job", plan["compilation_id"])
    code = next(t for t in tasks if t["field_path"] == ["code"])
    spec = next(t for t in tasks if t["field_path"] == ["spec"])
    material = next(t for t in tasks if t["field_path"] == ["material"])
    name = next(t for t in tasks if t["field_path"] == ["material", "name"])
    assert code["status"] == "filled" and code["subject_instance_iri"] == iri("E")
    assert spec["status"] == "conflict" and not spec["required"]
    assert material["range_class_iri"] == "urn:Material"
    assert material["subject_instance_iri"] == iri("E")
    assert name["status"] == "missing" and name["subject_instance_iri"] == iri("M")
    assert name["objects"][0]["missing_properties"] == ["urn:label"]


def test_legacy_facade_requires_explicit_multi_source_bindings():
    from app.services.reporting.legacy_facade import require_single_source

    with pytest.raises(ReportingError, match="EXPLICIT_SOURCE_BINDINGS_REQUIRED"):
        require_single_source([{"source_slot_id": "a"}, {"source_slot_id": "b"}])


def test_rule_publication_checks_approved_claim_wording(db):
    from app.services.reporting.contract_registry import ContractRegistry

    registry = ContractRegistry(db)
    parameters = {
        "subject": {
            "type": {"kind": "entity", "class_iris": ["urn:Equipment"]},
            "semantic_ref": "urn:subject",
        },
        "evidence": {"type": {"kind": "boolean"}, "semantic_ref": "urn:evidence"},
    }
    claim = registry.create(
        kind="claim",
        family_id="claim-wording",
        revision_no=1,
        actor="analyst",
        definition={
            "category": "observed_fact",
            "text": "审核过的陈述",
            "description": "Synthetic claim",
            "applicable_scope": {},
            "precondition": {"op": "parameter", "parameter": "evidence"},
            "parameters": parameters,
            "subject_parameter": "subject",
            "evidence_parameters": ["evidence"],
        },
    )
    for decision in ["reviewed", "published"]:
        registry.decide(
            claim.id,
            decision,
            actor="analyst",
            reason="Synthetic test",
            expected_hash=claim.content_hash,
        )
    rule = registry.create(
        kind="rule",
        family_id="rule-wording",
        revision_no=1,
        actor="analyst",
        definition={
            "output_type": {
                "kind": "record",
                "fields": {"claim": {"type": {"kind": "string"}, "semantic_ref": "urn:claim"}},
            },
            "description": "Invalid synthetic wording",
            "applicable_scope": {},
            "parameters": parameters,
            "branches": [
                {
                    "branch_id": "yes",
                    "when": {"op": "literal", "value": True},
                    "fields": {
                        "claim": {
                            "value": {"op": "literal", "value": "偷偷修改的陈述"},
                            "category": "observed_fact",
                            "claim_ref": claim.id,
                        }
                    },
                }
            ],
        },
    )
    registry.decide(
        rule.id,
        "reviewed",
        actor="analyst",
        reason="Synthetic test",
        expected_hash=rule.content_hash,
    )
    with pytest.raises(ReportingError, match="CLAIM_TEXT_MISMATCH"):
        registry.decide(
            rule.id,
            "published",
            actor="analyst",
            reason="Synthetic test",
            expected_hash=rule.content_hash,
        )
    assert registry.load(rule.id, published=False)["status"] == "reviewed"


def test_range_compatibility_preserves_endpoint_units_and_open_boundaries():
    from app.services.reporting.template_compiler import compatible
    from app.services.reporting.template_v2 import TypeSpec

    actual = TypeSpec(
        kind="range",
        item_type=TypeSpec(kind="quantity", unit="kg", dimension="mass"),
        lower_inclusive=False,
        upper_inclusive=True,
    )
    assert compatible(actual, actual)
    assert not compatible(actual, actual.model_copy(update={"lower_inclusive": True}))
    assert not compatible(
        actual,
        actual.model_copy(
            update={"item_type": TypeSpec(kind="quantity", unit="L", dimension="volume")}
        ),
    )
