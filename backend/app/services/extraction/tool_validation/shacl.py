"""Fixed, local SHACL representation checks, separate from factual correctness.

The profile describes one submitted literal claim. Its required fields are a
tool representation contract, not inferred ontology mandatory properties or
business acceptance limits. Neither caller-provided shapes nor rules run here.
"""

from __future__ import annotations

from urllib.parse import quote

from rdflib import RDF, RDFS, SH, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from app.services.extraction.literal_normalizer import canonical_unit
from app.services.extraction.ontology_guided.claim_protocol import QuantityPolicy, QuantityValue
from app.services.extraction.ontology_guided.contracts import SlotSpec, VersionedRef
from app.services.extraction.ontology_guided.tool_contracts import (
    FocusCoverage,
    MetricData,
    ShaclData,
    ShaclIssue,
)
from app.services.extraction.ontology_guided.value_constraints import NUMERIC_DATATYPES

PROFILE_VERSION = "metric-representation-v1"
QUANTITY_PROFILE_VERSION = "quantity-representation-v2"
METRIC = Namespace("urn:ontology-agent:metric:")
PROFILE = Namespace(f"urn:ontology-agent:shapes:{PROFILE_VERSION}:")
QUANTITY_PROFILE = Namespace(f"urn:ontology-agent:shapes:{QUANTITY_PROFILE_VERSION}:")
VALIDATION_OPTIONS = {
    "inference": "none", "advanced": False, "js": False, "inplace": False,
    "do_owl_imports": False, "meta_shacl": False, "abort_on_first": False,
    "allow_infos": False, "allow_warnings": False,
}


def claim_node(candidate_id: str) -> URIRef:
    return URIRef(f"urn:ontology-agent:claim:{quote(candidate_id, safe='')}")


def build_metric_graph(
    *, candidate_id: str, raw: str, slot: SlotSpec, value: object = None,
    unit: str | None = None,
) -> Graph:
    """Build an ephemeral representation; never add a missing value or unit."""
    graph = Graph()
    node = claim_node(candidate_id)
    graph.add((node, RDF.type, METRIC.LiteralClaim))
    graph.add((node, METRIC.raw, Literal(raw, datatype=XSD.string)))
    graph.add((node, METRIC.predicate, URIRef(slot.iri)))
    if value is not None and len(set(slot.datatype_iris)) == 1:
        graph.add((node, METRIC.value, Literal(value, datatype=URIRef(slot.datatype_iris[0]))))
    if unit is not None:
        graph.add((node, METRIC.unit, Literal(unit, datatype=XSD.string)))
    return graph


def build_quantity_graph(*, candidate_id: str, slot: SlotSpec, quantity: QuantityValue) -> Graph:
    """One temporary quantity claim; no graph registry or persisted graph identity."""
    graph = Graph()
    node = claim_node(candidate_id)
    graph.add((node, RDF.type, METRIC.QuantityClaim))
    graph.add((node, METRIC.predicate, URIRef(slot.iri)))
    fields = {
        "raw": (quantity.raw, XSD.string), "form": (quantity.form, XSD.string),
        "sourceUnit": (quantity.source_unit, XSD.string),
        "unit": (quantity.target_unit, XSD.string),
        "dimension": (quantity.dimension, XSD.string),
        "scalar": (quantity.scalar, URIRef(quantity.datatype_iri)),
        "lower": (quantity.lower, URIRef(quantity.datatype_iri)),
        "upper": (quantity.upper, URIRef(quantity.datatype_iri)),
        "lowerInclusive": (quantity.lower_inclusive, XSD.boolean),
        "upperInclusive": (quantity.upper_inclusive, XSD.boolean),
        "comparator": (quantity.comparator, XSD.string),
        "endpointRole": (quantity.endpoint_role, XSD.string),
    }
    for name, (value, datatype) in fields.items():
        if value is not None:
            graph.add((node, METRIC[name], Literal(value, datatype=datatype)))
    return graph


