"""报告期 APS 候选设备解析。

设备需求表中的 ``A/B``、``A或B`` 是候选集合，不等于实际使用集合。本模块在风险事实构建前，
用产品与计划生产月份查询 Mock APS，只向报告投影实际占用的设备及其所属车间。
"""

from __future__ import annotations

import calendar
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.mock_data import MockEquipmentScheduleOverride
from app.services.extraction.equipment_schedule_source import (
    apply_daily_product_overrides,
    generate_equipment_schedules,
)
from app.services.extraction.equipment_source import get_equipment_source, is_equipment_edge

_APS_FILTER_EQUIPMENT_IDS = {"PF64216", "PF64616"}
_PRODUCT_RE = re.compile(r"\bHRS-\d{3,5}\b", re.IGNORECASE)
_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_PLANNED_MONTH_RE = re.compile(
    r"计划[^。；;\n]{0,80}?(20\d{2})\s*年\s*(\d{1,2})\s*月"
)
_APS_WARNING_PREDICATE_IRI = (
    "https://ontology.pharma-gmp.cn/slpra/drug-development/hasApsScheduleWarning"
)
_APS_WARNING_CLASS_IRI = (
    "https://ontology.pharma-gmp.cn/slpra/drug-development/ApsScheduleWarning"
)


def _property(edge: dict, label: str) -> str:
    for item in edge.get("object_data_properties") or []:
        if item.get("label") == label and item.get("value"):
            return str(item["value"]).strip()
    return ""


def _walk(edges: list[dict]):
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        yield edge
        yield from _walk(edge.get("sub_relationships") or [])


def _report_context(
    edges: list[dict], source_text: str = ""
) -> tuple[str | None, date | None, date | None]:
    product_code: str | None = None
    month_value: str | None = None
    for edge in _walk(edges):
        if product_code is None:
            candidates = [
                str(edge.get("object_text") or ""),
                str(edge.get("subject_text") or ""),
            ]
            candidates.extend(
                str(item.get("value") or "")
                for item in edge.get("object_data_properties") or []
            )
            for value in candidates:
                match = _PRODUCT_RE.search(value)
                if match:
                    product_code = match.group(0).upper()
                    break
        if month_value is None:
            month_value = _property(edge, "计划生产时间") or None
    if not month_value and source_text:
        planned = _PLANNED_MONTH_RE.search(source_text)
        if planned:
            month_value = f"{planned.group(1)}年{planned.group(2)}月"
    if not month_value:
        return product_code, None, None
    match = _MONTH_RE.search(month_value)
    if not match:
        return product_code, None, None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return product_code, None, None
    return (
        product_code,
        date(year, month, 1),
        date(year, month, calendar.monthrange(year, month)[1]),
    )


