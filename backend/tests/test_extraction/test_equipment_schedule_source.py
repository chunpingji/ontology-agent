from datetime import date, datetime, timezone

import pytest

from app.services.extraction.equipment_schedule_source import (
    apply_daily_product_overrides,
    generate_equipment_schedules,
)


def test_generates_deterministic_production_and_cleaning_records():
    params = {
        "equipment_id": "CT64201",
        "equipment_name": "离心机",
        "workshop_code": "642",
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 31),
        "now": datetime(2026, 8, 15, tzinfo=timezone.utc),
    }

    first = generate_equipment_schedules(**params)
    second = generate_equipment_schedules(**params)

    assert first == second
    assert first
    assert {item["activity_type"] for item in first} == {"production", "cleaning"}
    assert all(item["equipment_id"] == "CT64201" for item in first)
    assert all(item["product_name"] and item["batch_no"] for item in first)
    assert all(item["start_at"] < item["end_at"] for item in first)


def test_rejects_invalid_or_excessive_range():
    base = {
        "equipment_id": "CT64201",
        "equipment_name": "离心机",
        "workshop_code": "642",
    }
    with pytest.raises(ValueError):
        generate_equipment_schedules(**base, start_date=date(2026, 8, 2), end_date=date(2026, 8, 1))
    with pytest.raises(ValueError):
        generate_equipment_schedules(**base, start_date=date(2025, 1, 1), end_date=date(2026, 2, 1))


def test_pf64216_is_fully_occupied_by_hrs_5678_in_june():
    records = generate_equipment_schedules(
        equipment_id="PF64216",
        equipment_name="钛棒过滤器",
        workshop_code="642",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    assert len(records) == 1
    schedule = records[0]
    assert schedule["product_code"] == "HRS-5678"
    assert schedule["batch_no"] == "HRS-5678-202606"
    assert schedule["equipment_id"] == "PF64216"
    assert schedule["start_at"].isoformat() == "2026-06-01T00:00:00+08:00"
    assert schedule["end_at"].isoformat() == "2026-07-01T00:00:00+08:00"
    assert schedule["priority"] == "urgent"


def test_daily_override_replaces_only_selected_day_of_full_month_schedule():
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = generate_equipment_schedules(
        equipment_id="PF64216",
        equipment_name="钛棒过滤器",
        workshop_code="642",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        now=now,
    )

    updated = apply_daily_product_overrides(
        records,
        overrides=[(date(2026, 6, 10), "HRS-1597")],
        equipment_id="PF64216",
        equipment_name="钛棒过滤器",
        workshop_code="642",
        now=now,
    )

    assert len(updated) == 3
    assert [item["product_code"] for item in updated] == ["HRS-5678", "HRS-1597", "HRS-5678"]
    selected = updated[1]
    assert selected["start_at"].isoformat() == "2026-06-10T00:00:00+08:00"
    assert selected["end_at"].isoformat() == "2026-06-11T00:00:00+08:00"
