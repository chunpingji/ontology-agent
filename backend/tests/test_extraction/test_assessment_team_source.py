"""Mock 评估小组主数据事实源单测（评估小组成员 A-Box 尚未对接的 interim 替身）。

契约：复用 5 个 GxP 角色，为每个角色合成一名评估小组成员（姓名 + 角色 + 部门 +
本体角色类 IRI）；``list_members`` 列出全部成员；角色源为空/不可用 → ``[]``（优雅降级）。
"""

from __future__ import annotations

from app.services.extraction.assessment_team_source import (
    AssessmentTeamMemberFact,
    MockAssessmentTeamSource,
    get_assessment_team_source,
)

_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


def test_factory_returns_mock():
    assert isinstance(get_assessment_team_source(), MockAssessmentTeamSource)


def test_list_members_one_per_gxp_role():
    members = get_assessment_team_source().list_members()
    assert len(members) == 5
    assert all(isinstance(m, AssessmentTeamMemberFact) for m in members)


def test_members_reuse_gxp_roles():
    labels = {m.role_label for m in get_assessment_team_source().list_members()}
    assert labels == {"QA", "仓储管理", "EHS评估", "生产工艺评估", "设备评估"}


def test_member_carries_role_class_and_department():
    by_role = {m.role_label: m for m in get_assessment_team_source().list_members()}
    qa = by_role["QA"]
    assert qa.role_class_iri == f"{_PERS_NS}QualityAssuranceRole"
    assert qa.department == "质量部"
    assert qa.name  # a synthesized member name is present


def test_member_names_are_deterministic_and_unique():
    members = get_assessment_team_source().list_members()
    names = [m.name for m in members]
    assert all(n for n in names)  # never blank
    assert len(names) == len(set(names))  # 5 distinct members
    # stable across calls (no randomness) — supports golden-master reports
    again = [m.name for m in get_assessment_team_source().list_members()]
    assert names == again


def test_role_source_unavailable_degrades_to_empty(monkeypatch):
    def _boom():
        raise RuntimeError("role source down")

    monkeypatch.setattr(
        "app.services.extraction.assessment_team_source.get_role_source", _boom
    )
    assert MockAssessmentTeamSource().list_members() == []


# --------------------------------------------------------------------------- #
# DB-first read path — Mock 管理页编辑写入 mock_team_members，改即生效（bug ① 回归）
# --------------------------------------------------------------------------- #

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.mock_data import MockTeamMember


def _sessionmaker_seeded(*rows: MockTeamMember):
    """An in-memory-SQLite ``sessionmaker`` seeded with ``mock_team_members`` rows,
    standing in for the live ``app.db.SessionLocal`` so the source's DB-first read
    path is exercised offline (no Postgres, no conftest DB coupling)."""
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


def test_db_row_overrides_canned_name(monkeypatch):
    """Bug ①: after the Mock 管理页 edits QA 姓名 → 「王玉」 (written to mock_team_members),
    ``list_members`` reads the DB and returns 「王玉」 — NOT the canned 「王玉华」 that the
    行文预览 used to keep showing."""
    Sm = _sessionmaker_seeded(
        MockTeamMember(
            team_type="assessment", name="王玉", role_label="QA", department="质量部",
            role_class_iri=f"{_PERS_NS}QualityAssuranceRole", role_code="QA",
        ),
    )
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    members = MockAssessmentTeamSource().list_members()
    assert [m.name for m in members] == ["王玉"]
    assert all(m.name != "王玉华" for m in members)  # canned name no longer surfaces


def test_db_read_filters_by_team_type(monkeypatch):
    """The shared ``mock_team_members`` table holds both teams; the assessment source
    returns ONLY ``team_type='assessment'`` rows — approvers never leak in (bug ②)."""
    Sm = _sessionmaker_seeded(
        MockTeamMember(
            team_type="assessment", name="王玉", role_label="QA", department="质量部",
            role_class_iri=f"{_PERS_NS}QualityAssuranceRole", role_code="QA",
        ),
        MockTeamMember(
            team_type="approver", name="孙立群", role_label="上市许可持有人批准",
            department="上市许可持有人", role_class_iri=f"{_PERS_NS}MAHRole", role_code="mah",
        ),
    )
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    names = [m.name for m in MockAssessmentTeamSource().list_members()]
    assert names == ["王玉"]
    assert "孙立群" not in names


def test_empty_db_falls_back_to_canned(monkeypatch):
    """Table present but empty → graceful fallback to the canned 5-role roster
    (offline / not-yet-seed 环境仍有值)."""
    Sm = _sessionmaker_seeded()  # table created, zero rows
    monkeypatch.setattr("app.db.SessionLocal", Sm)
    members = MockAssessmentTeamSource().list_members()
    assert len(members) == 5
    assert any(m.name == "王玉华" for m in members)
