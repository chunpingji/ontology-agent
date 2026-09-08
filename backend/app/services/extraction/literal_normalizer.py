"""Versioned, domain-independent literal grammar; never a fact finder."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
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
    "day": ("time", "86400"),
    "m2": ("area", "1"),
    "cm2": ("area", "0.0001"),
    "m3": ("volume", "1000"),
    "%": ("ratio", "0.01"),
    "K": ("temperature", "1"),
    "°C": ("temperature", "1"),
}
UNIT_REGISTRY_VERSION = "si-subset-v2-compound"


def canonical_unit(unit):
    unit = normalize("NFKC", unit).replace("μ", "u").replace("µ", "u")
    return {"℃": "°C", "C": "°C", "d": "day", "天": "day"}.get(unit, unit)


def unit_definition(unit):
    parts = canonical_unit(unit).split("/")
    if any(part not in UNITS for part in parts):
        raise LiteralNormalizationError("unit_unknown")
    dimension, scale = UNITS[parts[0]]
    scale = Fraction(scale)
    # Keep denominator dimensions explicit (mg/kg is not a unitless scalar).
    for part in parts[1:]:
        dimension += "/" + UNITS[part][0]
        scale /= Fraction(UNITS[part][1])
    return dimension, scale


def _legend_boolean(symbol, legend):
    if symbol not in {"√", "✓", "✔", "×", "✗", "✘"} or not legend:
        raise LiteralNormalizationError("unknown_boolean" if symbol == "—" else "invalid boolean")
    # A closed legend grammar, not a default meaning for ticks/crosses.
    text = normalize("NFKC", legend).translate(str.maketrans("“”‘’", '\"\"\'\''))
    pattern = (
        re.escape(symbol) + r"['\"]?\s*(?:表示|代表|为|:|=)\s*['\"]?"
        r"([^;；。\r\n]+)"
    )
    values = set()
    for meaning in re.findall(pattern, text):
        meaning = meaning.strip(" \t'\"")
        if any(word in meaning for word in ("可能", "不足", "不充分", "不详", "不确定", "无法")):
            raise LiteralNormalizationError("invalid boolean legend")
        if meaning in {"true", "是", "阳性", "有"} or meaning.startswith(("有", "存在")):
            values.add(True)
        elif meaning in {"false", "否", "阴性", "无"} or meaning.startswith(("没有", "无")):
            values.add(False)
        else:
            raise LiteralNormalizationError("invalid boolean legend")
    if len(values) != 1:
        raise LiteralNormalizationError("invalid boolean legend")
    return next(iter(values))


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
    raw: str, *, datatype: str = "string", target_unit: str | None = None,
    source_unit: str | None = None, boolean_legend: str | None = None,
) -> LiteralValue:
    datatype = datatype.rsplit("#", 1)[-1]
    if datatype == "string":
        return LiteralValue(kind="text", raw_value=raw, normalized_value=raw)
    text = normalize("NFKC", raw).replace("−", "-").strip()
    if datatype == "boolean":
        booleans = {"true": True, "false": False, "1": True, "0": False, "是": True, "否": False}
        value = booleans[text] if text in booleans else _legend_boolean(text, boolean_legend)
        return LiteralValue(
            kind="boolean",
            raw_value=raw,
            normalized_value=value,
            datatype_iri=XSD + datatype,
            conversion_record={"boolean_legend": boolean_legend} if boolean_legend else {},
        )
    if datatype == "gYearMonth":
        if text.endswith("月"):
            parts = text[:-1].split("年")
        else:
            parts = text.split("-")
        if (
            len(parts) != 2
            or len(parts[0]) != 4
            or not 1 <= len(parts[1]) <= 2
            or any(not part or any(c not in "0123456789" for c in part) for part in parts)
        ):
            raise LiteralNormalizationError("invalid year-month")
        year, month = map(int, parts)
        if not 1 <= year <= 9999 or not 1 <= month <= 12:
            raise LiteralNormalizationError("invalid year-month")
        return LiteralValue(
            kind="date",
            raw_value=raw,
            normalized_value=f"{year:04d}-{month:02d}",
            datatype_iri=XSD + datatype,
        )
    if datatype == "date":
        try:
            chinese = re.fullmatch(r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日", text)
            if chinese:
                value = date(*(int(part) for part in chinese.groups()))
            else:
                value = date.fromisoformat(text)
                if len(text) != 10 or text[4] != "-" or text[7] != "-":
                    raise ValueError("only calendar dates are supported")
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
    raw_unit = suffix or source_unit or None
    unit = canonical_unit(raw_unit) if raw_unit else None
    if suffix and source_unit and canonical_unit(suffix) != canonical_unit(source_unit):
        raise LiteralNormalizationError("source_unit_conflict")
    with localcontext() as context:
        context.prec = 2048
        dimension, scale = unit_definition(unit) if unit else (None, None)
    conversion = {}
    if target_unit:
        target_unit = canonical_unit(target_unit)
        with localcontext() as context:
            context.prec = 2048
            target_dimension, target_scale = unit_definition(target_unit)
            if unit is None or target_dimension != dimension:
                raise LiteralNormalizationError("unit_missing_or_incompatible")
            ratio = scale / target_scale
            factor = Decimal(ratio.numerator) / Decimal(ratio.denominator)
            # A non-terminating conversion is not silently rounded.
            if Fraction(factor) != ratio:
                raise LiteralNormalizationError("unit_conversion_not_exact")
            offset = Decimal("273.15") * (
                int(unit == "°C" and target_unit == "K")
                - int(unit == "K" and target_unit == "°C")
            )
            lower = lower * factor + offset
            if upper is not None:
                upper = upper * factor + offset
        conversion = {
            "registry_version": UNIT_REGISTRY_VERSION,
            "from": unit,
            "to": target_unit,
            "factor": _decimal_text(factor),
            "offset": _decimal_text(offset),
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