def _quantity_profile(slot: SlotSpec, policy: QuantityPolicy, source_unit: str | None) -> Graph:
    """Fixed SHACL Core representation rules, parameterized only by frozen declarations."""
    if (slot.constraint_status != "resolved" or len(set(slot.datatype_iris)) != 1
            or policy.predicate_iri != slot.iri or not policy.allowed_forms):
        raise ValueError("constraint_unresolved")
    graph = Graph()
    root = QUANTITY_PROFILE.Claim
    graph.add((root, RDF.type, SH.NodeShape))
    graph.add((root, SH.targetClass, METRIC.QuantityClaim))

    def field(parent, name, datatype, *, required=True, prohibited=False, fixed=None, choices=None):
        shape = URIRef(str(parent) + "/" + name)
        graph.add((parent, SH.property, shape))
        graph.add((shape, RDF.type, SH.PropertyShape))
        graph.add((shape, SH.path, METRIC[name]))
        graph.add((shape, SH.maxCount, Literal(0 if prohibited else 1)))
        if required and not prohibited:
            graph.add((shape, SH.minCount, Literal(1)))
        if datatype is not None:
            graph.add((shape, SH.datatype, datatype))
        if fixed is not None:
            graph.add((shape, SH.hasValue, fixed))
        if choices is not None:
            head = BNode()
            Collection(graph, head, choices)
            graph.add((shape, SH["in"], head))
        return shape

    field(root, "raw", XSD.string)
    predicate = field(root, "predicate", None, fixed=URIRef(slot.iri))
    graph.add((predicate, SH.nodeKind, SH.IRI))
    field(root, "form", XSD.string,
          choices=[Literal(v, datatype=XSD.string) for v in policy.allowed_forms])
    physical = policy.unit_requirement == "physical"
    units = {canonical_unit(unit) for unit in policy.allowed_target_units}
    units.update(canonical_unit(unit) for unit in [slot.canonical_unit, source_unit] if unit)
    field(root, "unit", XSD.string, required=physical,
          prohibited=policy.unit_requirement == "count",
          choices=[Literal(unit, datatype=XSD.string) for unit in sorted(units)] if units else None)
    field(root, "sourceUnit", XSD.string, required=physical,
          prohibited=policy.unit_requirement == "count")
    field(root, "dimension", XSD.string, required=physical,
          prohibited=policy.unit_requirement == "count",
          choices=[Literal("ratio", datatype=XSD.string)]
          if policy.unit_requirement == "dimensionless" else None)
    numeric_type = URIRef(slot.datatype_iris[0])
    forms = []
    for form in policy.allowed_forms:
        branch = QUANTITY_PROFILE[form]
        forms.append(branch)
        graph.add((branch, RDF.type, SH.NodeShape))
        field(branch, "form", XSD.string, fixed=Literal(form, datatype=XSD.string))
        required_numbers = {"scalar": {"scalar"}, "interval": {"lower", "upper"},
                            "lower_bound": {"lower"}, "upper_bound": {"upper"}}[form]
        for name in ("scalar", "lower", "upper"):
            shape = field(branch, name, numeric_type, prohibited=name not in required_numbers)
            if name == "lower" and form == "interval":
                graph.add((shape, SH.lessThanOrEquals, METRIC["upper"]))
        for name, endpoint in (("lowerInclusive", "lower"), ("upperInclusive", "upper")):
            field(branch, name, XSD.boolean, prohibited=endpoint not in required_numbers)
        field(branch, "endpointRole", XSD.string,
              prohibited=form != "interval" or policy.endpoint_role is None,
              fixed=(Literal(policy.endpoint_role, datatype=XSD.string) if form == "interval"
                     and policy.endpoint_role is not None else None))
        comparisons = ("gt", "ge") if form == "lower_bound" else ("lt", "le")
        field(branch, "comparator", XSD.string, prohibited=form in {"scalar", "interval"},
              choices=[Literal(v, datatype=XSD.string) for v in comparisons]
              if form.endswith("bound") else None)
        if form.endswith("bound"):
            closed, opened = ("ge", "gt") if form == "lower_bound" else ("le", "lt")
            branches = []
            for included, comparator in ((True, closed), (False, opened)):
                option = URIRef(str(branch) + "/" + comparator)
                branches.append(option)
                field(option, "comparator", XSD.string,
                      fixed=Literal(comparator, datatype=XSD.string))
                field(option, "lowerInclusive" if form == "lower_bound" else "upperInclusive",
                      XSD.boolean, fixed=Literal(included))
            head = BNode()
            Collection(graph, head, branches)
            graph.add((branch, SH["or"], head))
        elif form == "interval":
            # A zero-length interval is valid only when both ends are included.
            increasing = URIRef(str(branch) + "/increasing")
            closed = URIRef(str(branch) + "/closed")
            lower = field(increasing, "lower", numeric_type)
            graph.add((lower, SH.lessThan, METRIC["upper"]))
            field(closed, "lowerInclusive", XSD.boolean, fixed=Literal(True))
            field(closed, "upperInclusive", XSD.boolean, fixed=Literal(True))
            head = BNode()
            Collection(graph, head, [increasing, closed])
            graph.add((branch, SH["or"], head))
    head = BNode()
    Collection(graph, head, forms)
    graph.add((root, SH["or"], head))
    return graph


