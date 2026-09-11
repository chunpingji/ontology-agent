"""Conservative literal normalization against the frozen ontology slot."""

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation

XSD = "http://www.w3.org/2001/XMLSchema#"


def normalize_literal(raw: str, slot) -> tuple[object, str | None]:
    """Never alter the source value or invent missing calendar/unit precision."""
    types = set(slot.datatype_iris)
    if slot.constraint_status != "resolved" or len(types) != 1:
        return None, "constraint_unresolved"
    datatype = next(iter(types))
    value = raw.strip()
    if datatype == XSD + "string":
        return value, None
    if slot.canonical_unit:
        # A unit conversion needs a registered conversion contract. None is
        # currently frozen by this protocol, so do not silently strip units.
        return None, "constraint_unresolved"
    if datatype == XSD + "boolean":
        booleans = {"true": True, "1": True, "是": True, "false": False, "0": False, "否": False}
        if value.casefold() in booleans:
            return booleans[value.casefold()], None
    elif datatype in {XSD + n for n in ("integer", "int", "nonNegativeInteger", "positiveInteger")}:
        if re.fullmatch(r"[+-]?\d+", value):
            number = int(value)
            if (
                (datatype != XSD + "int" or -(2**31) <= number < 2**31)
                and (datatype != XSD + "nonNegativeInteger" or number >= 0)
                and (datatype != XSD + "positiveInteger" or number > 0)
            ):
                return number, None
    elif datatype in {XSD + n for n in ("decimal", "double", "float")}:
        try:
            number = Decimal(value)
            if number.is_finite() and math.isfinite(float(number)):
                return str(number), None
        except (InvalidOperation, ValueError, OverflowError):
            pass
    elif datatype == XSD + "gYearMonth":
        match = re.fullmatch(r"(\d{4})(?:-(\d{2})|年(\d{1,2})月)", value)
        if match and 1 <= int(match[2] or match[3]) <= 12 and int(match[1]) > 0:
            return f"{match[1]}-{int(match[2] or match[3]):02d}", None
    elif datatype == XSD + "date":
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return date.fromisoformat(value).isoformat(), None
        except ValueError:
            pass
    else:
        return None, "constraint_unresolved"
    return None, "datatype_mismatch"
