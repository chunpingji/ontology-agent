"""设备排期 Mock 数据源。

按设备与日期范围确定性生成生产、清洗排期，供主数据页面验证年/月/周可视化。
真实 APS/MES 接入后可在保持 API 契约不变的前提下替换本数据源。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

_PRODUCTS = (
    ("PRD-API-A", "API-A", "原料药 A", "kg"),
    ("PRD-API-B", "API-B", "原料药 B", "kg"),
    ("PRD-INT-C", "INT-C", "中间体 C", "kg"),
    ("PRD-API-D", "API-D", "原料药 D", "kg"),
)

_FULL_JUNE_EQUIPMENT_ID = "PF64216"
_FULL_JUNE_PRODUCT_CODE = "HRS-5678"
EDITABLE_PRODUCTS = {"HRS-5678", "HRS-1597"}


def _seed(value: str) -> int:
    return sum(ord(char) for char in value)


def _status(start_at: datetime, end_at: datetime, now: datetime) -> tuple[str, int]:
    if end_at <= now:
        return "completed", 100
    if start_at <= now < end_at:
        elapsed = (now - start_at).total_seconds()
        total = (end_at - start_at).total_seconds()
        return "running", max(1, min(99, round(elapsed / total * 100)))
    return "scheduled", 0


def _full_june_schedule(
    *,
    year: int,
    equipment_id: str,
    equipment_name: str,
    workshop_code: str,
    now: datetime,
) -> dict:
    start_at = datetime(year, 6, 1, tzinfo=SHANGHAI)
    end_at = datetime(year, 7, 1, tzinfo=SHANGHAI)
    status, progress = _status(start_at, end_at, now)
    batch_no = f"{_FULL_JUNE_PRODUCT_CODE}-{year}06"
    return {
        "id": f"SCH-{equipment_id}-{year}06-HRS5678",
        "task_id": f"TASK-{equipment_id}-{year}06-HRS5678",
        "task_name": f"{_FULL_JUNE_PRODUCT_CODE} 全月生产任务",
        "activity_type": "production",
        "status": status,
        "priority": "urgent",
        "product_id": "PRD-HRS-5678",
        "product_code": _FULL_JUNE_PRODUCT_CODE,
        "product_name": _FULL_JUNE_PRODUCT_CODE,
        "batch_no": batch_no,
        "planned_quantity": 1000,
        "quantity_unit": "kg",
        "equipment_id": equipment_id,
        "equipment_name": equipment_name,
        "workshop_code": workshop_code,
        "start_at": start_at,
        "end_at": end_at,
        "actual_start_at": start_at if status in {"running", "completed"} else None,
        "actual_end_at": end_at if status == "completed" else None,
        "process_step": "钛棒过滤",
        "operator_team": f"{workshop_code}车间专项生产班",
        "progress": progress,
        "remark": "HRS-5678 独占 PF64216 钛棒过滤器整个 6 月排期",
    }


def apply_daily_product_overrides(
    records: list[dict],
    *,
    overrides: list[tuple[date, str]],
    equipment_id: str,
    equipment_name: str,
    workshop_code: str,
    now: datetime,
) -> list[dict]:
    """用人工选择的产品替换设备整日占用，同时保留原排期的前后区间。"""

    result = list(records)
    for schedule_date, product_code in sorted(overrides):
        if product_code not in EDITABLE_PRODUCTS:
            raise ValueError(f"unsupported product code: {product_code}")
        day_start = datetime.combine(schedule_date, time.min, SHANGHAI)
        day_end = day_start + timedelta(days=1)
        next_result: list[dict] = []
        for item in result:
            if item["end_at"] <= day_start or item["start_at"] >= day_end:
                next_result.append(item)
                continue
            if item["start_at"] < day_start:
                before = dict(item)
                before["id"] = f'{item["id"]}-BEFORE-{schedule_date:%Y%m%d}'
                before["end_at"] = day_start
                next_result.append(before)
            if item["end_at"] > day_end:
                after = dict(item)
                after["id"] = f'{item["id"]}-AFTER-{schedule_date:%Y%m%d}'
                after["start_at"] = day_end
                next_result.append(after)

        status, progress = _status(day_start, day_end, now)
        batch_no = f"{product_code}-{schedule_date:%Y%m%d}"
        next_result.append(
            {
                "id": f"SCH-{equipment_id}-{schedule_date:%Y%m%d}-OVERRIDE",
                "task_id": f"TASK-{equipment_id}-{schedule_date:%Y%m%d}-OVERRIDE",
                "task_name": f"{product_code} 日占用任务",
                "activity_type": "production",
                "status": status,
                "priority": "high",
                "product_id": f"PRD-{product_code}",
                "product_code": product_code,
                "product_name": product_code,
                "batch_no": batch_no,
                "planned_quantity": 100,
                "quantity_unit": "kg",
                "equipment_id": equipment_id,
                "equipment_name": equipment_name,
                "workshop_code": workshop_code,
                "start_at": day_start,
                "end_at": day_end,
                "actual_start_at": day_start if status in {"running", "completed"} else None,
                "actual_end_at": day_end if status == "completed" else None,
                "process_step": "设备占用",
                "operator_team": f"{workshop_code}车间生产班",
                "progress": progress,
                "remark": "月视图人工修改的 Mock 产品占用",
            }
        )
        result = next_result
    return sorted(result, key=lambda item: (item["start_at"], item["id"]))


def generate_equipment_schedules(
    *,
    equipment_id: str,
    equipment_name: str,
    workshop_code: str,
    start_date: date,
    end_date: date,
    now: datetime | None = None,
) -> list[dict]:
    """生成闭区间 ``start_date..end_date`` 内与设备相交的排期。"""

    if end_date < start_date:
        raise ValueError("end_date must not be earlier than start_date")
    if (end_date - start_date).days > 370:
        raise ValueError("date range must not exceed 371 days")

    current = now or datetime.now(timezone.utc)
    seed = _seed(equipment_id)
    # 向前看一天，以包含前一天开始、跨入查询范围的生产任务。
    cursor = start_date - timedelta(days=1)
    records: list[dict] = []

    while cursor <= end_date:
        # 每台设备采用不同但稳定的两/三天生产节拍。
        if (cursor.toordinal() + seed) % 3 != 0:
            cursor += timedelta(days=1)
            continue

        product_id, product_code, product_name, unit = _PRODUCTS[
            (cursor.toordinal() + seed) % len(_PRODUCTS)
        ]
        start_hour = 6 + seed % 5
        duration_hours = 10 + (cursor.day + seed) % 11
        production_start = datetime.combine(cursor, time(start_hour), SHANGHAI)
        production_end = production_start + timedelta(hours=duration_hours)
        batch_no = f"{product_code.replace('-', '')}-{cursor:%y%m%d}-{seed % 90 + 10:02d}"
        task_token = f"{equipment_id}-{cursor:%Y%m%d}"
        status, progress = _status(production_start, production_end, current)

        records.append(
            {
                "id": f"SCH-{task_token}-P",
                "task_id": f"TASK-{task_token}",
                "task_name": f"{product_name}生产任务",
                "activity_type": "production",
                "status": status,
                "priority": "high" if cursor.day % 7 == 0 else "normal",
                "product_id": product_id,
                "product_code": product_code,
                "product_name": product_name,
                "batch_no": batch_no,
                "planned_quantity": 100 + (seed + cursor.day * 17) % 401,
                "quantity_unit": unit,
                "equipment_id": equipment_id,
                "equipment_name": equipment_name,
                "workshop_code": workshop_code,
                "start_at": production_start,
                "end_at": production_end,
                "actual_start_at": production_start if status in {"running", "completed"} else None,
                "actual_end_at": production_end if status == "completed" else None,
                "process_step": "生产加工",
                "operator_team": f"{workshop_code}车间{'甲' if seed % 2 == 0 else '乙'}班",
                "progress": progress,
                "remark": "生产结束后执行批后清洗",
            }
        )

        cleaning_start = production_end
        cleaning_end = cleaning_start + timedelta(hours=2 + seed % 2)
        cleaning_status, cleaning_progress = _status(cleaning_start, cleaning_end, current)
        records.append(
            {
                "id": f"SCH-{task_token}-C",
                "task_id": f"TASK-{task_token}-CLEAN",
                "task_name": "批后设备清洗",
                "activity_type": "cleaning",
                "status": cleaning_status,
                "priority": "normal",
                "product_id": product_id,
                "product_code": product_code,
                "product_name": product_name,
                "batch_no": batch_no,
                "planned_quantity": None,
                "quantity_unit": None,
                "equipment_id": equipment_id,
                "equipment_name": equipment_name,
                "workshop_code": workshop_code,
                "start_at": cleaning_start,
                "end_at": cleaning_end,
                "actual_start_at": (
                    cleaning_start if cleaning_status in {"running", "completed"} else None
                ),
                "actual_end_at": cleaning_end if cleaning_status == "completed" else None,
                "process_step": "设备清洗",
                "operator_team": f"{workshop_code}车间清洗班",
                "progress": cleaning_progress,
                "remark": None,
            }
        )
        cursor += timedelta(days=1)

    range_start = datetime.combine(start_date, time.min, SHANGHAI)
    range_end = datetime.combine(end_date + timedelta(days=1), time.min, SHANGHAI)
    if equipment_id == _FULL_JUNE_EQUIPMENT_ID:
        for year in range(start_date.year, end_date.year + 1):
            full_month = _full_june_schedule(
                year=year,
                equipment_id=equipment_id,
                equipment_name=equipment_name,
                workshop_code=workshop_code,
                now=current,
            )
            if full_month["start_at"] >= range_end or full_month["end_at"] <= range_start:
                continue
            # 专项任务独占整月，移除所有与它重叠的通用生产和清洗排期。
            records = [
                item
                for item in records
                if item["end_at"] <= full_month["start_at"]
                or item["start_at"] >= full_month["end_at"]
            ]
            records.append(full_month)

    return [
        item
        for item in records
        if item["start_at"] < range_end and item["end_at"] > range_start
    ]
