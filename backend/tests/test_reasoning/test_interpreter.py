"""Interpreter core: restricted-vocabulary validation + three-valued OWA eval.

Locks in T006 (`validate_pattern`, FR-004/FR-014) and T007 (Kleene three-valued
evaluation with OWA "absent → UNKNOWN, never FALSE", FR-010/SC-006).
"""

from __future__ import annotations

import pytest

from app.services.reasoning.interpreter import (
    FALSE,
    TRUE,
    UNKNOWN,
    Facts,
    PatternError,
    Ternary,
    evaluate,
    t_and,
    t_or,
    validate_pattern,
)


# --------------------------------------------------------------------------- #
# T006 — restricted-mode vocabulary validation
# --------------------------------------------------------------------------- #
def test_validate_accepts_each_vocabulary_op():
    valid = [
        {"op": "some_values_from", "property": "hasToxicityProfile", "filler_class": "GenotoxicityProfile"},
        {"op": "class_membership", "property": "hasOEBClassification", "classes": ["OEB4", "OEB5"]},
        {"op": "datatype_facet", "property": "sensitizationLevel", "cmp": "gt", "value": 3},
        {"op": "boolean_has_value", "property": "hasBetaLactamRing", "value": True},
        {"op": "external_alignment", "property": "hasActiveIngredient", "alignment": "CHEBI_35610"},
        {"op": "class_present", "class": "PenicillinDrug"},
        {"op": "literal_eq", "key": "pathway", "value": "residue"},
        {"op": "literal_cmp", "key": "pde", "cmp": "lt", "value": 0.01},
    ]
    for node in valid:
        validate_pattern(node)  # must not raise
    validate_pattern({"op": "and", "operands": valid})
    validate_pattern({"op": "or", "operands": valid})


@pytest.mark.parametrize(
    "node",
    [
        {"op": "no_such_op", "property": "x"},
        {"op": "some_values_from", "property": "p"},  # missing filler_class
        {"op": "datatype_facet", "property": "p", "cmp": "between", "value": 3},  # bad cmp
        {"op": "class_membership", "property": "p", "classes": "OEB4"},  # classes not a list
        {"op": "and", "operands": []},  # empty operands
        {"op": "and", "operands": [{"op": "bad"}]},  # invalid child
        "not-a-dict",
    ],
)
def test_validate_rejects_malformed(node):
    with pytest.raises(PatternError):
        validate_pattern(node)


# --------------------------------------------------------------------------- #
# T007 — three-valued (Kleene) logic + OWA
# --------------------------------------------------------------------------- #
def test_ternary_forbids_bool_coercion():
    with pytest.raises(TypeError):
        bool(UNKNOWN)


def test_t_and_kleene():
    assert t_and([TRUE, TRUE]) is TRUE
    assert t_and([TRUE, FALSE]) is FALSE
    assert t_and([TRUE, UNKNOWN]) is UNKNOWN
    assert t_and([FALSE, UNKNOWN]) is FALSE  # any FALSE dominates
    assert t_and([UNKNOWN, UNKNOWN]) is UNKNOWN


def test_t_or_kleene():
    assert t_or([FALSE, FALSE]) is FALSE
    assert t_or([TRUE, FALSE]) is TRUE
    assert t_or([UNKNOWN, FALSE]) is UNKNOWN
    assert t_or([TRUE, UNKNOWN]) is TRUE  # any TRUE dominates
    assert t_or([UNKNOWN, UNKNOWN]) is UNKNOWN


def test_datatype_facet_true_false_unknown():
    node = {"op": "datatype_facet", "property": "sensitizationLevel", "cmp": "gt", "value": 3}
    assert evaluate(node, Facts(data_values={"sensitizationLevel": 4})) is TRUE
    assert evaluate(node, Facts(data_values={"sensitizationLevel": 2})) is FALSE
    # OWA: attribute absent → UNKNOWN, never FALSE
    assert evaluate(node, Facts(data_values={})) is UNKNOWN


def test_some_values_from_owa():
    node = {"op": "some_values_from", "property": "hasToxicityProfile", "filler_class": "GenotoxicityProfile"}
    assert evaluate(node, Facts(relations={"hasToxicityProfile": ["GenotoxicityProfile"]})) is TRUE
    # relation asserted but no match → UNKNOWN (open world cannot deny)
    assert evaluate(node, Facts(relations={"hasToxicityProfile": ["OtherProfile"]})) is UNKNOWN
    # relation absent → UNKNOWN
    assert evaluate(node, Facts(relations={})) is UNKNOWN


def test_boolean_has_value_owa():
    node = {"op": "boolean_has_value", "property": "hasBetaLactamRing", "value": True}
    assert evaluate(node, Facts(data_values={"hasBetaLactamRing": True})) is TRUE
    assert evaluate(node, Facts(data_values={"hasBetaLactamRing": False})) is FALSE
    assert evaluate(node, Facts(data_values={})) is UNKNOWN  # absent → UNKNOWN


def test_class_present_closed_over_drug_classes():
    node = {"op": "class_present", "class": "CytotoxicDrug"}
    assert evaluate(node, Facts(drug_classes=["CytotoxicDrug"])) is TRUE
    assert evaluate(node, Facts(drug_classes=["HormonalDrug"])) is FALSE
    assert evaluate(node, Facts(drug_classes=[])) is UNKNOWN


def test_and_or_compose_owa():
    a = {"op": "class_present", "class": "CytotoxicDrug"}
    b = {"op": "boolean_has_value", "property": "hasBetaLactamRing", "value": True}
    facts = Facts(drug_classes=["CytotoxicDrug"])  # a=TRUE, b=UNKNOWN (absent)
    assert evaluate({"op": "and", "operands": [a, b]}, facts) is UNKNOWN
    assert evaluate({"op": "or", "operands": [a, b]}, facts) is TRUE
