"""Mock 评估小组主数据事实源 —— 风险评估报告「评估小组成员」A-Box 尚未对接的 interim 替身。

风险评估报告（``RiskAssessmentReport hasAssessmentTeam AssessmentTeam``）需列出评估小组
成员会签表：每位成员对应一个 GxP 评审角色（QA、仓储管理、EHS评估、生产工艺评估、设备评估）。
这些成员个体尚未接入本系统（A-Box 不在库），故本模块 **mock 一个外部评估小组主数据 API**：
复用 :mod:`app.services.extraction.role_source` 已有的 5 个 GxP 角色，为每个角色合成一名
稳定的评估小组成员（姓名 + 归属部门 + 本体角色类 IRI）。

真实内网 OA/HR 主数据 API 接入时，改由 ``app/services/integration`` 的 RestConnector
支撑，只需替换 :func:`get_assessment_team_source` 返回的实现（本 :class:`AssessmentTeamSource`
Protocol 即契约）——调用方（016 事实源 provider）零改动。

离线安全（Principle VI 优雅降级）：角色源不可用 / 为空 → :meth:`list_members` 返回 ``[]``，
调用方跳过、绝不抛出。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services.extraction.role_source import RoleFact, get_role_source


@dataclass(frozen=True)
class AssessmentTeamMemberFact:
    """外部事实源返回的单个评估小组成员事实。"""

    name: str
    role_label: str
    department: str
    role_class_iri: str
    role_code: str = ""


class AssessmentTeamSource(Protocol):
    """评估小组主数据事实源契约。"""

    def list_members(self) -> list[AssessmentTeamMemberFact]:
        """列出评估小组全部成员（源不可用 / 为空 → ``[]``）。"""
        ...


# 稳定的合成成员姓名，按 GxP 角色代码映射（deterministic —— 无随机）。角色代码未命中时
# 回退到角色标签本身，保证任何角色都能产出一名成员。
_MEMBER_NAMES: dict[str, str] = {
    "QA": "王玉华",
    "WH": "刘建国",
    "EHS": "陈志强",
    "PPA": "张伟民",
    "ENG": "李国栋",
}


def _department_of(role: RoleFact) -> str:
    """从角色事实的 data_properties 中取「归属部门」。"""
    for dp in role.data_properties or []:
        if dp.get("label") == "归属部门" and dp.get("value"):
            return str(dp["value"])
    return ""


def _member_from_role(role: RoleFact) -> AssessmentTeamMemberFact:
    return AssessmentTeamMemberFact(
        name=_MEMBER_NAMES.get(role.code, role.label),
        role_label=role.label,
        department=_department_of(role),
        role_class_iri=role.role_class_iri,
        role_code=role.code,
    )


def _db_members() -> list[AssessmentTeamMemberFact]:
    """从 ``mock_team_members`` 表读取 ``team_type='assessment'`` 成员（Mock 管理页可实时编辑）。

    表已 seed → 返回其中的行（用户在 Mock 管理页的增/改/删在此立即生效、重启后保留）；表为空 /
    表不存在 / DB 不可用 → ``[]``，由调用方回退到 canned 名册。按 ``created_at`` 稳定排序，近似
    seed 时的角色顺序（编辑只改 ``updated_at``，不扰动顺序）。延迟导入 + 全捕获异常，保证本模块在
    无 DB 的气隙/单测环境仍可导入与降级。"""
    try:
        from app.db import SessionLocal
        from app.models.mock_data import MockTeamMember

        db = SessionLocal()
        try:
            rows = (
                db.query(MockTeamMember)
                .filter_by(team_type="assessment")
                .order_by(MockTeamMember.created_at, MockTeamMember.role_code)
                .all()
            )
            return [
                AssessmentTeamMemberFact(
                    name=r.name,
                    role_label=r.role_label,
                    department=r.department,
                    role_class_iri=r.role_class_iri,
                    role_code=r.role_code,
                )
                for r in rows
            ]
        finally:
            db.close()
    except Exception:  # pragma: no cover - defensive; DB 不可用/无表绝不抛出
        return []


class MockAssessmentTeamSource:
    """评估小组主数据（mock 外部 API）。

    优先读 ``mock_team_members`` 表（Mock 管理页可编辑、持久化、**改即生效**）；表为空 / 不可用时
    回退到 canned 名册（复用 5 个 GxP 角色 + :data:`_MEMBER_NAMES`），保证未 seed / 气隙环境仍有值。
    角色源亦为空 → ``[]``（优雅降级）。"""

    def list_members(self) -> list[AssessmentTeamMemberFact]:
        members = _db_members()
        if members:
            return members
        try:
            roles = get_role_source().list_all()
        except Exception:  # pragma: no cover - defensive; 源不可用绝不抛出
            return []
        return [_member_from_role(r) for r in roles]


def get_assessment_team_source() -> AssessmentTeamSource:
    """返回当前生效的评估小组主数据事实源。

    现为 mock（评估小组成员 A-Box 尚未对接，复用 GxP 角色）；真实内网 OA/HR 主数据 API
    接入时在此切换为 RestConnector 支撑的实现即可，调用方无需改动。
    """
    return MockAssessmentTeamSource()
