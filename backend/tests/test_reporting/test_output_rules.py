from copy import deepcopy

import pytest

from app.services.reporting.claim_catalog import evaluate_rule
from app.services.reporting.condition_resolver import evaluate_condition
from app.services.reporting.input_resolver import typed_value
from app.services.reporting.template_compiler import compile_template
from app.services.reporting.template_v2 import DerivedBinding
from tests.test_reporting.test_output_contracts import contracts, ontology, template


def rule_fixture():
    binding = DerivedBinding.model_validate(
        {
            "kind": "derived",
            "binding_id": "result",
            "provider": "rule_result",
            "contract_ref": "rule",
            "parameters": {"verified": {"input_id": "verified"}},
        }
    )
    bundle = {
        "source_bundle_id": "frozen",
        "applicable_at": None,
        "contracts": {
            "rule": {
                "kind": "rule",
                "status": "published",
                "definition": {
                    "claims_reviewed": True,
                    "review_ref": "review",
                    "parameters": {"verified": {"type": {"kind": "boolean"}}},
                    "output_type": {
                        "kind": "list",
                        "item_identity": "entity_id",
                        "item_type": {
                            "kind": "record",
                            "fields": {
                                "post": {"type": {"kind": "string"}, "semantic_ref": "urn:decision"}
                            },
                        },
                    },
                    "branches": [
                        {
                            "branch_id": "b",
                            "when": {"op": "literal", "value": True},
                            "fields": {
                                "post": {
                                    "category": "risk_decision",
                                    "value": {"op": "literal", "value": "low"},
                                    "requires_implementation": True,
                                    "implementation_parameters": ["verified"],
                                }
                            },
                        }
                    ],
                },
            }
        },
    }
    return binding, bundle


@pytest.mark.parametrize(
    "actual,nature,expected",
    [
        (False, "observed_fact", "missing"),
        (None, "observed_fact", "missing"),
        (True, "planned_control", "missing"),
        (True, "observed_fact", "ready"),
    ],
)
def test_actual_control_requires_proven_true_observed_evidence(actual, nature, expected):
    binding, bundle = rule_fixture()
    value = typed_value(
        "verified",
        {"kind": "boolean"},
        actual,
        refs=["fact"],
        subject=["E"],
        scope="scope",
        derivation={"business_nature": nature},
    )
    value.applicable_scope = {"applicable_at": None}
    result = evaluate_rule(binding, bundle, {"verified": value}, "scope")
    assert result.items[0].fields["post"].state == expected


@pytest.mark.parametrize("actual", [False, None])
def test_false_unknown_branch_never_produces_low(actual):
    binding, bundle = rule_fixture()
    branch = bundle["contracts"]["rule"]["definition"]["branches"][0]
    branch["when"] = {"op": "parameter", "parameter": "verified"}
    value = typed_value("verified", {"kind": "boolean"}, actual)
    result = evaluate_rule(binding, bundle, {"verified": value}, "scope")
    assert result.items[0].fields["post"].value is None


def test_duplicate_rule_row_identity_is_a_conflict():
    binding, bundle = rule_fixture()
    definition = bundle["contracts"]["rule"]["definition"]
    definition["branches"].append(deepcopy(definition["branches"][0]))
    result = evaluate_rule(binding, bundle, {}, "scope")
    assert result.state == "conflict"


def test_condition_cannot_read_unmapped_input():
    data = {
        "source_bundle_id": "frozen",
        "applicable_at": None,
        "sources": {
            "doc": {
                "snapshot": {
                    "assertions": [
                        {
                            "assertion_id": "a",
                            "subject_iri": "E",
                            "object_iri": None,
                            "candidate": {
                                "assertion_status": "conditional",
                                "condition_provenance_indexes": [0],
                            },
                        }
                    ]
                }
            }
        },
        "contracts": {
            "cb": {
                "status": "published",
                "definition": {
                    "condition_ref": "condition",
                    "association_review_ref": "review",
                    "assertion_id": "a",
                    "subject_id": "E",
                    "applicable_at": None,
                    "parameters": {},
                    "business_nature": "observed_fact",
                    "polarity": "affirmed",
                },
            },
            "condition": {
                "status": "published",
                "definition": {
                    "parameters": {},
                    "expression": {"op": "input", "input_ref": {"input_id": "other"}},
                },
            },
        },
    }
    value = typed_value("other", {"kind": "boolean"}, True, subject=["unrelated"])
    result = evaluate_condition("cb", data, {"other": value}, "scope")
    assert not result["projection_eligible"]
    assert result["result"] == "UNKNOWN"


def test_defaulted_inputref_in_condition_is_part_of_cycle():
    value = template()
    value["definitions"]["bindings"]["equipment"]["scope"]["condition_refs"] = ["cb"]
    registered = contracts()
    registered.update(
        {
            "cb": {
                "kind": "condition_binding",
                "status": "published",
                "definition": {
                    "condition_ref": "condition",
                    "parameters": {"p": {"input_id": "rows", "field_path": ["code"]}},
                },
            },
            "condition": {
                "kind": "condition",
                "status": "published",
                "definition": {
                    "parameters": {"p": {"type": {"kind": "string"}}},
                    "expression": {"op": "parameter", "parameter": "p"},
                },
            },
        }
    )
    plan = compile_template(value, ontology(), registered)
    assert not plan["valid"]
    assert "DEPENDENCY_CYCLE" in {d["code"] for d in plan["diagnostics"]}
