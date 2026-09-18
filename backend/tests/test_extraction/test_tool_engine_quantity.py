"""Controller checks preserve quantity meaning, units and non-vacuous SHACL coverage."""
from __future__ import annotations

import pytest
from rdflib import RDF, XSD, Graph, Literal, URIRef

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.ontology_guided.claim_protocol import (
    PropertyProposal,
    QuantityPolicy,
    VerificationTargetSpec,
    VerifiedTarget,
    claim_content_hash,
)
from app.services.extraction.ontology_guided.contracts import (
    SemanticDecision,
    SlotSpec,
    TraversalScope,
    VersionedRef,
)
from app.services.extraction.ontology_guided.tool_contracts import BindingData
from app.services.extraction.tool_validation.metric import normalize_metric
from app.services.extraction.tool_validation.shacl import (
    METRIC,
    QUANTITY_PROFILE_VERSION,
    build_quantity_graph,
    claim_node,
    validate_graph,
    validate_metric_result,
)


def metric_case(raw, *, unit="kg", datatype="decimal", forms=None, endpoint=None,
                requirement="physical", allowed=None, source=None):
    ref = VersionedRef(id="claim", revision=1)
    anchor = EvidenceAnchor(document_hash="a" * 64, structure_hash="b" * 64,
                            parser_version="v1", evidence_id="evidence", section_node_id="s",
                            block_id="block", span_start=0, span_end=len(raw))
    slot = SlotSpec(iri="urn:quantity", label="Quantity", datatype_iris=[str(XSD[datatype])],
                    canonical_unit=unit)
    policy = QuantityPolicy(predicate_iri=slot.iri, allowed_forms=forms or ["scalar"],
                            endpoint_role=endpoint, allowed_target_units=allowed or [],
                            unit_requirement=requirement, declaration_ref="ontology:quantity")
    quote = {"evidence_id": anchor.evidence_id, "text": raw, "context_text": None}
    payload = PropertyProposal(
        local_id="p1", subject_id="s1", predicate_iri=slot.iri, value_quote=quote,
        field_support=[quote], unit_support=[quote], bridge_kind="owned_field_group",
        bridge_ref_ids=[], qualifiers={"polarity": "affirmed", "modality": "asserted",
                                      "condition_support": [], "scope_qualifiers": []},
    )
    scope = TraversalScope.create()
    target = VerificationTargetSpec(
        target_id="target", target_kind="property", claim_ref=ref, payload=payload,
        content_hash=claim_content_hash("property", payload, scope, []),
        required_facets=["field_role", "value", "unit"], scope=scope, dependency_refs=[],
    )
    decisions = [SemanticDecision(
        decision_id="decision:" + facet, target_id=target.target_id, check_kind=facet,
        verdict="supported", reason_code="supported", reason="Source supports this facet",
        support_refs=[anchor], verifier_version="test", attempt_id="verification",
    ) for facet in target.required_facets]
    return {
        "raw": raw, "slot": slot, "quantity_policy": policy, "source_unit": source,
        "target_unit": None, "candidate_ref": ref, "target": target,
        "binding": BindingData(claim_ref=ref, validation_status="passed", source_unit=source,
                               resolved_role_refs=[anchor], issues=[]),
        "verified": VerifiedTarget(target_id=target.target_id, content_hash=target.content_hash,
                                   decisions=decisions, missing_facets=[], validation_issues=[]),
    }


def shacl(metric, case):
    return validate_metric_result(
        metric, slot=case["slot"], quantity_policy=case["quantity_policy"],
        candidate_ref=case["candidate_ref"], shape_profile_id=QUANTITY_PROFILE_VERSION,
        raw=case["raw"],
    )


@pytest.mark.parametrize("raw,lower,upper,closed", [
    ("[1000, 2000) g", "1", "2", (True, False)),
    ("(1000, 2000] g", "1", "2", (False, True)),
    ("[1000, 1000] g", "1", "1", (True, True)),
    ("1000–2000 g", "1", "2", (True, True)),
])
def test_full_interval_retains_both_endpoints_and_openness(raw, lower, upper, closed):
    case = metric_case(raw, forms=["interval"])
    metric = normalize_metric(**case)
    assert metric.validation_status == "passed"
    quantity = metric.quantity
    assert (quantity.lower, quantity.upper) == (lower, upper)
    assert (quantity.lower_inclusive, quantity.upper_inclusive) == closed
    assert quantity.raw == raw and quantity.source_unit == "g"
    assert quantity.target_unit == "kg" and quantity.conversion_record["factor"] == "0.001"
    assert metric.normalized_literal is None
    result = shacl(metric, case)
    assert result.conforms is True and result.validation_status == "passed"
    assert result.coverage.complete and result.coverage.expected == result.coverage.actual


