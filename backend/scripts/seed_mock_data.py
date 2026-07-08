"""初始化 mock 数据表 —— 从 canned 数据源导入到数据库。

运行：cd backend && uv run python scripts/seed_mock_data.py

幂等性：若数据库已有记录（按 code/equipment_id 判重），则跳过；否则插入默认 canned 数据。
"""

from datetime import datetime, timezone

from app.db import SessionLocal
from app.models.mock_data import (
    MockDepartment,
    MockRole,
    MockEquipment,
    MockProductionArea,
    MockTeamMember,
)
from app.services.extraction.department_source import get_department_source
from app.services.extraction.role_source import get_role_source
from app.services.extraction.equipment_source import get_equipment_source
from app.services.extraction.production_area_source import get_production_area_source
from app.services.extraction.assessment_team_source import get_assessment_team_source
from app.services.extraction.approver_team_source import get_approver_team_source


def seed_departments(db):
    src = get_department_source()
    facts = src.list_all()
    count = 0
    for f in facts:
        exists = db.query(MockDepartment).filter_by(code=f.code).first()
        if not exists:
            db.add(MockDepartment(
                code=f.code,
                iri=f.iri,
                label=f.label,
                description=f.description,
                data_properties=f.data_properties,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1
    db.commit()
    print(f"✓ Departments: {count} inserted, {len(facts) - count} already exist")


def seed_roles(db):
    src = get_role_source()
    facts = src.list_all()
    count = 0
    for f in facts:
        exists = db.query(MockRole).filter_by(code=f.code).first()
        if not exists:
            db.add(MockRole(
                code=f.code,
                iri=f.iri,
                label=f.label,
                role_class_iri=f.role_class_iri,
                description=f.description,
                data_properties=f.data_properties,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1
    db.commit()
    print(f"✓ Roles: {count} inserted, {len(facts) - count} already exist")


def seed_equipment(db):
    src = get_equipment_source()
    all_facts = []
    for wc in ("642", "646"):
        all_facts.extend(src.list_by_workshop(wc))
    count = 0
    for f in all_facts:
        exists = db.query(MockEquipment).filter_by(equipment_id=f.equipment_id).first()
        if not exists:
            db.add(MockEquipment(
                equipment_id=f.equipment_id,
                iri=f.iri,
                label=f.label,
                equipment_class_iri=f.equipment_class_iri,
                workshop_code=f.workshop_code,
                data_properties=f.data_properties,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1
    db.commit()
    print(f"✓ Equipment: {count} inserted, {len(all_facts) - count} already exist")


def seed_production_areas(db):
    src = get_production_area_source()
    codes = ["642", "646", "644"]
    count = 0
    for code in codes:
        f = src.resolve(code)
        if not f:
            continue
        exists = db.query(MockProductionArea).filter_by(code=f.code).first()
        if not exists:
            db.add(MockProductionArea(
                code=f.code,
                iri=f.iri,
                label=f.label,
                description=f.description,
                data_properties=f.data_properties,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1
    db.commit()
    print(f"✓ Production Areas: {count} inserted, {len(codes) - count} already exist")


def seed_team_members(db):
    assessment_src = get_assessment_team_source()
    approver_src = get_approver_team_source()

    assessment_members = assessment_src.list_members()
    approver_members = approver_src.list_members()

    count = 0
    for m in assessment_members:
        exists = db.query(MockTeamMember).filter_by(
            team_type="assessment", role_code=m.role_code
        ).first()
        if not exists:
            db.add(MockTeamMember(
                team_type="assessment",
                name=m.name,
                role_label=m.role_label,
                department=m.department,
                role_class_iri=m.role_class_iri,
                role_code=m.role_code,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1

    for m in approver_members:
        exists = db.query(MockTeamMember).filter_by(
            team_type="approver", role_code=m.role_code
        ).first()
        if not exists:
            db.add(MockTeamMember(
                team_type="approver",
                name=m.name,
                role_label=m.role_label,
                department=m.department,
                role_class_iri=m.role_class_iri,
                role_code=m.role_code,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            count += 1

    db.commit()
    total = len(assessment_members) + len(approver_members)
    print(f"✓ Team Members: {count} inserted, {total - count} already exist")


def main():
    print("Seeding mock data tables from canned sources...")
    db = SessionLocal()
    try:
        seed_departments(db)
        seed_roles(db)
        seed_equipment(db)
        seed_production_areas(db)
        seed_team_members(db)
        print("\n✓ Mock data seeding complete!")
    finally:
        db.close()


if __name__ == "__main__":
    main()