def _profile(slot: SlotSpec) -> Graph:
    if slot.constraint_status != "resolved" or len(set(slot.datatype_iris)) != 1:
        raise ValueError("constraint_unresolved")
    graph = Graph()
    graph.add((PROFILE.Claim, RDF.type, SH.NodeShape))
    graph.add((PROFILE.Claim, SH.targetClass, METRIC.LiteralClaim))
    fields = [
        ("Raw", METRIC.raw, XSD.string, None),
        ("Predicate", METRIC.predicate, None, URIRef(slot.iri)),
        ("Value", METRIC.value, URIRef(slot.datatype_iris[0]), None),
    ]
    if slot.canonical_unit:
        fields.append((
            "Unit", METRIC.unit, XSD.string,
            Literal(canonical_unit(slot.canonical_unit), datatype=XSD.string),
        ))
    for name, path, datatype, fixed in fields:
        shape = PROFILE[name]
        graph.add((PROFILE.Claim, SH.property, shape))
        graph.add((shape, RDF.type, SH.PropertyShape))
        graph.add((shape, SH.path, path))
        graph.add((shape, SH.minCount, Literal(1)))
        graph.add((shape, SH.maxCount, Literal(1)))
        if datatype is not None:
            graph.add((shape, SH.datatype, datatype))
        if path == METRIC.predicate:
            graph.add((shape, SH.nodeKind, SH.IRI))
        if fixed is not None:
            graph.add((shape, SH.hasValue, fixed))
    return graph


def unevaluated_shacl(reason: str) -> dict:
    return {
        "profile": PROFILE_VERSION,
        "options": dict(VALIDATION_OPTIONS),
        "execution_status": "not_run",
        "validation_status": "incomplete",
        "evaluated": False,
        "conforms": None,
        "report": [],
        "report_text": None,
        "coverage": {
            "expected_focus_nodes": [], "actual_focus_nodes": [],
            "executed_shapes": [], "missing_focus_nodes": [], "complete": False,
        },
        "issues": [reason],
    }


def _report_rows(report_graph: Graph) -> list[dict]:
    fields = (
        "focusNode", "resultPath", "value", "sourceShape",
        "sourceConstraintComponent", "resultSeverity",
    )
    rows = []
    for node in report_graph.subjects(RDF.type, SH.ValidationResult):
        row = {
            name: str(value) if (value := report_graph.value(node, SH[name])) is not None else None
            for name in fields
        }
        row["message"] = sorted(str(message) for message in report_graph.objects(
            node, SH.resultMessage,
        ))
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(str(row[key]) for key in fields))


