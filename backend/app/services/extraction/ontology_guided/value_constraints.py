"""Conservative literal normalization against the frozen ontology slot."""

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.extraction.literal_normalizer import (
    UNIT_REGISTRY_VERSION,
    LiteralNormalizationError,
)
from app.services.extraction.literal_normalizer import (
    normalize_literal as normalize_registered_literal,
)

XSD = "http://www.w3.org/2001/XMLSchema#"
UNIT_POLICY_VERSION = "source-bound-units-v1"
UNIT_NORMALIZATION_VERSION = f"{UNIT_POLICY_VERSION}:{UNIT_REGISTRY_VERSION}"
CONSTRAINT_REASONS = {
    "unit_source_missing": "缺少修饰当前数值的原文单位",
    "unit_binding_source_missing": "单位证明未覆盖当前数值及单位来源",
    "unit_binding_not_supported": "原文尚未证明单位属于当前数值",
    "unit_record_mismatch": "单位不属于当前数值所在表达式或对应字段",
    "unit_quote_partial": "单位引用不完整，不能省略前缀、分母或指数",
    "quantity_value_partial": "数值引用截取了另一个数字的一部分",
    "source_unit_conflict": "数值后缀单位与引用的来源单位冲突",
    "unit_unknown": "原文单位尚未登记，无法确定其含义",
    "unit_missing_or_incompatible": "原文单位缺失或与属性要求的单位不兼容",
    "unit_conversion_not_exact": "单位换算无法精确表示，尚未配置舍入规则",
    "scalar_value_required": "区间或比较值不能直接作为精确标量，须独立核验字段角色",
    "datatype_mismatch": "原文值或换算结果不符合本体数据类型",
    "constraint_unresolved": "本体数据类型或单位约束尚未解决",
    "unsupported numeric grammar": "原文数值格式暂不支持规范化",
}


def normalize_literal(
    raw: str, slot, *, source_unit: str | None = None, normalization_record: dict | None = None,
) -> tuple[object, str | None]:
    """Never alter the source value or invent missing calendar/unit precision."""
    types = set(slot.datatype_iris)
    if slot.constraint_status != "resolved" or len(types) != 1:
        return None, "constraint_unresolved"
    datatype = next(iter(types))
    value = raw.strip()
    if datatype == XSD + "string":
        return value, None
    if slot.canonical_unit:
        if datatype not in {XSD + n for n in (
            "decimal", "double", "float", "integer", "int", "nonNegativeInteger",
            "positiveInteger",
        )}:
            return None, "constraint_unresolved"
        try:
            literal = normalize_registered_literal(
                raw, datatype="decimal", source_unit=source_unit, target_unit=slot.canonical_unit,
            )
        except LiteralNormalizationError as exc:
            return None, str(exc)
        # A scalar ontology slot cannot silently lose a range or comparison.
        # Its independently verified endpoint must have been quoted explicitly.
        if literal.kind != "number":
            return None, "scalar_value_required"
        normalized, issue = normalize_literal(
            literal.normalized_value, slot.model_copy(update={"canonical_unit": None}),
        )
        if issue:
            return None, issue
        if normalization_record is not None:
            record = literal.conversion_record
            normalization_record.update(
                **record, policy_version=UNIT_POLICY_VERSION, raw_unit=literal.raw_unit,
                datatype_iri=datatype,
                mapping_kind=("conversion" if record["from"] != record["to"] else
                              "identity" if literal.raw_unit == record["to"] else "alias"),
            )
        return normalized, None
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
