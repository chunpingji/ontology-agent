"""Mock 外部部门主数据事实源 —— Department A-Box 尚未对接的 interim 替身。

药品生产企业的内部职能部门（如质量部、仓储部、设备部、生产车间等）个体尚未接入本系统
（A-Box 不在库、无内网 OA/HR 主数据 API 对接）。抽取期关系 finder 需把文档里的部门名
映射为 ``Department`` 端点，故本模块 **mock 一个外部部门主数据 API**：给定部门代码，
返回该部门的稳定标识（合成 A-Box IRI）、名称与若干属性。

真实内网 OA/HR 主数据 API 接入时，改由 ``app/services/integration`` 的 RestConnector
支撑，只需替换 :func:`get_department_source` 返回的实现（本 :class:`DepartmentSource`
Protocol 即契约）——调用方 finder 零改动。

离线安全（Principle VI 优雅降级）：未知部门代码 / 源不可用 → :meth:`resolve` 返回
``None``，调用方跳过、绝不抛出。合成 IRI 沿用物化器「个体永不入 TTL、``facts#<id>``」
约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

_FACTS_NS = "http://slpra.org/facts#"
_PERS_NS = "https://ontology.pharma-gmp.cn/slpra/personnel/"


@dataclass(frozen=True)
class DepartmentFact:
    """外部事实源返回的单个部门事实。"""

    code: str
    iri: str
    label: str
    description: str = ""
    data_properties: list[dict] = field(default_factory=list)


class DepartmentSource(Protocol):
    """部门主数据事实源契约。"""

    def resolve(self, code: str) -> DepartmentFact | None:
        """按部门代码解析为 Department 事实（未知 → ``None``）。"""
        ...

    def list_all(self) -> list[DepartmentFact]:
        """列出全部已知部门。"""
        ...


def _dp(label: str, value: str) -> dict:
    return {"iri": None, "label": label, "value": value}


def _fact(
    code: str,
    label: str,
    *,
    full_name: str = "",
    responsibility: str = "",
    gxp_roles: str = "",
    description: str = "",
) -> DepartmentFact:
    dps = [
        _dp("部门代码", code),
        _dp("部门名称", label),
    ]
    if full_name:
        dps.append(_dp("部门全称", full_name))
    if responsibility:
        dps.append(_dp("核心职责", responsibility))
    if gxp_roles:
        dps.append(_dp("关联GxP角色", gxp_roles))
    return DepartmentFact(
        code=code,
        iri=f"{_FACTS_NS}department-{code}",
        label=label,
        description=description,
        data_properties=dps,
    )


_CANNED: dict[str, DepartmentFact] = {
    "QA": _fact(
        "QA",
        "质量部",
        full_name="质量管理部",
        responsibility="质量保证（QA）与质量控制（QC）；审核批准风险评估报告与清洁验证报告，"
        "变更控制，偏差调查与纠正预防；开发并验证残留检测方法（HPLC/TOC 等），取样回收率研究",
        gxp_roles="qa, qc, cv",
        description="质量部负责企业药品生产全过程的质量保证与质量控制工作，"
        "涵盖质量体系管理、偏差处理、变更控制、清洁验证方案审批及残留检测方法开发与验证。",
    ),
    "WH": _fact(
        "WH",
        "仓储部",
        full_name="仓储管理部",
        responsibility="原辅料与成品仓储管理，物料收发与台账，防止物料交叉污染与混淆",
        gxp_roles="operator",
        description="仓储部负责原辅料、中间体及成品的仓储管理，"
        "确保物料标识清晰、存储条件合规、先进先出，防止物料交叉污染与混淆。",
    ),
    "EHS": _fact(
        "EHS",
        "EHS",
        full_name="环境、职业健康与安全部",
        responsibility="高毒/高活/激素类/细胞毒产品的人员防护与职业暴露限值（OEL）控制，"
        "环境排放管理，安全生产监督",
        gxp_roles="ehs",
        description="EHS 部门负责企业环境保护、职业健康防护和安全生产管理，"
        "尤其关注高毒、高活、激素类及细胞毒产品生产中的人员防护与职业暴露限值（OEL）控制。",
    ),
    "ENG": _fact(
        "ENG",
        "设备部",
        full_name="设备工程部",
        responsibility="设备选型与可清洁性设计，预防性维护，设备确认与再确认，"
        "管道气密性测试、阀门点检、快接口检查",
        gxp_roles="eng, hvac",
        description="设备部负责生产设备的选型、安装确认、运行确认、性能确认及日常预防性维护，"
        "确保设备设计便于清洁，公用系统（HVAC、水系统、压缩空气等）持续处于验证状态。",
    ),
    "WS": _fact(
        "WS",
        "生产车间",
        full_name="生产车间",
        responsibility="药品生产工艺的一线执行，设备操作与清洁，生产批记录填写，"
        "更衣与人流/物流管理",
        gxp_roles="operator",
        description="生产车间是药品生产的一线执行单元，负责按批生产指令和标准操作规程（SOP）"
        "执行生产操作、设备清洁及批记录填写。",
    ),
    "PD1": _fact(
        "PD1",
        "生产一部",
        full_name="生产管理一部",
        responsibility="原料药生产计划编排与执行管理，生产调度，工艺参数控制与偏差处理",
        gxp_roles="operator, mgmt",
        description="生产一部负责原料药生产线的计划编排、生产调度与过程管理，"
        "协调车间资源确保生产计划按时完成。",
    ),
    "PD2": _fact(
        "PD2",
        "生产二部",
        full_name="生产管理二部",
        responsibility="制剂/成品生产计划编排与执行管理，生产调度，工艺参数控制与偏差处理",
        gxp_roles="operator, mgmt",
        description="生产二部负责制剂/成品生产线的计划编排、生产调度与过程管理，"
        "协调车间资源确保生产计划按时完成。",
    ),
}


class MockDepartmentSource:
    """内存 canned 部门主数据（mock 外部 API）；未知部门代码 → ``None``（优雅降级）。"""

    def resolve(self, code: str) -> DepartmentFact | None:
        return _CANNED.get((code or "").strip().upper())

    def list_all(self) -> list[DepartmentFact]:
        return list(_CANNED.values())


def get_department_source() -> DepartmentSource:
    """返回当前生效的部门主数据事实源。

    现为 mock（Department 尚未对接）；真实内网 OA/HR 主数据 API 接入时在此切换为
    RestConnector 支撑的实现即可，调用方 finder 无需改动。
    """
    return MockDepartmentSource()
