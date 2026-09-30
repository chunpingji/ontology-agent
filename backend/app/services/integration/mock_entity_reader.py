"""Finite source-format adapter. No ontology, identifier or mention interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mock_data import (
    MockDepartment,
    MockEquipment,
    MockProductionArea,
    MockRole,
    MockTeamMember,
)

SOURCE_SYSTEM = "builtin_mock"
SCAN_LIMIT = 5000


@dataclass(frozen=True)
class Dataset:
    model: type
    label: str
    columns: tuple[str, ...]
    attributes: bool = True


DATASETS = {
    "production_areas": Dataset(
        MockProductionArea,
        "生产区域",
        (
            "id",
            "code",
            "iri",
            "label",
            "description",
        ),
    ),
    "equipment": Dataset(
        MockEquipment,
        "设备",
        (
            "id",
            "equipment_id",
            "iri",
            "label",
            "equipment_class_iri",
            "workshop_code",
        ),
    ),
    "departments": Dataset(MockDepartment, "部门", ("id", "code", "iri", "label", "description")),
    "roles": Dataset(
        MockRole,
        "角色",
        (
            "id",
            "code",
            "iri",
            "label",
            "role_class_iri",
            "description",
        ),
    ),
    "team_members": Dataset(
        MockTeamMember,
        "小组成员",
        (
            "id",
            "team_type",
            "name",
            "role_label",
            "department",
            "role_class_iri",
            "role_code",
        ),
        attributes=False,
    ),
}


def is_iri(value: str) -> bool:
    try:
        return bool(value and not any(c.isspace() for c in value) and urlsplit(value).scheme)
    except ValueError:
        return False


def validate_path(dataset: str, path: str) -> None:
    spec = DATASETS.get(dataset)
    if spec is None:
        raise ValueError("未知 Mock 数据集")
    prefix, _, key = path.partition(":")
    if prefix == "col" and key in spec.columns:
        return
    if spec.attributes and prefix in {"attr-iri", "attr-label"} and key.strip():
        if prefix == "attr-label" or is_iri(key):
            return
    raise ValueError(f"非法来源字段定位：{path}")


def select_values(row: dict, path: str) -> list[tuple[object, int | None]]:
    prefix, _, key = path.partition(":")
    if prefix == "col":
        value = row.get(key)
        if key == "id" and value is not None:
            value = str(value)
        return [] if value is None else [(value, None)]
    field = "iri" if prefix == "attr-iri" else "label"
    return [
        (item["value"], i)
        for i, item in enumerate(row.get("data_properties") or [])
        if isinstance(item, dict) and item.get(field) == key and item.get("value") is not None
    ]


class MockEntityReader:
    def __init__(self, db: Session):
        self.db = db

    def read(self, dataset: str) -> tuple[list[dict], bool]:
        table = DATASETS[dataset].model.__table__
        # Core savepoint does not flush pending ORM writes (Session.begin_nested does).
        # An unavailable source must not poison other PostgreSQL SELECTs in the batch.
        conn = self.db.connection()
        with conn.begin_nested():
            rows = conn.execute(select(table).order_by(table.c.id).limit(SCAN_LIMIT + 1))
            items = [dict(row) for row in rows.mappings()]
        return items[:SCAN_LIMIT], len(items) > SCAN_LIMIT

    @staticmethod
    def fields(dataset: str, rows: list[dict]) -> list[dict]:
        spec = DATASETS[dataset]
        paths = {f"col:{c}" for c in spec.columns}
        for row in rows:
            for attr in row.get("data_properties") or []:
                if not isinstance(attr, dict):
                    continue
                for prefix, key in (("attr-iri", "iri"), ("attr-label", "label")):
                    if isinstance(attr.get(key), str) and attr[key].strip():
                        path = f"{prefix}:{attr[key]}"
                        try:
                            validate_path(dataset, path)
                        except ValueError:
                            continue
                        paths.add(path)
        return [
            {
                "source_path": p,
                "label": p.partition(":")[2],
                "source_datatype": "string",
                "multiple": not p.startswith("col:"),
            }
            for p in sorted(paths)
        ]
