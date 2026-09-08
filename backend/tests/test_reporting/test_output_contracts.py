"""Adversarial V2 compiler contracts. These examples are not approved business data."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.reporting.template_v2 import TemplateV2


def ontology():
    return {
        "urn:Report": {
            "parents": [],
            "properties": [],
            "relationships": [
                {"iri": "urn:uses", "range": ["urn:Equipment"]},
            ],
        },
        "urn:Equipment": {
            "parents": [],
            "relationships": [
                {"iri": "urn:material", "range": ["urn:Material"]},
            ],
            "properties": [
                {
                    "iri": "urn:code",
                    "datatype": "http://www.w3.org/2001/XMLSchema#string",
                    "max_count": 1,
                },
                {
                    "iri": "urn:spec",
                    "datatype": "http://www.w3.org/2001/XMLSchema#string",
                    "max_count": 1,
                },
            ],
        },
        "urn:Material": {
            "parents": [],
            "relationships": [],
            "properties": [
                {"iri": "urn:label", "datatype": "http://www.w3.org/2001/XMLSchema#string"},
            ],
        },
    }


def contracts():
    return {
        "style-1": {
            "kind": "style",
            "status": "published",
            "revision_no": 1,
            "definition": {"font": "Arial"},
        },
        "policy-1": {
            "kind": "policy",
            "status": "published",
            "revision_no": 1,
            "definition": {"require_review": True, "signature_slots": []},
        },
    }


def template():
    return {
        "schema_version": 2,
        "template_family_id": "family",
        "template_revision_id": "revision",
        "revision_no": 1,
        "ontology_release_ref": "ontology-1",
        "style_profile_ref": "style-1",
        "publication_policy_ref": "policy-1",
        "source_slots": [{"source_slot_id": "doc", "kind": "document", "class_iri": "urn:Report"}],
        "definitions": {
            "bindings": {
                "equipment": {
                    "binding_id": "equipment",
                    "kind": "facts",
                    "contract_ref": {
                        "kind": "ontology",
                        "release_ref": "ontology-1",
                        "root_class_iri": "urn:Report",
                        "result_class_iri": "urn:Equipment",
                    },
                    "scope": {
                        "source_slot": "doc",
                        "predicate_path": [{"predicate_iri": "urn:uses"}],
                    },
                }
            },
            "inputs": {
                "rows": {
                    "input_id": "rows",
                    "name": "equipment_rows",
                    "label": "设备",
                    "binding_ref": "equipment",
                    "required": True,
                    "projection": {
                        "kind": "records",
                        "fields": {
                            "code": {
                                "value": {"kind": "property", "property_iri": "urn:code"},
                                "required": True,
                            },
                            "spec": {"value": {"kind": "property", "property_iri": "urn:spec"}},
                        },
                    },
                }
            },
        },
        "sections": [
            {
                "section_id": "sec",
                "groups": [
                    {
                        "group_id": "group",
                        "units": [
                            {
                                "output_id": "table",
                                "title": "设备表",
                                "bindings": [{"binding_ref": "equipment"}],
                                "inputs": [{"input_ref": "rows", "alias": "equipment"}],
                                "render": {
                                    "kind": "table",
                                    "rows": {"kind": "input_ref", "input_id": "rows"},
                                    "columns": [
                                        {"column_id": "col", "title": "编号", "field_ref": "code"}
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def compile_example(value=None, schema=None):
    from app.services.reporting.template_compiler import compile_template

    return compile_template(value or template(), schema or ontology(), contracts())


def test_infers_record_list_and_tracks_consumed_field():
    plan = compile_example()
    assert plan["valid"], plan["diagnostics"]
    assert plan["input_types"]["rows"]["kind"] == "list"
    assert plan["input_types"]["rows"]["item_type"]["fields"]["code"]["type"]["kind"] == "string"
    assert any(u["field_path"] == ["code"] for u in plan["uses"]["table"])


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda t: t["definitions"]["inputs"]["rows"]["projection"]["fields"]["code"][
                "value"
            ].update(property_iri="urn:material"),
            "ONTOLOGY_PATH_TYPE_MISMATCH",
        ),
        (
            lambda t: t["definitions"]["inputs"]["rows"]["projection"]["fields"]["code"][
                "value"
            ].update(property_iri="urn:unknown"),
            "UNKNOWN_ONTOLOGY_REFERENCE",
        ),
        (
            lambda t: t["sections"][0]["groups"][0]["units"][0]["render"]["rows"].update(
                input_id="other-unit-secret"
            ),
            "UNRESOLVED_INPUT_REFERENCE",
        ),
        (
            lambda t: t["sections"][0]["groups"][0]["units"][0].update(bindings=[]),
            "UNDECLARED_BINDING_DEPENDENCY",
        ),
        (
            lambda t: t["definitions"]["bindings"]["equipment"].update(
                dependencies=[{"kind": "input", "ref": "rows"}]
            ),
            "DEPENDENCY_CYCLE",
        ),
    ],
)
def test_rejects_invalid_semantics(mutation, code):
    value = template()
    mutation(value)
    plan = compile_example(value)
    assert not plan["valid"]
    assert code in {d["code"] for d in plan["diagnostics"]}


def test_unknown_keys_cannot_be_discarded():
    value = template()
    value["definitions"]["bindings"]["equipment"]["finder"] = "equipment_by_name"
    with pytest.raises(ValidationError):
        TemplateV2.model_validate(value)


def test_renaming_and_moving_preserve_selection_semantics():
    value = template()
    old = compile_example(value)
    value["definitions"]["inputs"]["rows"]["label"] = "renamed"
    value["sections"][0]["groups"][0]["units"][0]["title"] = "another title"
    new = compile_example(value)
    assert new["valid"]
    assert new["input_types"] == old["input_types"]
    assert new["schema_hash"] != old["schema_hash"]


def test_wrong_endpoint_and_inverse_path_are_checked():
    value = template()
    value["definitions"]["bindings"]["equipment"]["scope"]["predicate_path"][0]["direction"] = (
        "inverse"
    )
    assert not compile_example(value)["valid"]


def test_unused_definitions_do_not_expand_runtime():
    value = template()
    extra = deepcopy(value["definitions"]["inputs"]["rows"])
    extra.update(input_id="unused", name="unused", binding_ref="unresolved")
    value["definitions"]["inputs"]["unused"] = extra
    plan = compile_example(value)
    assert plan["valid"], plan["diagnostics"]
    assert "input:unused" not in plan["node_order"]
    assert any(d["code"] == "UNUSED_DEFINITION" for d in plan["diagnostics"])
