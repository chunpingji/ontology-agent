"""Mock 审批人小组主数据事实源 —— 风险评估报告「审批人小组成员」A-Box 尚未对接的 interim 替身。

风险评估报告（``RiskAssessmentReport hasApproverTeam ApproverTeam``）需列出审批人小组
会签表：每位成员对应一个具审批权限的 GxP 角色（QA 审核、企业管理层批准、上市许可持有人批准）。
这些审批角色属 personnel 本体的 accountability 层（``MAHRole`` / ``ManagementRole`` /
``QualityAssuranceRole``），不在 :mod:`app.services.extraction.role_source` 的 5 个职能角色
mock 名册内，故本模块 **自带一份确定性审批人名册**（而非复用评估小组的 5 角色）：每位审批人
映射到 personnel 本体的角色类 IRI，稳定标识（姓名 + 归属 + 角色类 IRI）。

真实内网 OA/HR 主数据 API 接入时，改由 ``app/services/integration`` 的 RestConnector
支撑，只需替换 :func:`get_approver_team_source` 返回的实现（本 :class:`ApproverTeamSource`
Protocol 即契约）——调用方（016 产品报告 edge finder）零改动。

离线安全（Principle VI 优雅降级）：源不可用 / 为空 → :meth:`list_members` 返回 ``[]``，
调用方跳过、绝不抛出。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


@dataclass(frozen=True)
class ApproverTeamMemberFact:
    """外部事实源返回的单个审批人小组成员事实。"""

    name: str
    role_label: str
    department: str
    role_class_iri: str
    role_code: str = ""


class ApproverTeamSource(Protocol):
    """审批人小组主数据事实源契约。"""

    def list_members(self) -> list[ApproverTeamMemberFact]:
        """列出审批人小组全部成员（源不可用 / 为空 → ``[]``）。"""
        ...


# 稳定的合成审批人名册（deterministic —— 无随机）。审批权限角色取 personnel 本体的
# accountability 层 + QA 审核。姓名刻意区别于评估小组，避免会签表混淆。
# (角色标签, personnel 角色类 local-name, 归属, 角色代码, 姓名)
_APPROVERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("质量保证审核", "QualityAssuranceRole", "质量部", "qa", "周慧敏"),
    ("企业管理层批准", "ManagementRole", "管理层", "mgmt", "赵明远"),
    ("上市许可持有人批准", "MAHRole", "上市许可持有人", "mah", "孙立群"),
)


def _canned_members() -> list[ApproverTeamMemberFact]:
    """自带的确定性审批人名册（:data:`_APPROVERS`），DB 未 seed / 不可用时的回退。"""
    return [
        ApproverTeamMemberFact(
            name=name,
            role_label=role_label,
            department=department,
            role_class_iri=f"{_PERS_NS}{role_local}",
            role_code=role_code,
        )
        for role_label, role_local, department, role_code, name in _APPROVERS
    ]


def _db_members() -> list[ApproverTeamMemberFact]:
    """从 ``mock_team_members`` 表读取 ``team_type='approver'`` 成员（Mock 管理页可实时编辑）。

    表已 seed → 返回其中的行（Mock 管理页的增/改/删在此立即生效、重启后保留）；表为空 / 表不存在 /
    DB 不可用 → ``[]``，由调用方回退到 canned 名册。按 ``created_at`` 稳定排序。延迟导入 + 全捕获
    异常，保证本模块在无 DB 的气隙/单测环境仍可导入与降级。"""
    try:
        from app.db import SessionLocal
        from app.models.mock_data import MockTeamMember

        db = SessionLocal()
        try:
            rows = (
                db.query(MockTeamMember)
                .filter_by(team_type="approver")
                .order_by(MockTeamMember.created_at, MockTeamMember.role_code)
                .all()
            )
            return [
                ApproverTeamMemberFact(
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


class MockApproverTeamSource:
    """审批人小组主数据（mock 外部 API）。

    优先读 ``mock_team_members`` 表（Mock 管理页可编辑、持久化、**改即生效**）；表为空 / 不可用时
    回退到自带的 canned 审批人名册 :data:`_APPROVERS`。名册为空 → ``[]``（优雅降级）。"""

    def list_members(self) -> list[ApproverTeamMemberFact]:
        return _db_members() or _canned_members()


def get_approver_team_source() -> ApproverTeamSource:
    """返回当前生效的审批人小组主数据事实源。

    现为 mock（审批人小组成员 A-Box 尚未对接，自带确定性名册）；真实内网 OA/HR 主数据 API
    接入时在此切换为 RestConnector 支撑的实现即可，调用方无需改动。
    """
    return MockApproverTeamSource()