def validate_graph(
    data_graph: Graph, *, slot: SlotSpec, expected_focus_nodes: list[str],
    quantity_policy: QuantityPolicy | None = None, source_unit: str | None = None,
) -> dict:
    """Validate against the fixed profile and independently account for coverage.

    Native ``conforms`` is retained even if the graph is empty or no candidate
    was targeted. Such vacuous conformance never yields application success.
    """
    result = unevaluated_shacl("validation_not_started")
    if quantity_policy is not None:
        result["profile"] = QUANTITY_PROFILE_VERSION
    expected = set(expected_focus_nodes)
    # SHACL targetClass includes explicit subclasses without enabling inference.
    target_class = METRIC.QuantityClaim if quantity_policy is not None else METRIC.LiteralClaim
    classes = {target_class}
    pending = [target_class]
    while pending:
        for subclass in data_graph.subjects(RDFS.subClassOf, pending.pop()):
            if subclass not in classes:
                classes.add(subclass)
                pending.append(subclass)
    actual = {str(node) for cls in classes for node in data_graph.subjects(RDF.type, cls)}
    coverage = result["coverage"]
    coverage.update(
        expected_focus_nodes=sorted(expected), actual_focus_nodes=sorted(actual),
        missing_focus_nodes=sorted(expected - actual),
        unexpected_focus_nodes=sorted(actual - expected),
        complete=bool(expected) and actual == expected,
    )
    try:
        from pyshacl import validate

        shapes = (_quantity_profile(slot, quantity_policy, source_unit)
                  if quantity_policy is not None else _profile(slot))
        conforms, report_graph, report_text = validate(
            data_graph, shacl_graph=shapes, **VALIDATION_OPTIONS,
        )
        if not isinstance(report_graph, Graph):
            raise RuntimeError(f"SHACL validation failure: {report_graph}")
    except Exception as exc:
        result.update(execution_status="failed", issues=[f"{type(exc).__name__}: {exc}"])
        return result
    rows = _report_rows(report_graph)
    root = QUANTITY_PROFILE.Claim if quantity_policy is not None else PROFILE.Claim
    coverage["executed_shapes"] = sorted(str(shape) for shape in (
        {root, *shapes.objects(root, SH.property)} if actual else set()
    ))
    result.update(
        execution_status="completed", evaluated=True, conforms=bool(conforms),
        report=rows, report_text=report_text, issues=[],
    )
    if not coverage["complete"]:
        result["validation_status"] = "incomplete"
        result["issues"].append("shacl_coverage_incomplete")
    elif conforms:
        result["validation_status"] = "passed"
    else:
        # Absent fields are incomplete evidence; retain the actual SHACL failure.
        missing_only = bool(rows) and all(
            row["sourceConstraintComponent"] in {
                str(SH.MinCountConstraintComponent), str(SH.HasValueConstraintComponent),
            } and not list(data_graph.objects(
                URIRef(row["focusNode"]), URIRef(row["resultPath"]),
            ))
            for row in rows
        )
        result["validation_status"] = "incomplete" if missing_only else "failed"
        result["issues"].append("shacl_missing_fields" if missing_only else "shacl_nonconformant")
    return result


def validate_metric_result(
    metric: MetricData, *, slot: SlotSpec, quantity_policy: QuantityPolicy,
    candidate_ref: VersionedRef, shape_profile_id: str, raw: str,
) -> ShaclData:
    """Controller-only composition: use this call's normalization result immediately."""
    if metric.claim_ref != candidate_ref:
        raise ValueError("shacl_claim_mismatch")
    if quantity_policy.predicate_iri != slot.iri:
        raise ValueError("shacl_claim_mismatch")
    numeric = len(slot.datatype_iris) == 1 and slot.datatype_iris[0] in NUMERIC_DATATYPES
    expected_profile = QUANTITY_PROFILE_VERSION if numeric else PROFILE_VERSION
    if shape_profile_id != expected_profile:
        raise ValueError("shacl_profile_mismatch")
    if metric.validation_status != "passed" or metric.issues:
        result = unevaluated_shacl("metric_not_passed")
        result["profile"] = expected_profile
    elif metric.quantity is not None:
        if metric.quantity.raw != raw or not numeric:
            raise ValueError("shacl_claim_mismatch")
        graph = build_quantity_graph(
            candidate_id=candidate_ref.id, slot=slot, quantity=metric.quantity,
        )
        result = validate_graph(
            graph, slot=slot, expected_focus_nodes=[str(claim_node(candidate_ref.id))],
            quantity_policy=quantity_policy, source_unit=metric.quantity.source_unit,
        )
    elif not numeric and metric.normalized_literal is not None:
        graph = build_metric_graph(
            candidate_id=candidate_ref.id, slot=slot, raw=raw,
            value=metric.normalized_literal,
        )
        result = validate_graph(graph, slot=slot,
                                expected_focus_nodes=[str(claim_node(candidate_ref.id))])
    else:
        result = unevaluated_shacl("metric_value_missing")
        result["profile"] = expected_profile
    coverage = result["coverage"]
    return ShaclData(
        profile=result["profile"], evaluated=result["evaluated"], conforms=result["conforms"],
        validation_status=result["validation_status"],
        report=[ShaclIssue(
            focus_node=row["focusNode"], path=row["resultPath"], value=row["value"],
            source_shape=row["sourceShape"], constraint_component=row["sourceConstraintComponent"],
            severity=row["resultSeverity"], messages=row["message"],
        ) for row in result["report"]],
        coverage=FocusCoverage(
            expected=coverage["expected_focus_nodes"], actual=coverage["actual_focus_nodes"],
            missing=coverage["missing_focus_nodes"], executed_shapes=coverage["executed_shapes"],
            complete=coverage["complete"],
        ),
    )
