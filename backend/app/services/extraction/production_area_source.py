"""Mock 外部车间主数据事实源 —— ProductionArea A-Box 尚未对接的 interim 替身。

设施域的 ``ProductionArea``（生产车间/区域）个体尚未接入本系统（A-Box 不在库、无内网
设施主数据 API 对接）。抽取期关系 finder 需把文档里的车间号（如「642/646车间」）映射为
``ProductionArea`` 端点，故本模块 **mock 一个外部车间主数据 API**：给定车间号，返回该车间
的稳定标识（合成 A-Box IRI）、名称与若干设施属性。

真实内网设施 API 接入时，改由 ``app/services/integration`` 的 RestConnector 支撑，只需替换
:func:`get_production_area_source` 返回的实现（本 :class:`ProductionAreaSource` Protocol 即
契约）——调用方 finder 零改动。

离线安全（Principle VI 优雅降级）：未知车间号 / 源不可用 → :meth:`resolve` 返回 ``None``，
调用方跳过该车间、绝不抛出。合成 IRI 沿用物化器「个体永不入 TTL、``facts#<id>``」约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# 合成 A-Box 命名空间（个体永不入 TTL；与物化器一致）。
_FACTS_NS = "http://slpra.org/facts#"


@dataclass(frozen=True)
class ProductionAreaFact:
    """外部事实源返回的单个生产车间事实（``data_properties`` 为 ``{iri,label,value}`` 列表）。"""

    code: str
    iri: str
    label: str
    description: str = ""
    data_properties: list[dict] = field(default_factory=list)


class ProductionAreaSource(Protocol):
    """车间主数据事实源契约：按车间号解析为 ProductionArea 事实（未知 → ``None``）。"""

    def resolve(self, code: str) -> ProductionAreaFact | None: ...


def _dp(label: str, value: str) -> dict:
    return {"iri": None, "label": label, "value": value}


def _fact(
    code: str,
    *,
    clean_zones: str,
    building: str,
    description: str = "",
    dept_code: str = "",
    purpose: str = "",
    product_type: str = "",
    qualification_year: str = "",
    utilities: str = "",
    applicable_products: str = "",
    address: str = "",
) -> ProductionAreaFact:
    dps = [
        _dp("车间编号", code),
        _dp("洁净区设置", clean_zones),
        _dp("所属厂房", building),
    ]
    if dept_code:
        dps.append(_dp("部门代码", dept_code))
    if purpose:
        dps.append(_dp("车间用途", purpose))
    if product_type:
        dps.append(_dp("产品类型", product_type))
    if qualification_year:
        dps.append(_dp("厂房设施确认年份", qualification_year))
    if utilities:
        dps.append(_dp("配套系统", utilities))
    if applicable_products:
        dps.append(_dp("适用品种", applicable_products))
    if address:
        dps.append(_dp("地址", address))
    return ProductionAreaFact(
        code=code,
        iri=f"{_FACTS_NS}production-area-{code}",
        label=f"{code}车间",
        description=description,
        data_properties=dps,
    )


_UTILS = "普冷、氮气、压缩空气、循环水、纯化水、蒸汽"
_PURPOSE = "非细胞毒临床产品备样专用车间"
_PRODUCTS = "HRS-1234/HRS-5678/HRS-9267"

# 车间号 → 事实（mock 示例值；真实内网源接入后由 RestConnector 提供）。
_CANNED: dict[str, ProductionAreaFact] = {
    "642": _fact(
        "642",
        clean_zones="一般区 / D级洁净区",
        building="原料药一厂房",
        dept_code="222",
        purpose=_PURPOSE,
        product_type="非无菌原料药",
        qualification_year="2020",
        utilities=_UTILS,
        applicable_products=_PRODUCTS,
        address="XXXX医药股份有限公司原料药分公司）",
        description=(
            "642 车间位于XXXX医药股份有限公司原料药分公司。"
            "642 车间部门代码为 222，为非细胞毒临床产品备样专用车间，"
            "用于生产非无菌原料药，已于 2020 年完成厂房设施确认。"
            "642 车间设有一般区和 D 级洁净区，"
            "车间配套普冷、氮气、压缩空气、循环水、纯化水、蒸汽等系统，"
            "且各系统均在验证周期内，"
            "可以满足 HRS-1234/HRS-5678/HRS-9267 原料药的生产需求。"
        ),
    ),
    "646": _fact(
        "646",
        clean_zones="一般区",
        building="原料药一厂房",
        dept_code="226",
        purpose=_PURPOSE,
        product_type="非无菌原料药",
        qualification_year="2020",
        utilities=_UTILS,
        applicable_products=_PRODUCTS,
        description=(
            "646 车间部门代码为 226，为非细胞毒临床产品备样专用车间，"
            "用于生产非无菌原料药，已于 2020 年完成厂房设施确认。"
            "646 车间仅设有一般区，"
            "车间配套普冷、氮气、压缩空气、循环水、纯化水、蒸汽等系统，"
            "且各系统均在验证周期内，"
            "可以满足 HRS-1234/HRS-5678/HRS-9267 的生产需求。"
        ),
    ),
    "644": _fact("644", clean_zones="C级洁净区", building="原料药一厂房"),
}


class MockProductionAreaSource:
    """内存 canned 车间主数据（mock 外部 API）；未知车间号 → ``None``（优雅降级）。"""

    def resolve(self, code: str) -> ProductionAreaFact | None:
        return _CANNED.get((code or "").strip())


def get_production_area_source() -> ProductionAreaSource:
    """返回当前生效的车间主数据事实源。

    现为 mock（ProductionArea 尚未对接）；真实内网设施 API 接入时在此切换为
    RestConnector 支撑的实现即可，调用方 finder 无需改动。
    """
    return MockProductionAreaSource()
