from datetime import date

from app.models.mock_data import MockEquipmentScheduleOverride
from app.services.extraction.equipment_source import USES_EQUIPMENT_IRI, iter_equipment_edges
from app.services.reporting.aps_equipment_resolver import (
    _report_context,
    resolve_aps_equipment_candidates,
)


def _candidate(code: str) -> dict:
    return {
        "predicate_iri": USES_EQUIPMENT_IRI,
        "predicate_label": "使用设备",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/ProcessEquipment",
        "object_text": code,
        "object_data_properties": [
            {"iri": None, "label": "候选设备组", "value": "PF64216|PF64616"},
        ],
        "sub_relationships": [],
    }


def _edges(product_code: str) -> list[dict]:
    return [
        {
            "predicate_label": "描述",
            "object_class_iri": "DrugProduct",
            "object_text": product_code,
            "object_data_properties": [],
            "sub_relationships": [],
        },
        _candidate("PF64216"),
        _candidate("PF64616"),
        {
            "predicate_label": "含备样生产计划",
            "object_text": "临床备样生产计划",
            "object_data_properties": [
                {"iri": None, "label": "计划生产时间", "value": "2026年6月"},
            ],
            "sub_relationships": [
                {"predicate_label": "生产车间", "object_text": "642车间"},
                {"predicate_label": "生产车间", "object_text": "646车间"},
            ],
        },
    ]


def test_hrs_5678_uses_only_pf64216_and_prunes_646_workshop(db):
    projected = resolve_aps_equipment_candidates(_edges("HRS-5678"), db)

    assert [edge["object_text"] for edge in iter_equipment_edges(projected)] == ["PF64216"]
    plan = next(edge for edge in projected if edge.get("object_text") == "临床备样生产计划")
    assert [item["object_text"] for item in plan["sub_relationships"]] == ["642车间"]


def test_hrs_1597_override_selects_pf64616_and_prunes_642_workshop(db):
    db.add(MockEquipmentScheduleOverride(
        equipment_id="PF64616",
        schedule_date=date(2026, 6, 10),
        product_code="HRS-1597",
    ))
    db.commit()

    projected = resolve_aps_equipment_candidates(_edges("HRS-1597"), db)

    assert [edge["object_text"] for edge in iter_equipment_edges(projected)] == ["PF64616"]
    plan = next(edge for edge in projected if edge.get("object_text") == "临床备样生产计划")
    assert [item["object_text"] for item in plan["sub_relationships"]] == ["646车间"]


def test_no_matching_aps_schedule_emits_explicit_warning(db):
    projected = resolve_aps_equipment_candidates(_edges("HRS-1597"), db)

    assert list(iter_equipment_edges(projected)) == []
    warning = next(
        edge for edge in projected if edge.get("predicate_label") == "APS排期告警"
    )
    assert "PF64216、PF64616" in warning["object_text"]
    assert "2026年06月" in warning["object_text"]
    assert "请调整或确认 APS 排期" in warning["object_text"]


def test_report_context_reads_planned_month_from_document_prose():
    product, start, end = _report_context(
        _edges("HRS-1597"),
        "本次备样用于Ⅰ期临床，计划在2026年6月于642和646车间完成生产。",
    )

    assert product == "HRS-1597"
    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 30)
