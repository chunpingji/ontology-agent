"""Fixed, local SHACL representation checks, separate from factual correctness.

The profile describes one submitted literal claim. Its required fields are a
tool representation contract, not inferred ontology mandatory properties or
business acceptance limits. Neither caller-provided shapes nor rules run here.
"""

from __future__ import annotations

from urllib.parse import quote

from rdflib import RDF, RDFS, SH, XSD, Graph, Literal, Namespace, URIRef

from app.services.extraction.literal_normalizer import canonical_unit
from app.services.extraction.ontology_guided.contracts import SlotSpec

PROFILE_VERSION = "metric-representation-v1"
METRIC = Namespace("urn:ontology-agent:metric:")
PROFILE = Namespace(f"urn:ontology-agent:shapes:{PROFILE_VERSION}:")
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
) -> dict:
    """Validate against the fixed profile and independently account for coverage.

    Native ``conforms`` is retained even if the graph is empty or no candidate
    was targeted. Such vacuous conformance never yields application success.
    """
    result = unevaluated_shacl("validation_not_started")
    expected = set(expected_focus_nodes)
    # SHACL targetClass includes explicit subclasses without enabling inference.
    classes = {METRIC.LiteralClaim}
    pending = [METRIC.LiteralClaim]
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

        shapes = _profile(slot)
        conforms, report_graph, report_text = validate(
            data_graph, shacl_graph=shapes, **VALIDATION_OPTIONS,
        )
        if not isinstance(report_graph, Graph):
            raise RuntimeError(f"SHACL validation failure: {report_graph}")
    except Exception as exc:
        result.update(execution_status="failed", issues=[f"{type(exc).__name__}: {exc}"])
        return result
    rows = _report_rows(report_graph)
    coverage["executed_shapes"] = sorted(str(shape) for shape in (
        {PROFILE.Claim, *shapes.objects(PROFILE.Claim, SH.property)} if actual else set()
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