def test_endpoint_policy_keeps_interval_source_and_selects_only_declared_endpoint():
    case = metric_case("[1000, 2000) g", forms=["interval"], endpoint="upper")
    result = normalize_metric(**case)
    assert result.normalized_literal == "2"
    assert result.quantity.raw == "[1000, 2000) g" and result.quantity.form == "interval"
    assert result.quantity.endpoint_role == "upper" and result.quantity.upper_inclusive is False
    assert shacl(result, case).validation_status == "passed"


@pytest.mark.parametrize("raw,form,value,included", [
    (">0 g", "lower_bound", "0", False), ("≥1000 g", "lower_bound", "1", True),
    ("<1000 g", "upper_bound", "1", False), ("≤1000 g", "upper_bound", "1", True),
])
def test_bound_is_not_reinterpreted_as_scalar(raw, form, value, included):
    case = metric_case(raw, forms=[form])
    result = normalize_metric(**case)
    assert result.validation_status == "passed" and result.normalized_literal is None
    quantity = result.quantity
    end = "lower" if form == "lower_bound" else "upper"
    assert getattr(quantity, end) == value and getattr(quantity, end + "_inclusive") == included
    assert shacl(result, case).validation_status == "passed"


@pytest.mark.parametrize("raw,unit,expected,offset", [
    ("0 ℃", "K", "273.15", "273.15"), ("273.15 K", "°C", "0", "-273.15"),
    ("0.000000000000000000000000000123 g", "mg",
     "0.000000000000000000000000123", "0"),
])
def test_exact_scale_offset_and_decimal_precision(raw, unit, expected, offset):
    case = metric_case(raw, unit=unit)
    result = normalize_metric(**case)
    assert result.normalized_literal == expected
    assert result.quantity.conversion_record["offset"] == offset
    assert shacl(result, case).validation_status == "passed"


def test_no_declared_target_preserves_unit_even_when_other_units_are_allowed():
    case = metric_case("2 毫克", unit=None, allowed=["g"], requirement="not_declared")
    result = normalize_metric(**case)
    assert result.normalized_literal == "2"
    assert result.quantity.source_unit == "毫克" and result.quantity.target_unit == "mg"
    assert result.quantity.conversion_record["mapping_kind"] == "alias"
    assert shacl(result, case).validation_status == "passed"
    case["target_unit"] = "g"
    converted = normalize_metric(**case)
    assert converted.normalized_literal == "0.002"
    assert shacl(converted, case).validation_status == "passed"


def test_cited_source_symbol_survives_unicode_alias_normalization():
    case = metric_case("0 ℃", unit="°C", source="℃")
    metric = normalize_metric(**case)
    assert metric.quantity.source_unit == "℃"
    assert metric.quantity.target_unit == "°C"
    assert metric.quantity.conversion_record["mapping_kind"] == "alias"
    assert shacl(metric, case).validation_status == "passed"


@pytest.mark.parametrize("raw,kwargs,code,status", [
    ("1 s", {"unit": "min"}, "unit_conversion_not_exact", "incomplete"),
    ("2 IU", {}, "unit_unknown", "incomplete"),
    ("2", {}, "unit_source_missing", "incomplete"),
    ("2 mL", {}, "unit_missing_or_incompatible", "failed"),
    ("[1,2] kg", {}, "quantity_form_not_allowed", "incomplete"),
    ("≈2 kg", {}, "quantity_approximate", "incomplete"),
    ("500 g", {"datatype": "integer"}, "datatype_mismatch", "failed"),
    ("-1 kg", {"datatype": "nonNegativeInteger"}, "datatype_mismatch", "failed"),
    ("0 kg", {"datatype": "positiveInteger"}, "datatype_mismatch", "failed"),
    ("2147483648 kg", {"datatype": "int"}, "datatype_mismatch", "failed"),
    ("2 kg", {"unit": None, "requirement": "count"},
     "unit_missing_or_incompatible", "failed"),
    ("2 kg", {"unit": None, "requirement": "dimensionless"},
     "unit_missing_or_incompatible", "failed"),
])
def test_unresolved_or_invalid_quantities_never_emit_trusted_values(raw, kwargs, code, status):
    result = normalize_metric(**metric_case(raw, **kwargs))
    assert result.validation_status == status
    assert result.normalized_literal is None and result.quantity is None
    assert code in [issue.code for issue in result.issues]


@pytest.mark.parametrize("mutation", ["claim", "hash", "facet", "rejected", "binding", "unit"])
def test_references_full_semantics_and_binding_are_required(mutation):
    case = metric_case("2 kg")
    if mutation == "claim":
        case["candidate_ref"] = VersionedRef(id="other", revision=1)
    elif mutation == "hash":
        case["verified"].content_hash = "other"
    elif mutation == "facet":
        case["verified"].decisions.pop()
    elif mutation == "rejected":
        case["verified"].decisions[0].verdict = "unsupported"
    elif mutation == "binding":
        case["binding"].validation_status = "incomplete"
    else:
        case["source_unit"] = "kg"
    result = normalize_metric(**case)
    assert result.validation_status != "passed"
    assert result.normalized_literal is None and result.quantity is None


