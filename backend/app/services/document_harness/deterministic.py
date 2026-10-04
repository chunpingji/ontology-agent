"""Pure representation calibration of current semantic claims; never calls a model."""

from decimal import Decimal

from rdflib import RDF, SH, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from app.services.extraction.literal_normalizer import (
    UNIT_REGISTRY_VERSION,
    LiteralNormalizationError,
    canonical_unit,
    normalize_literal,
)
from app.services.extraction.shacl_core import run_local_shacl

from .observations import RANGE
from .work import digest

PROFILE = "harness-literal-representation/1"
REP = Namespace("urn:harness:literal:")
CHECKS = ("identifier", "datatype", "unit", "shacl")
STATUSES = ("not_run", "passed", "invalid", "incomplete", "not_applicable", "error")
NUMERIC = {"number", "range", "comparison"}


def check(status, reason=None, **details):
    return {"status": status, "reason_code": reason, "details": details}


def calibration_input_hash(domain, row, catalog):
    return digest(
        {
            "domain": domain,
            "row": {
                k: v
                for k, v in row.items()
                if k
                not in {
                    "calibration",
                    "reason",
                    "review_evidence",
                    "confidence",
                    "window_id",
                }
            },
            "ontology": catalog.ontology_hash,
            "profile": PROFILE,
            "normalizer": "literal-v2",
            "units": UNIT_REGISTRY_VERSION,
            "shacl": "0.31.0",
        }
    )


def identifier_check(row, card):
    bindings = (row.get("identity_binding") or {}).get("identifiers", [])
    if not bindings:
        return check("not_applicable")
    if row.get("state") != "accepted":
        return check("incomplete", "semantic_not_supported")
    if not card:
        return check("incomplete", "identifier_type_unresolved")
    allowed = {p.iri for p in card.properties if p.identity_key} if card else set()
    allowed.update(p for group in card.identity_key_groups for p in group) if card else None
    for binding in bindings:
        if binding.get("value") != (binding.get("quote") or {}).get("text"):
            return check("invalid", "identifier_source_mismatch")
        if binding.get("property_iri") not in allowed:
            return check("invalid", "identifier_property_not_declared")
    present = {b["property_iri"] for b in bindings}
    missing = [
        list(set(group) - present)
        for group in card.identity_key_groups
        if set(group).intersection(present) and not set(group) <= present
    ]
    if missing:
        return check("incomplete", "identity_key_incomplete", missing_components=missing)
    # Namespace and scope cannot be synthesized from local literals.
    if any(b.get("identity_status") != "supported" for b in bindings):
        return check("incomplete", "identifier_scope_not_proven", format_status="not_applicable")
    return check("passed", format_status="not_applicable")


def build_literal_shapes(card, literal):
    shapes = Graph()
    root = REP.Shape
    shapes.add((root, RDF.type, SH.NodeShape))
    shapes.add((root, SH.targetClass, REP.Claim))

    def field(name, datatype, *, required=True, fixed=None):
        shape = BNode()
        shapes.add((root, SH.property, shape))
        shapes.add((shape, SH.path, REP[name]))
        shapes.add((shape, SH.minCount, Literal(int(required))))
        shapes.add((shape, SH.maxCount, Literal(1)))
        shapes.add((shape, SH.datatype, datatype))
        if fixed is not None:
            shapes.add((shape, SH.hasValue, Literal(fixed, datatype=datatype)))
        return shape

    field("raw", XSD.string)
    field("form", XSD.string, fixed=literal.kind)
    datatype = URIRef(card.datatype_iris[0])
    if literal.kind == "range":
        lower = field("lower", datatype)
        field("upper", datatype)
        shapes.add((lower, SH.lessThanOrEquals, REP["upper"]))
        field("lowerInclusive", XSD.boolean)
        field("upperInclusive", XSD.boolean)
        increasing, closed = BNode(), BNode()
        part = BNode()
        shapes.add((increasing, SH.property, part))
        shapes.add((part, SH.path, REP["lower"]))
        shapes.add((part, SH.lessThan, REP["upper"]))
        for name in ("lowerInclusive", "upperInclusive"):
            part = BNode()
            shapes.add((closed, SH.property, part))
            shapes.add((part, SH.path, REP[name]))
            shapes.add((part, SH.hasValue, Literal(True)))
        head = BNode()
        Collection(shapes, head, [increasing, closed])
        shapes.add((root, SH["or"], head))
    else:
        field("scalar", datatype)
        if literal.kind == "comparison":
            field("operator", XSD.string, fixed=literal.operator)
    if literal.canonical_unit:
        field("unit", XSD.string, fixed=literal.canonical_unit)
    return shapes


