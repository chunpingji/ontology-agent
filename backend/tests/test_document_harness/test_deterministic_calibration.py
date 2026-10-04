"""Representation checks preserve semantic truth and exact original literals."""

from copy import deepcopy

import pytest
from rdflib import Graph

from app.services.document_harness.deterministic import calibrate
from app.services.document_harness.ontology import catalog_from_graph


def case(raw="500 g", target="kg", datatype="decimal"):
    graph = Graph().parse(
        data=f"""
        @prefix : <urn:calibration:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix i: <https://ontology.pharma-gmp.cn/slpra/integration/> .
        :Report a owl:Class .
        :amount a owl:DatatypeProperty; rdfs:domain :Report; rdfs:range xsd:{datatype}
          {f'; i:canonicalUnit "{target}"' if target else ""} .
    """,
        format="turtle",
    )
    catalog = catalog_from_graph(graph, "urn:calibration:Report")
    row = {
        "id": "p",
        "subject_id": "document",
        "alignment_class_iri": catalog.root_class_iri,
        "predicate_iri": "urn:calibration:amount",
        "value": raw,
        "source_value": raw,
        "value_evidence": [],
        "source_unit": None,
        "source_unit_evidence": [],
        "state": "accepted",
    }
    return row, catalog


@pytest.mark.parametrize(
    "raw,target,expected,factor,offset",
    [
        ("500 g", "kg", "0.5", "0.001", "0"),
        ("20 ℃", "K", "293.15", "1", "273.15"),
        ("300 K", "℃", "26.85", "1", "-273.15"),
    ],
)
def test_exact_conversion_preserves_original(raw, target, expected, factor, offset):
    row, catalog = case(raw, target)
    original = deepcopy(row)
    result = calibrate("properties", row, catalog)
    assert row == original and row["state"] == "accepted"
    assert result["literal"]["normalized_value"] == expected
    assert result["literal"]["conversion_record"]["factor"] == factor
    assert result["literal"]["conversion_record"]["offset"] == offset
    assert result["checks"]["shacl"]["status"] == "passed"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[100,500) g", ("range", "0.1", "0.5", False)),
        (">=500 g", ("comparison", None, None, True)),
    ],
)
def test_intervals_and_comparators_keep_form(raw, expected):
    row, catalog = case(raw)
    result = calibrate("properties", row, catalog)
    literal = result["literal"]
    assert (
        literal["kind"],
        literal["lower"],
        literal["upper"],
        literal["upper_inclusive"],
    ) == expected
    if literal["kind"] == "comparison":
        assert literal["normalized_value"] == "0.5" and literal["operator"] == "ge"


@pytest.mark.parametrize(
    "raw,target,status",
    [
        ("500", "kg", "incomplete"),
        ("500 xyz", "kg", "incomplete"),
        ("500 m", "kg", "invalid"),
        ("5-3 g", "kg", "invalid"),
        ("1 s", "min", "incomplete"),
    ],
)
def test_missing_incompatible_or_unsupported_input_never_becomes_value(raw, target, status):
    row, catalog = case(raw, target)
    result = calibrate("properties", row, catalog)
    assert result["literal"] is None
    assert any(c["status"] == status for c in result["checks"].values())
    assert row["state"] == "accepted"


def test_independent_unit_requires_semantic_source_and_inline_conflict_is_invalid():
    row, catalog = case("500")
    row["source_unit"] = "g"
    assert (
        calibrate("properties", row, catalog)["checks"]["unit"]["reason_code"]
        == "unit_source_not_proven"
    )
    row["source_unit_evidence"] = [{"text": "g"}]
    assert calibrate("properties", row, catalog)["literal"]["normalized_value"] == "0.5"
    row["value"] = "500 kg"
    assert calibrate("properties", row, catalog)["checks"]["unit"]["status"] == "invalid"


def test_unresolved_semantics_and_conflicting_unit_declarations_do_not_normalize():
    row, catalog = case()
    row["state"] = "unresolved"
    assert calibrate("properties", row, catalog)["literal"] is None
    row["state"] = "accepted"
    card = catalog.classes[catalog.root_class_iri]
    prop = card.properties[0].model_copy(
        update={"canonical_unit": None, "diagnostics": ("canonical_unit_conflict",)}
    )
    catalog = catalog.model_copy(
        update={"classes": {card.iri: card.model_copy(update={"properties": (prop,)})}}
    )
    assert calibrate("properties", row, catalog)["checks"]["unit"]["status"] == "incomplete"


def test_shacl_tool_failure_preserves_fact_but_blocks_literal(monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("tool unavailable")

    monkeypatch.setattr("pyshacl.validate", failure)
    row, catalog = case()
    result = calibrate("properties", row, catalog)
    assert result["checks"]["shacl"]["status"] == "error"
    assert row["state"] == "accepted" and result["literal"] is None


@pytest.mark.parametrize(
    "raw,component,value,target,expected",
    [
        ("100-500 g", "lower", "100", "kg", "0.1"),
        ("100 g to 500 g", "upper", "500", "kg", "0.5"),
        ("20 至 30 ℃", "upper", "30", "K", "303.15"),
        ("100-500 g", "upper", "500", None, "500"),
    ],
)
def test_endpoint_uses_complete_source_range_without_guessing_unit(
    raw,
    component,
    value,
    target,
    expected,
):
    row, catalog = case(raw, target)
    row.update(value=value, value_component=component)
    result = calibrate("properties", row, catalog)
    literal = result["literal"]
    assert literal["raw_value"] == value and row["source_value"] == raw
    assert literal["normalized_value"] == expected
    assert literal["conversion_record"]["endpoint_role"] == component
    assert literal["conversion_record"]["source_range"] == raw
    assert literal["canonical_unit"] == ("g" if target is None else target)
    assert result["checks"]["shacl"]["status"] == "passed"


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("500-100 g", "range lower endpoint exceeds upper"),
        ("100 g-500 kg", "source_unit_conflict"),
    ],
)
def test_endpoint_cannot_hide_invalid_complete_range(raw, reason):
    row, catalog = case(raw)
    row.update(value=raw.split()[0].split("-")[0], value_component="lower")
    result = calibrate("properties", row, catalog)
    assert result["literal"] is None and row["state"] == "accepted"
    assert reason in {c["reason_code"] for c in result["checks"].values()}
