"""Mock 外部角色主数据事实源 —— GxP 评审角色 A-Box 尚未对接的 interim 替身。

共线生产风险评估涉及多个专业角色（QA、仓储管理、EHS评估、生产工艺评估、设备评估）的
会签与评审。这些角色个体尚未接入本系统（A-Box 不在库），抽取期关系 finder 需把文档里
的角色名映射为 ``GxPRole`` 端点，故本模块 **mock 一个外部角色主数据 API**：给定角色
代码，返回该角色的稳定标识（合成 A-Box IRI）、名称、本体角色类 IRI 与若干属性。

真实内网 OA/HR 主数据 API 接入时，改由 ``app/services/integration`` 的 RestConnector
支撑，只需替换 :func:`get_role_source` 返回的实现（本 :class:`RoleSource` Protocol 即
契约）——调用方 finder 零改动。

离线安全（Principle VI 优雅降级）：未知角色代码 / 源不可用 → :meth:`resolve` 返回
``None``，调用方跳过、绝不抛出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

_FACTS_NS = "http://slpra.org/facts#"
_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


@dataclass(frozen=True)
class RoleFact:
    """外部事实源返回的单个角色事实。"""

    code: str
    iri: str
    label: str
    role_class_iri: str
    description: str = ""
    data_properties: list[dict] = field(default_factory=list)


class RoleSource(Protocol):
    """角色主数据事实源契约。"""

    def resolve(self, code: str) -> RoleFact | None:
        """按角色代码解析为 GxPRole 事实（未知 → ``None``）。"""
        ...

    def list_all(self) -> list[RoleFact]:
        """列出全部已知角色。"""
        ...


def _dp(label: str, value: str) -> dict:
    return {"iri": None, "label": label, "value": value}


def _fact(
    code: str,
    label: str,
    role_class_local: str,
    *,
    responsibility: str = "",
    department: str = "",
    description: str = "",
) -> RoleFact:
    dps = [
        _dp("角色代码", code),
        _dp("角色名称", label),
    ]
    if responsibility:
        dps.append(_dp("核心职责", responsibility))
    if department:
        dps.append(_dp("归属部门", department))
    return RoleFact(
        code=code,
        iri=f"{_FACTS_NS}role-{code}",
        label=label,
        role_class_iri=f"{_PERS_NS}{role_class_local}",
        description=description,
        data_properties=dps,
    )


_CANNED: dict[str, RoleFact] = {
    "QA": _fact(
        "QA",
        "QA",
        "QualityAssuranceRole",
        responsibility="审核批准共线生产风险评估报告与清洁验证报告，"
        "变更控制，偏差调查与纠正预防",
        department="质量部",
        description="QA 角色负责共线生产全过程的质量保证，"
        "审核并批准风险评估报告、清洁验证方案及报告，"
        "对变更控制和偏差调查进行管理与审批。",
    ),
    "WH": _fact(
        "WH",
        "仓储管理",
        "WarehouseManagementRole",
        responsibility="共线品种原辅料与成品仓储管理，物料标识隔离、专区存放，"
        "防止物料交叉污染与混淆",
        department="仓储部",
        description="仓储管理角色负责共线生产相关物料的仓储控制，"
        "确保共线品种物料标识清晰、专区存放、先进先出，"
        "防止不同品种物料之间的交叉污染与混淆。",
    ),
    "EHS": _fact(
        "EHS",
        "EHS评估",
        "EHSRole",
        responsibility="高毒/高活/激素类/细胞毒产品的人员防护评估，"
        "职业暴露限值（OEL）控制措施制定与验证",
        department="EHS",
        description="EHS 评估角色负责评估共线生产中高毒、高活、激素类及细胞毒产品"
        "对操作人员的职业健康风险，制定人员防护措施与职业暴露限值（OEL）控制方案。",
    ),
    "PPA": _fact(
        "PPA",
        "生产工艺评估",
        "ProductionProcessAssessmentRole",
        responsibility="共线生产工艺路线可行性与交叉污染风险评估，"
        "工艺参数、操作步骤、中间体暴露环节、批次切换清场等风险识别与控制",
        department="生产一部",
        description="生产工艺评估角色负责评估共线生产工艺路线的可行性，"
        "识别工艺参数、操作步骤、中间体暴露环节及批次切换清场等环节的"
        "交叉污染风险，并制定相应控制措施。",
    ),
    "ENG": _fact(
        "ENG",
        "设备评估",
        "EquipmentEngineeringRole",
        responsibility="共线设备可清洁性评估，设备设计合理性审查，"
        "设备确认与再确认，预防性维护计划评估",
        department="设备部",
        description="设备评估角色负责评估共线设备的设计合理性与可清洁性，"
        "审查设备确认状态及预防性维护计划，确保共线设备满足清洁验证要求。",
    ),
}


class MockRoleSource:
    """内存 canned 角色主数据（mock 外部 API）；未知角色代码 → ``None``（优雅降级）。"""

    def resolve(self, code: str) -> RoleFact | None:
        return _CANNED.get((code or "").strip().upper())

    def list_all(self) -> list[RoleFact]:
        return list(_CANNED.values())


def get_role_source() -> RoleSource:
    """返回当前生效的角色主数据事实源。

    现为 mock（GxPRole 尚未对接）；真实内网 OA/HR 主数据 API 接入时在此切换为
    RestConnector 支撑的实现即可，调用方 finder 无需改动。
    """
    return MockRoleSource()
