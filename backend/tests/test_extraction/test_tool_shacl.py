import pytest
from rdflib import RDF, RDFS, XSD, Graph, Literal, URIRef

from app.services.extraction.ontology_guided.contracts import SlotSpec
from app.services.extraction.tool_validation.shacl import (
    METRIC,
    PROFILE,
    build_metric_graph,
    claim_node,
    validate_graph,
)


@pytest.fixture
def schema():
    return SlotSpec(
        iri="urn:predicate:pde", label="PDE", datatype_iris=[str(XSD.decimal)],
        canonical_unit="mg/day",
    )


def graph(schema, **kwargs):
    return build_metric_graph(candidate_id="rat", raw="100", slot=schema, **kwargs)


def validate(data, schema, **kwargs):
    return validate_graph(
        data, slot=schema, expected_focus_nodes=kwargs.get("expected", [str(claim_node("rat"))]),
    )


@pytest.mark.parametrize("empty_expected", [False, True])
def test_empty_graph_keeps_native_conformance_but_cannot_pass(empty_expected, schema):
    result = validate(Graph(), schema, expected=[] if empty_expected else [str(claim_node("rat"))])
    assert result["evaluated"] is True
    assert result["conforms"] is True
    assert result["validation_status"] == "incomplete"
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["executed_shapes"] == []


def test_wrong_target_type_cannot_pass_vacuously(schema):
    data = graph(schema, value="100", unit="mg/day")
    data.remove((claim_node("rat"), RDF.type, METRIC.LiteralClaim))
    data.add((claim_node("rat"), RDF.type, URIRef("urn:wrong-type")))
    result = validate(data, schema)
    assert result["conforms"] is True
    assert result["coverage"]["missing_focus_nodes"] == [str(claim_node("rat"))]
    assert result["validation_status"] == "incomplete"


def test_subclass_targets_match_shacl_target_semantics(schema):
    data = graph(schema, value="100", unit="mg/day")
    data.remove((claim_node("rat"), RDF.type, METRIC.LiteralClaim))
    subclass = URIRef("urn:subclass")
    data.add((claim_node("rat"), RDF.type, subclass))
    data.add((subclass, RDFS.subClassOf, METRIC.LiteralClaim))
    result = validate(data, schema)
    assert result["conforms"] is True
    assert result["coverage"]["complete"] is True


def test_missing_required_representation_fields_are_incomplete_not_false_facts(schema):
    result = validate(graph(schema), schema)
    assert result["conforms"] is False
    assert result["validation_status"] == "incomplete"
    assert {row["resultPath"] for row in result["report"]} == {
        str(METRIC.value), str(METRIC.unit),
    }
    assert result["report_text"].startswith("Validation Report")


@pytest.mark.parametrize("failure", ["datatype", "unit", "predicate", "cardinality"])
def test_wrong_values_keep_native_report_details(schema, failure):
    data = graph(schema, value="100", unit="mg/day")
    node = claim_node("rat")
    if failure == "datatype":
        data.set((node, METRIC.value, Literal("one hundred", datatype=XSD.string)))
    elif failure == "unit":
        data.set((node, METRIC.unit, Literal("kg/day", datatype=XSD.string)))
    elif failure == "predicate":
        data.set((node, METRIC.predicate, URIRef("urn:predicate:maximumDailyDose")))
    else:
        data.add((node, METRIC.value, Literal("7.5", datatype=XSD.decimal)))
    result = validate(data, schema)
    assert result["validation_status"] == "failed"
    assert result["conforms"] is False
    row = result["report"][0]
    assert row["focusNode"] == str(node)
    assert row["sourceShape"].startswith(str(PROFILE))
    assert row["sourceConstraintComponent"]
    assert row["resultSeverity"]
    assert row["message"]


def test_representation_profile_does_not_silence_large_business_values_or_mutate_graph(schema):
    data = graph(schema, value="1000000", unit="mg/day")
    before = set(data)
    result = validate(data, schema)
    assert result["conforms"] is True
    assert result["validation_status"] == "passed"
    assert set(data) == before
    assert len(result["coverage"]["executed_shapes"]) == 5


def test_unexpected_extra_candidates_are_visible_as_coverage_mismatch(schema):
    data = graph(schema, value="100", unit="mg/day")
    data += build_metric_graph(
        candidate_id="dog", raw="7.5", slot=schema, value="7.5", unit="mg/day",
    )
    result = validate(data, schema)
    assert result["conforms"] is True
    assert result["validation_status"] == "incomplete"
    assert result["coverage"]["unexpected_focus_nodes"] == [str(claim_node("dog"))]


def test_slot_configuration_failure_is_separate_from_nonconforming_data(schema):
    invalid = schema.model_copy(update={"constraint_status": "constraint_unresolved"})
    result = validate(graph(schema, value="100", unit="mg/day"), invalid)
    assert result["execution_status"] == "failed"
    assert result["evaluated"] is False
    assert result["conforms"] is None
    assert result["validation_status"] == "incomplete"
