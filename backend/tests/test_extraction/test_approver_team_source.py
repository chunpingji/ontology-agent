"""Mock 审批人小组主数据事实源单测（审批人小组成员 A-Box 尚未对接的 interim 替身）。

契约：自带一份确定性审批人名册（姓名 + 角色 + 部门 + personnel 本体角色类 IRI），审批权限
角色取 accountability 层（MAH/管理层）+ QA 审核；``list_members`` 列出全部成员，
deterministic（无随机，支撑 golden-master 报告）。
"""

from __future__ import annotations

from app.services.extraction.approver_team_source import (
    ApproverTeamMemberFact,
    MockApproverTeamSource,
    get_approver_team_source,
)

_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


def test_factory_returns_mock():
    assert isinstance(get_approver_team_source(), MockApproverTeamSource)


def test_list_members_nonempty_and_typed():
    members = get_approver_team_source().list_members()
    assert len(members) == 3
    assert all(isinstance(m, ApproverTeamMemberFact) for m in members)


def test_members_cover_approval_roles():
    labels = {m.role_label for m in get_approver_team_source().list_members()}
    assert labels == {"质量保证审核", "企业管理层批准", "上市许可持有人批准"}


def test_member_carries_personnel_role_class_iri():
    by_role = {m.role_label: m for m in get_approver_team_source().list_members()}
    assert by_role["质量保证审核"].role_class_iri == f"{_PERS_NS}QualityAssuranceRole"
    assert by_role["企业管理层批准"].role_class_iri == f"{_PERS_NS}ManagementRole"
    assert by_role["上市许可持有人批准"].role_class_iri == f"{_PERS_NS}MAHRole"
    assert all(m.department and m.name for m in by_role.values())


def test_member_names_are_deterministic_and_unique():
    names = [m.name for m in get_approver_team_source().list_members()]
    assert all(n for n in names)
    assert len(names) == len(set(names))
    again = [m.name for m in get_approver_team_source().list_members()]
    assert names == again


# --------------------------------------------------------------------------- #
# DB-first read path — Mock 管理页编辑写入 mock_team_members，改即生效（bug ① 回归）
# --------------------------------------------------------------------------- #

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.mock_data import MockTeamMember


def _sessionmaker_seeded(*rows: MockTeamMember):
    """In-memory-SQLite ``sessionmaker`` seeded with ``mock_team_members`` rows,
    standing in for the live ``app.db.SessionLocal`` so the source's DB-first read
    path is exercised offline."""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng, tables=[MockTeamMember.__table__])
    Sm = sessionmaker(bind=eng)
    db = Sm()
    try:
        db.add_all(rows)
        db.commit()
    finally:
        db.close()
    return Sm


def test_db_rows_override_canned(monkeypatch):
    """Bug ①: an edited approver written to mock_team_members surfaces via list_members
    (DB-first), replacing the canned 名册."""
    Sm = _sessionmaker_seeded(
        MockTeamMember(
            team_type="approver", name="新审批人", role_label="质量保证审核",
            department="质量部", role_class_iri=f"{_PERS_NS}QualityAssuranceRole", role_code="qa",
        ),
    )
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    members = MockApproverTeamSource().list_members()
    assert [m.name for m in members] == ["新审批人"]
    assert all(m.name not in {"周慧敏", "赵明远", "孙立群"} for m in members)


def test_db_read_filters_by_team_type(monkeypatch):
    """Approver source returns ONLY ``team_type='approver'`` rows — assessment members
    sharing the table never appear among approvers."""
    Sm = _sessionmaker_seeded(
        MockTeamMember(
            team_type="approver", name="周慧敏", role_label="质量保证审核",
            department="质量部", role_class_iri=f"{_PERS_NS}QualityAssuranceRole", role_code="qa",
        ),
        MockTeamMember(
            team_type="assessment", name="王玉", role_label="QA", department="质量部",
            role_class_iri=f"{_PERS_NS}QualityAssuranceRole", role_code="QA",
        ),
    )
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    names = [m.name for m in MockApproverTeamSource().list_members()]
    assert names == ["周慧敏"]
    assert "王玉" not in names


def test_empty_db_falls_back_to_canned(monkeypatch):
    """Table present but empty → canned approver roster (周慧敏/赵明远/孙立群)."""
    Sm = _sessionmaker_seeded()  # table created, zero rows
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    names = {m.name for m in MockApproverTeamSource().list_members()}
    assert names == {"周慧敏", "赵明远", "孙立群"}
