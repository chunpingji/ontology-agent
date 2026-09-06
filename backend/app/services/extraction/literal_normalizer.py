"""Versioned, domain-independent literal grammar; never a fact finder."""

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from unicodedata import normalize

from app.schemas.evidence import LiteralValue

XSD = "http://www.w3.org/2001/XMLSchema#"
# Symbol -> (dimension, scale to SI base). A closed unit registry is validation
# data, not a business extraction pattern. Unknown units remain unnormalized.
UNITS = {
    "kg": ("mass", "1"),
    "g": ("mass", "0.001"),
    "mg": ("mass", "0.000001"),
    "ug": ("mass", "0.000000001"),
    "ng": ("mass", "0.000000000001"),
    "m": ("length", "1"),
    "cm": ("length", "0.01"),
    "mm": ("length", "0.001"),
    "L": ("volume", "1"),
    "mL": ("volume", "0.001"),
    "s": ("time", "1"),
    "min": ("time", "60"),
    "h": ("time", "3600"),
    "%": ("ratio", "0.01"),
    "K": ("temperature", "1"),
}
UNIT_REGISTRY_VERSION = "si-subset-v1"


class LiteralNormalizationError(ValueError):
    pass


def _number(text: str, offset: int = 0) -> tuple[Decimal, int]:
    start = offset
    if offset < len(text) and text[offset] in "+-":
        offset += 1
    digits = 0
    while offset < len(text) and text[offset] in "0123456789":
        digits += 1
        offset += 1
    if offset < len(text) and text[offset] == ".":
        offset += 1
        while offset < len(text) and text[offset] in "0123456789":
            digits += 1
            offset += 1
    if not digits:
        raise LiteralNormalizationError("unsupported numeric grammar")
    if offset < len(text) and text[offset] in "eE":
        offset += 1
        if offset < len(text) and text[offset] in "+-":
            offset += 1
        exponent_start = offset
        while offset < len(text) and text[offset] in "0123456789":
            offset += 1
        if exponent_start == offset or int(text[exponent_start:offset]) > 1000:
            raise LiteralNormalizationError("unsupported exponent")
    try:
        return Decimal(text[start:offset]), offset
    except InvalidOperation as exc:
        raise LiteralNormalizationError("invalid decimal") from exc


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize_literal(
    raw: str, *, datatype: str = "string", target_unit: str | None = None
) -> LiteralValue:
    datatype = datatype.rsplit("#", 1)[-1]
    if datatype == "string":
        return LiteralValue(kind="text", raw_value=raw, normalized_value=raw)
    text = normalize("NFKC", raw).replace("−", "-").strip()
    if datatype == "boolean":
        if text not in ("true", "false", "1", "0"):
            raise LiteralNormalizationError("invalid boolean")
        return LiteralValue(
            kind="boolean",
            raw_value=raw,
            normalized_value=text in ("true", "1"),
            datatype_iri=XSD + datatype,
        )
    if datatype == "date":
        try:
            value = date.fromisoformat(text)
            if len(text) != 10 or text[4] != "-" or text[7] != "-":
                raise ValueError("only calendar ISO dates are supported")
        except ValueError as exc:
            raise LiteralNormalizationError("invalid date") from exc
        return LiteralValue(
            kind="date",
            raw_value=raw,
            normalized_value=value.isoformat(),
            datatype_iri=XSD + datatype,
        )
    if datatype not in {"decimal", "integer", "int", "float", "double", "nonNegativeInteger"}:
        raise LiteralNormalizationError("unsupported datatype")
    if len(text) > 512:
        raise LiteralNormalizationError("literal budget exceeded")
    operator, kind = "eq", "number"
    for symbol, op in (
        ("<=", "le"),
        (">=", "ge"),
        ("≤", "le"),
        ("≥", "ge"),
        ("<", "lt"),
        (">", "gt"),
        ("≈", "approx"),
    ):
        if text.startswith(symbol):
            operator, kind, text = op, "comparison", text[len(symbol) :].lstrip()
            break
    lower, end = _number(text)
    suffix = text[end:].strip()
    upper = None
    if suffix and suffix[0] in "-–—~～至到":
        if kind == "comparison":
            raise LiteralNormalizationError("comparison range is ambiguous")
        upper, end = _number(suffix[1:].lstrip())
        suffix = suffix[1:].lstrip()[end:].strip()
        kind = "range"
        if lower > upper:
            raise LiteralNormalizationError("range lower endpoint exceeds upper")
    raw_unit = suffix or None
    unit = suffix.replace("μ", "u").replace("µ", "u") or None
    if unit is not None and unit not in UNITS:
        raise LiteralNormalizationError("unit_unknown")
    dimension = UNITS[unit][0] if unit else None
    conversion = {}
    if target_unit:
        if unit is None or target_unit not in UNITS or UNITS[target_unit][0] != dimension:
            raise LiteralNormalizationError("unit_missing_or_incompatible")
        with localcontext() as context:
            context.prec = 2048
            factor = Decimal(UNITS[unit][1]) / Decimal(UNITS[target_unit][1])
            # A non-terminating conversion is not silently rounded.
            if factor * Decimal(UNITS[target_unit][1]) != Decimal(UNITS[unit][1]):
                raise LiteralNormalizationError("unit_conversion_not_exact")
            lower *= factor
            if upper is not None:
                upper *= factor
        conversion = {
            "registry_version": UNIT_REGISTRY_VERSION,
            "from": unit,
            "to": target_unit,
            "factor": _decimal_text(factor),
        }
        unit = target_unit
    for value in (lower, upper):
        if value is None:
            continue
        if (
            datatype in {"integer", "int", "nonNegativeInteger"}
            and value != value.to_integral_value()
        ):
            raise LiteralNormalizationError("integer_required")
        if datatype == "nonNegativeInteger" and value < 0:
            raise LiteralNormalizationError("negative_integer")
    return LiteralValue(
        kind=kind,
        raw_value=raw,
        datatype_iri=XSD + datatype,
        operator=operator,
        normalized_value=_decimal_text(lower) if upper is None else None,
        lower=_decimal_text(lower) if upper is not None else None,
        upper=_decimal_text(upper) if upper is not None else None,
        raw_unit=raw_unit,
        canonical_unit=unit,
        dimension=dimension,
        conversion_record=conversion,
    )