def resolve_aps_equipment_candidates(
    edges: list[dict], db: Session, document_path: str | None = None
) -> list[dict]:
    """返回报告专用关系投影；不修改持久化的原始抽取关系。"""

    groups: dict[str, set[str]] = {}
    for edge in _walk(edges):
        if not is_equipment_edge(edge):
            continue
        group = _property(edge, "候选设备组")
        code = str(edge.get("object_text") or "").strip()
        if group and code in _APS_FILTER_EQUIPMENT_IDS:
            groups.setdefault(group, set()).add(code)
    if not groups:
        return list(edges)

    source_text = ""
    if document_path and Path(document_path).is_file():
        try:
            from app.services.extraction.docx_structure import parse_docx_structure

            structure = parse_docx_structure(Path(document_path))
            source_text = "\n".join(structure.paragraphs)
        except Exception:
            # APS filtering remains safe: without a reliable month it leaves candidates untouched.
            source_text = ""
    product_code, start_date, end_date = _report_context(edges, source_text)
    if not product_code or not start_date or not end_date:
        return list(edges)

    source = get_equipment_source()
    selected_by_group: dict[str, set[str]] = {}
    selected_workshops: set[str] = set()
    now = datetime.now(timezone.utc)
    for group, candidates in groups.items():
        selected: set[str] = set()
        for equipment_id in candidates:
            fact = source.resolve(equipment_id)
            if fact is None:
                continue
            schedules = generate_equipment_schedules(
                equipment_id=fact.equipment_id,
                equipment_name=fact.label,
                workshop_code=fact.workshop_code,
                start_date=start_date,
                end_date=end_date,
                now=now,
            )
            overrides = (
                db.query(MockEquipmentScheduleOverride)
                .filter(
                    MockEquipmentScheduleOverride.equipment_id == equipment_id,
                    MockEquipmentScheduleOverride.schedule_date >= start_date,
                    MockEquipmentScheduleOverride.schedule_date <= end_date,
                )
                .all()
            )
            schedules = apply_daily_product_overrides(
                schedules,
                overrides=[(item.schedule_date, item.product_code) for item in overrides],
                equipment_id=fact.equipment_id,
                equipment_name=fact.label,
                workshop_code=fact.workshop_code,
                now=now,
            )
            if any(item.get("product_code") == product_code for item in schedules):
                selected.add(equipment_id)
                selected_workshops.add(f"{fact.workshop_code}车间")
        selected_by_group[group] = selected

    # 非候选设备本身就是明确需求，也属于报告实际设备集合；其车间不能因 APS 候选裁剪而丢失。
    for edge in _walk(edges):
        if not is_equipment_edge(edge):
            continue
        group = _property(edge, "候选设备组")
        code = str(edge.get("object_text") or "").strip()
        if group in selected_by_group and code not in selected_by_group[group]:
            continue
        fact = source.resolve(code)
        if fact is not None:
            selected_workshops.add(f"{fact.workshop_code}车间")

    def project(items: list[dict]) -> list[dict]:
        projected: list[dict] = []
        for original in items:
            if not isinstance(original, dict):
                continue
            edge = deepcopy(original)
            group = _property(edge, "候选设备组")
            code = str(edge.get("object_text") or "").strip()
            if group in selected_by_group and is_equipment_edge(edge):
                if code not in selected_by_group[group]:
                    continue
                edge.setdefault("object_data_properties", []).append({
                    "iri": None,
                    "label": "APS排期确认",
                    "value": f"{product_code}；{start_date:%Y-%m}；实际使用",
                    "source": "external",
                })
            # 报告已有 APS 决策时，生产计划的车间子关系必须服从实际设备车间集合。
            if edge.get("predicate_label") == "生产车间":
                workshop = str(edge.get("object_text") or "").replace(" ", "")
                if workshop not in selected_workshops:
                    continue
            edge["sub_relationships"] = project(edge.get("sub_relationships") or [])
            projected.append(edge)
        return projected

    projected = project(edges)
    for group, selected in selected_by_group.items():
        if selected:
            continue
        candidates = "、".join(sorted(groups[group]))
        warning = (
            f"APS排期冲突：产品 {product_code} 计划于 {start_date:%Y年%m月} 生产，"
            f"候选设备 {candidates} 均未找到该产品的实际占用排期，"
            "无法确认实际使用设备及车间，请调整或确认 APS 排期后重新生成报告。"
        )
        projected.append({
            "predicate_iri": _APS_WARNING_PREDICATE_IRI,
            "predicate_label": "APS排期告警",
            "object_class_iri": _APS_WARNING_CLASS_IRI,
            "object_text": warning,
            "object_source": "external",
            "object_data_properties": [
                {"iri": None, "label": "候选设备组", "value": group},
                {"iri": None, "label": "计划产品", "value": product_code},
                {"iri": None, "label": "计划月份", "value": f"{start_date:%Y-%m}"},
            ],
            "sub_relationships": [],
            "source_ref": "Mock APS设备排期",
        })
    return projected
