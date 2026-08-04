"""Mock 外部事实源数据管理 API —— 完整 CRUD，数据库持久化。

支持查看、新增、编辑、删除 mock 数据。修改立即生效，重启后保留。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.mock_data import (
    MockDepartment,
    MockEquipment,
    MockEquipmentScheduleOverride,
    MockProductionArea,
    MockRole,
    MockTeamMember,
)
from app.services.extraction.equipment_schedule_source import (
    EDITABLE_PRODUCTS,
    apply_daily_product_overrides,
    generate_equipment_schedules,
)

router = APIRouter()


# --- Pydantic schemas ---------------------------------------------------------


class DataProperty(BaseModel):
    iri: str | None
    label: str
    value: str


class DepartmentSchema(BaseModel):
    id: UUID | None = None
    code: str
    iri: str
    label: str
    description: str = ""
    data_properties: list[DataProperty] = []


class RoleSchema(BaseModel):
    id: UUID | None = None
    code: str
    iri: str
    label: str
    role_class_iri: str
    description: str = ""
    data_properties: list[DataProperty] = []


class EquipmentSchema(BaseModel):
    id: UUID | None = None
    equipment_id: str
    iri: str
    label: str
    equipment_class_iri: str
    workshop_code: str
    data_properties: list[DataProperty] = []


class EquipmentScheduleSchema(BaseModel):
    id: str
    task_id: str
    task_name: str
    activity_type: str
    status: str
    priority: str
    product_id: str | None = None
    product_code: str | None = None
    product_name: str | None = None
    batch_no: str | None = None
    planned_quantity: float | None = None
    quantity_unit: str | None = None
    equipment_id: str
    equipment_name: str
    workshop_code: str
    start_at: datetime
    end_at: datetime
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    process_step: str | None = None
    operator_team: str | None = None
    progress: int
    remark: str | None = None


class EquipmentScheduleOccupancyUpdate(BaseModel):
    equipment_id: str
    schedule_date: date
    product_code: Literal["HRS-5678", "HRS-1597"]


class EquipmentScheduleOccupancyResponse(EquipmentScheduleOccupancyUpdate):
    id: UUID


class ProductionAreaSchema(BaseModel):
    id: UUID | None = None
    code: str
    iri: str
    label: str
    description: str = ""
    data_properties: list[DataProperty] = []


class TeamMemberSchema(BaseModel):
    id: UUID | None = None
    team_type: str  # "assessment" or "approver"
    name: str
    role_label: str
    department: str
    role_class_iri: str
    role_code: str


# --- Departments CRUD ---------------------------------------------------------


@router.get("/departments")
def list_departments(db: Session = Depends(get_db)):
    items = db.query(MockDepartment).all()
    return [
        {
            "id": str(d.id),
            "code": d.code,
            "iri": d.iri,
            "label": d.label,
            "description": d.description,
            "data_properties": d.data_properties,
        }
        for d in items
    ]


@router.post("/departments")
def create_department(dept: DepartmentSchema, db: Session = Depends(get_db)):
    exists = db.query(MockDepartment).filter_by(code=dept.code).first()
    if exists:
        raise HTTPException(409, f"Department with code '{dept.code}' already exists")

    new_dept = MockDepartment(
        code=dept.code,
        iri=dept.iri,
        label=dept.label,
        description=dept.description,
        data_properties=[dp.model_dump() for dp in dept.data_properties],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_dept)
    db.commit()
    db.refresh(new_dept)
    return {"id": str(new_dept.id), "code": new_dept.code}


@router.put("/departments/{dept_id}")
def update_department(dept_id: UUID, dept: DepartmentSchema, db: Session = Depends(get_db)):
    existing = db.get(MockDepartment, dept_id)
    if not existing:
        raise HTTPException(404, "Department not found")

    existing.code = dept.code
    existing.iri = dept.iri
    existing.label = dept.label
    existing.description = dept.description
    existing.data_properties = [dp.model_dump() for dp in dept.data_properties]
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "code": existing.code}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockDepartment, dept_id)
    if not existing:
        raise HTTPException(404, "Department not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(dept_id)}


# --- Roles CRUD ---------------------------------------------------------------


@router.get("/roles")
def list_roles(db: Session = Depends(get_db)):
    items = db.query(MockRole).all()
    return [
        {
            "id": str(r.id),
            "code": r.code,
            "iri": r.iri,
            "label": r.label,
            "role_class_iri": r.role_class_iri,
            "description": r.description,
            "data_properties": r.data_properties,
        }
        for r in items
    ]


@router.post("/roles")
def create_role(role: RoleSchema, db: Session = Depends(get_db)):
    exists = db.query(MockRole).filter_by(code=role.code).first()
    if exists:
        raise HTTPException(409, f"Role with code '{role.code}' already exists")

    new_role = MockRole(
        code=role.code,
        iri=role.iri,
        label=role.label,
        role_class_iri=role.role_class_iri,
        description=role.description,
        data_properties=[dp.model_dump() for dp in role.data_properties],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return {"id": str(new_role.id), "code": new_role.code}


@router.put("/roles/{role_id}")
def update_role(role_id: UUID, role: RoleSchema, db: Session = Depends(get_db)):
    existing = db.get(MockRole, role_id)
    if not existing:
        raise HTTPException(404, "Role not found")

    existing.code = role.code
    existing.iri = role.iri
    existing.label = role.label
    existing.role_class_iri = role.role_class_iri
    existing.description = role.description
    existing.data_properties = [dp.model_dump() for dp in role.data_properties]
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "code": existing.code}


@router.delete("/roles/{role_id}")
def delete_role(role_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockRole, role_id)
    if not existing:
        raise HTTPException(404, "Role not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(role_id)}


# --- Equipment CRUD -----------------------------------------------------------


@router.get("/equipment")
def list_equipment(db: Session = Depends(get_db)):
    items = db.query(MockEquipment).all()
    return [
        {
            "id": str(e.id),
            "equipment_id": e.equipment_id,
            "iri": e.iri,
            "label": e.label,
            "equipment_class_iri": e.equipment_class_iri,
            "workshop_code": e.workshop_code,
            "data_properties": e.data_properties,
        }
        for e in items
    ]


@router.post("/equipment")
def create_equipment(equip: EquipmentSchema, db: Session = Depends(get_db)):
    exists = db.query(MockEquipment).filter_by(equipment_id=equip.equipment_id).first()
    if exists:
        raise HTTPException(409, f"Equipment with ID '{equip.equipment_id}' already exists")

    new_equip = MockEquipment(
        equipment_id=equip.equipment_id,
        iri=equip.iri,
        label=equip.label,
        equipment_class_iri=equip.equipment_class_iri,
        workshop_code=equip.workshop_code,
        data_properties=[dp.model_dump() for dp in equip.data_properties],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_equip)
    db.commit()
    db.refresh(new_equip)
    return {"id": str(new_equip.id), "equipment_id": new_equip.equipment_id}


@router.put("/equipment/{equip_id}")
def update_equipment(equip_id: UUID, equip: EquipmentSchema, db: Session = Depends(get_db)):
    existing = db.get(MockEquipment, equip_id)
    if not existing:
        raise HTTPException(404, "Equipment not found")

    existing.equipment_id = equip.equipment_id
    existing.iri = equip.iri
    existing.label = equip.label
    existing.equipment_class_iri = equip.equipment_class_iri
    existing.workshop_code = equip.workshop_code
    existing.data_properties = [dp.model_dump() for dp in equip.data_properties]
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "equipment_id": existing.equipment_id}


@router.delete("/equipment/{equip_id}")
def delete_equipment(equip_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockEquipment, equip_id)
    if not existing:
        raise HTTPException(404, "Equipment not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(equip_id)}


@router.get("/equipment-schedules", response_model=list[EquipmentScheduleSchema])
def list_equipment_schedules(
    equipment_id: str,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
):
    """返回指定设备在日期范围内的确定性 Mock 排期。"""

    equipment = db.query(MockEquipment).filter_by(equipment_id=equipment_id).first()
    if not equipment:
        raise HTTPException(404, "Equipment not found")
    try:
        schedules = generate_equipment_schedules(
            equipment_id=equipment.equipment_id,
            equipment_name=equipment.label,
            workshop_code=equipment.workshop_code,
            start_date=start_date,
            end_date=end_date,
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
            equipment_id=equipment.equipment_id,
            equipment_name=equipment.label,
            workshop_code=equipment.workshop_code,
            now=datetime.now(timezone.utc),
        )
        return [
            item
            for item in schedules
            if item["start_at"].date() <= end_date and item["end_at"].date() > start_date
        ]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put(
    "/equipment-schedules/occupancy",
    response_model=EquipmentScheduleOccupancyResponse,
)
def update_equipment_schedule_occupancy(
    update: EquipmentScheduleOccupancyUpdate,
    db: Session = Depends(get_db),
):
    """新增或替换设备某一天的 Mock 产品占用。"""

    if update.product_code not in EDITABLE_PRODUCTS:
        raise HTTPException(422, "Unsupported product code")
    equipment = db.query(MockEquipment).filter_by(equipment_id=update.equipment_id).first()
    if not equipment:
        raise HTTPException(404, "Equipment not found")
    override = (
        db.query(MockEquipmentScheduleOverride)
        .filter_by(equipment_id=update.equipment_id, schedule_date=update.schedule_date)
        .first()
    )
    if override is None:
        override = MockEquipmentScheduleOverride(
            equipment_id=update.equipment_id,
            schedule_date=update.schedule_date,
            product_code=update.product_code,
        )
        db.add(override)
    else:
        override.product_code = update.product_code
        override.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(override)
    return override


# --- Production Areas CRUD ----------------------------------------------------


@router.get("/production-areas")
def list_production_areas(db: Session = Depends(get_db)):
    items = db.query(MockProductionArea).all()
    return [
        {
            "id": str(a.id),
            "code": a.code,
            "iri": a.iri,
            "label": a.label,
            "description": a.description,
            "data_properties": a.data_properties,
        }
        for a in items
    ]


@router.post("/production-areas")
def create_production_area(area: ProductionAreaSchema, db: Session = Depends(get_db)):
    exists = db.query(MockProductionArea).filter_by(code=area.code).first()
    if exists:
        raise HTTPException(409, f"Production area with code '{area.code}' already exists")

    new_area = MockProductionArea(
        code=area.code,
        iri=area.iri,
        label=area.label,
        description=area.description,
        data_properties=[dp.model_dump() for dp in area.data_properties],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_area)
    db.commit()
    db.refresh(new_area)
    return {"id": str(new_area.id), "code": new_area.code}


@router.put("/production-areas/{area_id}")
def update_production_area(area_id: UUID, area: ProductionAreaSchema, db: Session = Depends(get_db)):
    existing = db.get(MockProductionArea, area_id)
    if not existing:
        raise HTTPException(404, "Production area not found")

    existing.code = area.code
    existing.iri = area.iri
    existing.label = area.label
    existing.description = area.description
    existing.data_properties = [dp.model_dump() for dp in area.data_properties]
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "code": existing.code}


@router.delete("/production-areas/{area_id}")
def delete_production_area(area_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockProductionArea, area_id)
    if not existing:
        raise HTTPException(404, "Production area not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(area_id)}


# --- Assessment Team CRUD -----------------------------------------------------


@router.get("/assessment-team")
def list_assessment_team(db: Session = Depends(get_db)):
    items = db.query(MockTeamMember).filter_by(team_type="assessment").all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "role_label": m.role_label,
            "department": m.department,
            "role_class_iri": m.role_class_iri,
            "role_code": m.role_code,
        }
        for m in items
    ]


@router.post("/assessment-team")
def create_assessment_team_member(member: TeamMemberSchema, db: Session = Depends(get_db)):
    exists = db.query(MockTeamMember).filter_by(
        team_type="assessment", role_code=member.role_code
    ).first()
    if exists:
        raise HTTPException(409, f"Assessment team member with role '{member.role_code}' already exists")

    new_member = MockTeamMember(
        team_type="assessment",
        name=member.name,
        role_label=member.role_label,
        department=member.department,
        role_class_iri=member.role_class_iri,
        role_code=member.role_code,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    return {"id": str(new_member.id), "role_code": new_member.role_code}


@router.put("/assessment-team/{member_id}")
def update_assessment_team_member(member_id: UUID, member: TeamMemberSchema, db: Session = Depends(get_db)):
    existing = db.get(MockTeamMember, member_id)
    if not existing or existing.team_type != "assessment":
        raise HTTPException(404, "Assessment team member not found")

    existing.name = member.name
    existing.role_label = member.role_label
    existing.department = member.department
    existing.role_class_iri = member.role_class_iri
    existing.role_code = member.role_code
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "role_code": existing.role_code}


@router.delete("/assessment-team/{member_id}")
def delete_assessment_team_member(member_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockTeamMember, member_id)
    if not existing or existing.team_type != "assessment":
        raise HTTPException(404, "Assessment team member not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(member_id)}


# --- Approver Team CRUD -------------------------------------------------------


@router.get("/approver-team")
def list_approver_team(db: Session = Depends(get_db)):
    items = db.query(MockTeamMember).filter_by(team_type="approver").all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "role_label": m.role_label,
            "department": m.department,
            "role_class_iri": m.role_class_iri,
            "role_code": m.role_code,
        }
        for m in items
    ]


@router.post("/approver-team")
def create_approver_team_member(member: TeamMemberSchema, db: Session = Depends(get_db)):
    exists = db.query(MockTeamMember).filter_by(
        team_type="approver", role_code=member.role_code
    ).first()
    if exists:
        raise HTTPException(409, f"Approver team member with role '{member.role_code}' already exists")

    new_member = MockTeamMember(
        team_type="approver",
        name=member.name,
        role_label=member.role_label,
        department=member.department,
        role_class_iri=member.role_class_iri,
        role_code=member.role_code,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    return {"id": str(new_member.id), "role_code": new_member.role_code}


@router.put("/approver-team/{member_id}")
def update_approver_team_member(member_id: UUID, member: TeamMemberSchema, db: Session = Depends(get_db)):
    existing = db.get(MockTeamMember, member_id)
    if not existing or existing.team_type != "approver":
        raise HTTPException(404, "Approver team member not found")

    existing.name = member.name
    existing.role_label = member.role_label
    existing.department = member.department
    existing.role_class_iri = member.role_class_iri
    existing.role_code = member.role_code
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": str(existing.id), "role_code": existing.role_code}


@router.delete("/approver-team/{member_id}")
def delete_approver_team_member(member_id: UUID, db: Session = Depends(get_db)):
    existing = db.get(MockTeamMember, member_id)
    if not existing or existing.team_type != "approver":
        raise HTTPException(404, "Approver team member not found")
    db.delete(existing)
    db.commit()
    return {"deleted": str(member_id)}