def validate_literal(card, literal, claim_id):
    data = Graph()
    node = URIRef("urn:harness:claim:" + digest(claim_id))
    data.add((node, RDF.type, REP.Claim))
    fields = {
        "raw": (literal.raw_value, XSD.string),
        "form": (literal.kind, XSD.string),
        "unit": (literal.canonical_unit, XSD.string),
    }
    numeric_type = URIRef(card.datatype_iris[0])
    if literal.kind == "range":
        fields.update(
            lower=(literal.lower, numeric_type),
            upper=(literal.upper, numeric_type),
            lowerInclusive=(literal.lower_inclusive, XSD.boolean),
            upperInclusive=(literal.upper_inclusive, XSD.boolean),
        )
    else:
        fields["scalar"] = (literal.normalized_value, numeric_type)
        if literal.kind == "comparison":
            fields["operator"] = (literal.operator, XSD.string)
    for name, (value, datatype) in fields.items():
        if value is not None:
            data.add((node, REP[name], Literal(value, datatype=datatype)))
    result = run_local_shacl(
        data, build_literal_shapes(card, literal), expected_focus_nodes=[str(node)]
    )
    status = (
        "error"
        if result["execution_status"] != "completed"
        else "incomplete"
        if not result["coverage"]["complete"]
        else "passed"
        if result["conforms"]
        else "invalid"
    )
    return check(status, None if status == "passed" else "shacl_" + status, **result)


def normalize_component(row, prop):
    """Validate the complete source range before normalizing an exact endpoint."""
    component = row.get("value_component", "whole")
    options = {
        "datatype": prop.datatype_iris[0],
        "target_unit": prop.canonical_unit,
        "source_unit": row.get("source_unit"),
    }
    match = RANGE.fullmatch(row.get("source_value", str(row["value"])))
    units = [unit for unit in (match["first_unit"], match["unit"]) if unit] if match else []
    if len({canonical_unit(unit) for unit in units}) > 1:
        raise LiteralNormalizationError("source_unit_conflict")
    if component not in {"lower", "upper"}:
        return normalize_literal(str(row["value"]), **options)
    if not match or match[component] != row["value"]:
        raise LiteralNormalizationError("unsupported endpoint grammar")
    unit = units[0] if units else None
    values = {
        key: normalize_literal(match[key] + (" " + unit if unit else ""), **options)
        for key in ("lower", "upper")
    }
    if Decimal(values["lower"].normalized_value) > Decimal(values["upper"].normalized_value):
        raise LiteralNormalizationError("range lower endpoint exceeds upper")
    literal = values[component]
    return literal.model_copy(
        update={
            "raw_value": row["value"],
            "conversion_record": {
                **literal.conversion_record,
                "endpoint_role": component,
                "source_range": row["source_value"],
            },
        }
    )


def calibrate(domain, row, catalog):
    result = {
        "input_hash": calibration_input_hash(domain, row, catalog),
        "checks": {key: check("not_applicable") for key in CHECKS},
        "literal": None,
    }
    checks = result["checks"]
    if domain == "entities":
        checks["identifier"] = identifier_check(row, catalog.classes.get(row.get("class_iri")))
        return result
    if domain != "properties":
        return result
    card = catalog.classes.get(row.get("alignment_class_iri"))
    prop = (
        next((p for p in card.properties if p.iri == row.get("predicate_iri")), None)
        if card
        else None
    )
    if row.get("state") != "accepted":
        checks["datatype"] = check("incomplete", "semantic_not_supported")
        if prop and prop.canonical_unit:
            checks["unit"] = check("incomplete", "semantic_not_supported")
        return result
    if not prop or prop.constraint_status != "resolved" or len(prop.datatype_iris) != 1:
        checks["datatype"] = check("incomplete", "datatype_unresolved")
        return result
    if "canonical_unit_conflict" in prop.diagnostics:
        checks["unit"] = check("incomplete", "canonical_unit_conflict")
        return result
    source_unit = row.get("source_unit")
    if source_unit and not row.get("source_unit_evidence"):
        checks["unit"] = check("incomplete", "unit_source_not_proven")
        return result
    try:
        literal = normalize_component(row, prop)
    except LiteralNormalizationError as exc:
        reason = str(exc)
        unit_error = reason.startswith("unit") or reason == "source_unit_conflict"
        incomplete = any(
            part in reason
            for part in (
                "unsupported",
                "unit_unknown",
                "not_exact",
                "budget",
                "unknown_boolean",
            )
        ) or (
            reason == "unit_missing_or_incompatible"
            and not source_unit
            and not any(ch.isalpha() or ch in "℃°%" for ch in str(row["value"]))
        )
        checks["unit" if unit_error else "datatype"] = check(
            "incomplete" if incomplete else "invalid",
            reason,
        )
        return result
    except Exception as exc:
        checks["datatype"] = check("error", "literal_execution_failed", message=str(exc))
        return result
    checks["datatype"] = check("passed")
    if literal.kind in NUMERIC:
        checks["unit"] = check(
            "passed" if literal.canonical_unit else "not_applicable",
            conversion=literal.conversion_record,
        )
        checks["shacl"] = validate_literal(prop, literal, row["id"])
    if all(
        checks[key]["status"] in {"passed", "not_applicable"}
        for key in ("datatype", "unit", "shacl")
    ):
        result["literal"] = literal.model_dump(mode="json")
    return result