@pytest.mark.parametrize("datatype,raw,expected", [
    ("integer", "2", "2"), ("decimal", "1e400", "1" + "0" * 400),
    ("boolean", "是", True), ("gYearMonth", "2026年6月", "2026-06"),
])
def test_literal_return_type_does_not_float_round_or_coerce_bool(datatype, raw, expected):
    case = metric_case(raw, unit=None, datatype=datatype, requirement="count")
    result = normalize_metric(**case)
    assert result.normalized_literal == expected
    assert type(result.normalized_literal) is type(expected)


def test_explicit_target_must_be_allowed_and_normalization_does_not_run_shacl(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("normalize_metric must not execute SHACL")
    monkeypatch.setattr("pyshacl.validate", forbidden)
    case = metric_case("2 kg")
    assert normalize_metric(**case).validation_status == "passed"
    case["target_unit"] = "g"
    result = normalize_metric(**case)
    assert result.issues[0].code == "target_unit_not_allowed"


@pytest.mark.parametrize("mutation", ["empty", "wrong_type", "extra", "empty_expected"])
def test_quantity_shacl_requires_exact_nonempty_focus(mutation):
    case = metric_case("2 kg")
    metric = normalize_metric(**case)
    data = build_quantity_graph(candidate_id="claim", slot=case["slot"], quantity=metric.quantity)
    expected = [str(claim_node("claim"))]
    if mutation == "empty":
        data = Graph()
    elif mutation == "wrong_type":
        data.remove((claim_node("claim"), RDF.type, METRIC.QuantityClaim))
        data.add((claim_node("claim"), RDF.type, URIRef("urn:wrong")))
    elif mutation == "extra":
        data += build_quantity_graph(candidate_id="other", slot=case["slot"],
                                     quantity=metric.quantity)
    else:
        expected = []
    result = validate_graph(data, slot=case["slot"], expected_focus_nodes=expected,
                            quantity_policy=case["quantity_policy"], source_unit="kg")
    assert result["validation_status"] == "incomplete" and not result["coverage"]["complete"]


@pytest.mark.parametrize("mutation", ["reversed", "open_singleton", "unit", "value_type"])
def test_shacl_independently_rejects_malformed_quantity_representation(mutation):
    case = metric_case("[1,2] kg", forms=["interval"])
    metric = normalize_metric(**case)
    data = build_quantity_graph(candidate_id="claim", slot=case["slot"], quantity=metric.quantity)
    node = claim_node("claim")
    if mutation == "reversed":
        data.set((node, METRIC["lower"], Literal("3", datatype=XSD.decimal)))
    elif mutation == "open_singleton":
        data.set((node, METRIC["lower"], Literal("2", datatype=XSD.decimal)))
        data.set((node, METRIC.lowerInclusive, Literal(False)))
    elif mutation == "unit":
        data.set((node, METRIC.unit, Literal("mL", datatype=XSD.string)))
    else:
        data.set((node, METRIC["lower"], Literal("one", datatype=XSD.string)))
    result = validate_graph(data, slot=case["slot"], expected_focus_nodes=[str(node)],
                            quantity_policy=case["quantity_policy"], source_unit="kg")
    assert result["conforms"] is False and result["validation_status"] == "failed"
    assert result["report"] and result["coverage"]["complete"]


def test_shacl_result_is_bound_to_this_claim_and_fixed_profile():
    case = metric_case("2 kg")
    metric = normalize_metric(**case)
    with pytest.raises(ValueError, match="shacl_claim_mismatch"):
        validate_metric_result(metric, slot=case["slot"], quantity_policy=case["quantity_policy"],
                               candidate_ref=VersionedRef(id="other", revision=1),
                               shape_profile_id=QUANTITY_PROFILE_VERSION, raw=case["raw"])
    with pytest.raises(ValueError, match="shacl_profile_mismatch"):
        validate_metric_result(metric, slot=case["slot"], quantity_policy=case["quantity_policy"],
                               candidate_ref=case["candidate_ref"], shape_profile_id="custom",
                               raw=case["raw"])


def test_shacl_cannot_run_after_failed_normalization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("missing source unit must prevent SHACL graph construction")
    monkeypatch.setattr("pyshacl.validate", forbidden)
    case = metric_case("2")
    metric = normalize_metric(**case)
    result = shacl(metric, case)
    assert result.profile == QUANTITY_PROFILE_VERSION
    assert not result.evaluated and result.conforms is None
    assert result.validation_status == "incomplete"


def test_shacl_technical_failure_is_incomplete_without_conformance(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("validator unavailable")
    monkeypatch.setattr("pyshacl.validate", unavailable)
    case = metric_case("2 kg")
    result = shacl(normalize_metric(**case), case)
    assert not result.evaluated and result.conforms is None
    assert result.validation_status == "incomplete"
