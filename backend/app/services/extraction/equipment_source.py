"""Mock 外部设备档案事实源 —— Equipment A-Box 尚未对接的 interim 替身。

设备域的 ``ProcessEquipment``（生产设备）个体尚未接入本系统（A-Box 不在库、无内网
设备主数据 API 对接）。抽取期关系 finder 需把文档里的设备编号（如「CT64201」）映射为
``Equipment`` 端点，故本模块 **mock 一个外部设备档案 API**：给定设备编号，返回该设备
的稳定标识（合成 A-Box IRI）、名称、本体类 IRI 与若干设备属性。

数据来源：``docs/642车间设备档案.xlsx`` 与 ``docs/646车间设备档案.xlsx`` 的「设备档案」
sheet，每条设备以唯一的「设备编号」标识；无编号的行被忽略。设备归属的车间由文件名中的
车间号（642/646）决定。

真实内网设备主数据 API 接入时，改由 ``app/services/integration`` 的 RestConnector 支撑，
只需替换 :func:`get_equipment_source` 返回的实现（本 :class:`EquipmentSource` Protocol 即
契约）——调用方 finder 零改动。

离线安全（Principle VI 优雅降级）：未知设备编号 / 源不可用 → :meth:`resolve` 返回
``None``，调用方跳过该设备、绝不抛出。合成 IRI 沿用物化器「个体永不入 TTL、
``facts#<id>``」约定。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterator, Protocol, Sequence

from app.services.reporting.source_ref import format_source_ref as _source_ref_text

logger = logging.getLogger(__name__)

_FACTS_NS = "http://slpra.org/facts#"
_EQUIP_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"

# 设备端点识别所需的谓词/类 IRI（供抽取期与报告期共用，避免各处重复漂移）。
EQUIPMENT_NS = _EQUIP_NS
EQUIPMENT_IRI = f"{_EQUIP_NS}Equipment"
PROCESS_EQUIPMENT_IRI = f"{_EQUIP_NS}ProcessEquipment"
USES_EQUIPMENT_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/usesEquipment"

# 档案标签 → 文档规范标签：外部档案用「规格型号/主体材质」，而模板槽位、覆盖校验与设备表
# 读取的是文档标签「设备规格/材质」（``设备名称`` 两侧同名）。富化时把档案值写入文档规范
# 标签（仅当文档侧为空），既有消费方遂零改动即可取到外部值——这是 材质=待补充 的根因修复。
_ARCHIVE_TO_DOC_LABEL: dict[str, str] = {
    "设备名称": "设备名称",
    "规格型号": "设备规格",
    "主体材质": "材质",
}
# 5 列设备一览表用不到、且会污染事实行的档案列，不并入 edge。
_SKIP_ARCHIVE_LABELS = ("安装位置", "是否洁净区", "设备编号")

# ---------------------------------------------------------------------------
# 设备名称 → 本体设备类 IRI（基于 slpra-equipment.ttl 定义）
# ---------------------------------------------------------------------------
_EQUIP_CLASS: dict[str, str] = {
    "离心机": f"{_EQUIP_NS}Centrifuge",
    "搪玻璃反应釜": f"{_EQUIP_NS}Reactor",
    "玻璃釜": f"{_EQUIP_NS}Reactor",
    "哈氏合金釜": f"{_EQUIP_NS}Reactor",
    "超低温反应釜": f"{_EQUIP_NS}Reactor",
    "旋蒸": f"{_EQUIP_NS}RotaryEvaporator",
    "旋转蒸发器": f"{_EQUIP_NS}RotaryEvaporator",
    "热风循环干燥箱": f"{_EQUIP_NS}HotAirDryer",
    "真空干燥箱": f"{_EQUIP_NS}VacuumDryer",
    "电热真空干燥箱": f"{_EQUIP_NS}ElectricVacuumDryer",
    "电热鼓风干燥箱": f"{_EQUIP_NS}HotAirBlastDryer",
    "整粒机": f"{_EQUIP_NS}Granulator",
    "压滤器": f"{_EQUIP_NS}FilterPress",
    "可移动式压滤器": f"{_EQUIP_NS}FilterPress",
    "微压柱系统": f"{_EQUIP_NS}ChromatographyColumnSystem",
    "高压制备液相系统": f"{_EQUIP_NS}ProcessEquipment",
    "钛棒过滤器": f"{_EQUIP_NS}ProcessEquipment",
    "移动储罐": f"{_EQUIP_NS}ProcessEquipment",
    "滴加罐": f"{_EQUIP_NS}ProcessEquipment",
}

# ---------------------------------------------------------------------------
# 主体材质 → 本体材料类 IRI（基于 slpra-equipment.ttl ConstructionMaterial 层级）
# ---------------------------------------------------------------------------
_MATERIAL_CLASS: dict[str, str] = {
    "304不锈钢": f"{_EQUIP_NS}StainlessSteel304",
    "316L不锈钢": f"{_EQUIP_NS}StainlessSteel316L",
    "316L": f"{_EQUIP_NS}StainlessSteel316L",
    "304不锈钢衬HALAR": f"{_EQUIP_NS}SS304WithHALARCoating",
    "304不锈钢HALAR": f"{_EQUIP_NS}SS304WithHALARCoating",
    "316L不锈钢衬HALAR": f"{_EQUIP_NS}SS316LWithHALARCoating",
    "316L不锈钢HALAR": f"{_EQUIP_NS}SS316LWithHALARCoating",
    "搪玻璃": f"{_EQUIP_NS}BorosilicateGlass",
    "玻璃": f"{_EQUIP_NS}BorosilicateGlass",
}


@dataclass(frozen=True)
class EquipmentFact:
    """外部事实源返回的单个生产设备事实。"""

    equipment_id: str
    iri: str
    label: str
    equipment_class_iri: str
    workshop_code: str
    data_properties: list[dict] = field(default_factory=list)


class EquipmentSource(Protocol):
    """设备档案事实源契约。"""

    def resolve(self, equipment_id: str) -> EquipmentFact | None:
        """按设备编号解析为 Equipment 事实（未知 → ``None``）。"""
        ...

    def list_by_workshop(self, workshop_code: str) -> list[EquipmentFact]:
        """列出指定车间的全部设备。"""
        ...


def _dp(iri: str | None, label: str, value: str) -> dict:
    return {"iri": iri, "label": label, "value": value}


def _build_fact(
    workshop: str,
    equipment_id: str,
    name: str,
    spec: str,
    material: str,
    location: str,
    area_type: str,
) -> EquipmentFact:
    class_iri = _EQUIP_CLASS.get(name, f"{_EQUIP_NS}ProcessEquipment")
    material_iri = _MATERIAL_CLASS.get(material)

    dps = [
        _dp(f"{_EQUIP_NS}equipmentID", "设备编号", equipment_id),
        _dp(f"{_EQUIP_NS}equipmentName", "设备名称", name),
    ]
    if spec:
        dps.append(_dp(f"{_EQUIP_NS}modelSpecification", "规格型号", spec))
    if material:
        dps.append(_dp(
            f"{_EQUIP_NS}constructedOf" if material_iri else None,
            "主体材质",
            material,
        ))
    if location:
        dps.append(_dp(f"{_EQUIP_NS}locatedIn", "安装位置", location))
    is_clean = area_type == "洁净区"
    dps.append(_dp(f"{_EQUIP_NS}isInCleanArea", "是否洁净区", str(is_clean).lower()))

    label = f"{name} {spec}".strip() if spec else name
    return EquipmentFact(
        equipment_id=equipment_id,
        iri=f"{_FACTS_NS}equipment-{equipment_id}",
        label=label,
        equipment_class_iri=class_iri,
        workshop_code=workshop,
        data_properties=dps,
    )


# ---------------------------------------------------------------------------
# Canned data — (车间号, 设备编号, 设备名称, 规格型号, 主体材质, 安装位置, 位置类型)
# 源自 docs/642车间设备档案.xlsx + docs/646车间设备档案.xlsx「设备档案」sheet。
# ---------------------------------------------------------------------------
_RAW: list[tuple[str, str, str, str, str, str, str]] = [
    ("642", "CT64201", "离心机", "800", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("642", "CT64202", "离心机", "600", "316L不锈钢", "一般区南平台下", "一般区"),
    ("642", "CT64203", "离心机", "600", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("642", "CT64204", "离心机", "600", "316L不锈钢", "洁净区精制间", "洁净区"),
    ("642", "CT64205", "离心机", "800", "304不锈钢衬HALAR", "一般区北平台下", "一般区"),
    ("642", "CT64206", "离心机", "800", "316L不锈钢", "一般区北平台下", "一般区"),
    ("642", "CT64209", "离心机", "450", "304不锈钢衬HALAR", "洁净区精制间", "洁净区"),
    ("642", "CT64210", "离心机", "600", "316L不锈钢", "一般区南平台下", "一般区"),
    ("642", "CT64211", "离心机", "600", "316L不锈钢", "一般区南平台下", "一般区"),
    ("642", "CT64212", "离心机", "600", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("642", "CT64213", "离心机", "800", "316L不锈钢", "一般区南平台下", "一般区"),
    ("642", "CT64214", "离心机", "800", "304不锈钢衬HALAR", "一般区北平台下", "一般区"),
    ("642", "DE64201", "热风循环干燥箱", "48盘", "316L不锈钢", "一般区干燥间", "一般区"),
    ("642", "DE64202", "真空干燥箱", "24盘", "304不锈钢", "一般区干燥间", "一般区"),
    ("642", "DE64203", "真空干燥箱", "24盘", "304不锈钢衬HALAR", "洁净区干燥间", "洁净区"),
    ("642", "DE64204", "电热鼓风干燥箱", "12盘", "316L不锈钢HALAR", "洁净区干燥间", "洁净区"),
    ("642", "DE64205", "电热真空干燥箱", "8盘", "不锈钢衬HALAR", "一般区干燥间", "一般区"),
    ("642", "DE64206", "电热真空干燥箱", "6053", "316L不锈钢", "一般区干燥间", "一般区"),
    ("642", "DE64207", "电热鼓风干燥箱", "BGX-198", "316L不锈钢", "一般区干燥间", "一般区"),
    ("642", "EE64201", "旋蒸", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "EE64202", "旋蒸", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "EE64203", "旋蒸", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "EE64204", "旋蒸", "20L", "玻璃", "洁净区精制间", "洁净区"),
    ("642", "EE64205", "旋蒸", "2L", "玻璃", "五楼实验隔断", "一般区"),
    ("642", "EE64206", "旋蒸", "5L", "玻璃", "五楼实验隔断", "一般区"),
    ("642", "EE64211", "旋蒸", "50L", "玻璃", "五楼实验隔断", "一般区"),
    ("642", "EE64212", "旋蒸", "50L", "玻璃", "五楼实验隔断", "一般区"),
    ("642", "EE64216", "旋转蒸发器", "20L", "玻璃", "一般区", "一般区"),
    ("642", "EE64217", "旋转蒸发器", "20L", "玻璃", "一般区", "一般区"),
    ("642", "EE64218", "旋蒸", "50L", "玻璃", "", "一般区"),
    ("642", "EE64219", "旋蒸", "50L", "玻璃", "", "一般区"),
    ("642", "PF64201", "微压柱系统", "300*2000mm", "不锈钢", "五楼制备间", "一般区"),
    ("642", "PF64202", "微压柱系统", "400*1600mm", "不锈钢", "五楼制备间", "一般区"),
    ("642", "PF64203", "微压柱系统", "500*2000mm", "不锈钢", "五楼制备间", "一般区"),
    ("642", "PF64204", "高压制备液相系统", "DAC200", "不锈钢", "五楼制备间", "一般区"),
    ("642", "PF64205", "高压制备液相系统", "DAC200", "不锈钢", "五楼一般区", "一般区"),
    ("642", "PF64211", "可移动式压滤器", "30L", "不锈钢衬HALAR", "三楼一般区", "一般区"),
    ("642", "PF64212", "可移动式压滤器", "30L", "不锈钢", "三楼一般区", "一般区"),
    ("642", "PF64213", "可移动式压滤器", "50L", "不锈钢衬HALAR", "三楼一般区", "一般区"),
    ("642", "PF64214", "压滤器", "50L", "", "", "一般区"),
    ("642", "RE64201", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64202", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64203", "搪玻璃反应釜", "300L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64204", "搪玻璃反应釜", "300L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64205", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64206", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64207", "搪玻璃反应釜", "100L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64208", "搪玻璃反应釜", "100L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64209", "搪玻璃反应釜", "50L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64210", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64211", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("642", "RE64212", "搪玻璃反应釜", "200L", "搪玻璃", "洁净区精制间钢平台", "洁净区"),
    ("642", "RE64213", "搪玻璃反应釜", "500L", "搪玻璃", "洁净区精制间钢平台", "洁净区"),
    ("642", "RE64214", "搪玻璃反应釜", "500L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64215", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64216", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64217", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64218", "搪玻璃反应釜", "2000L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64219", "搪玻璃反应釜", "2000L", "搪玻璃", "一般区北平台", "一般区"),
    ("642", "RE64220", "玻璃釜", "100L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64221", "玻璃釜", "100L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64222", "玻璃釜", "50L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64223", "玻璃釜", "50L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64224", "超低温反应釜", "50L", "哈氏合金", "五楼一般区南", "一般区"),
    ("642", "RE64225", "玻璃釜", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64226", "玻璃釜", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64227", "玻璃釜", "50L", "玻璃", "洁净区精制间", "洁净区"),
    ("642", "RE64228", "玻璃釜", "10L", "玻璃", "洁净区精制间", "洁净区"),
    ("642", "RE64229", "玻璃釜", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("642", "RE64230", "玻璃釜", "100L", "玻璃", "洁净区精制间", "洁净区"),
    ("642", "RE64231", "哈氏合金釜", "200L", "哈氏合金", "一般区", "一般区"),
    ("642", "RE64232", "玻璃釜", "50L", "玻璃", "一般区", "一般区"),
    ("642", "RE64233", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("642", "RE64234", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("642", "RE64235", "玻璃釜", "10L", "玻璃", "一般区", "一般区"),
    ("642", "RE64236", "玻璃釜", "20L", "玻璃", "一般区", "一般区"),
    ("642", "RE64237", "超低温反应釜", "50L", "哈氏合金", "一般区", "一般区"),
    ("642", "RE64238", "玻璃釜", "10L", "玻璃", "一般区", "一般区"),
    ("642", "RE64241", "玻璃釜", "20L", "玻璃", "一般区", "一般区"),
    ("642", "RE64242", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("642", "ST64231", "移动储罐", "50L", "316L不锈钢", "一般区", "一般区"),
    ("642", "ST64232", "移动储罐", "50L", "316L不锈钢", "一般区", "一般区"),
    ("642", "ST64233", "移动储罐", "50L", "316L", "一般区", "一般区"),
    ("642", "ST64234", "移动储罐", "50L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64235", "移动储罐", "50L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64236", "移动储罐", "50L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64237", "移动储罐", "100L", "316L", "一般区", "一般区"),
    ("642", "ST64238", "移动储罐", "100L", "316L", "一般区", "一般区"),
    ("642", "ST64239", "移动储罐", "100L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64240", "移动储罐", "100L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64241", "移动储罐", "200L", "316L", "一般区", "一般区"),
    ("642", "ST64242", "移动储罐", "200L", "316L", "一般区", "一般区"),
    ("642", "ST64243", "移动储罐", "200L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64244", "移动储罐", "200L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "ST64245", "移动储罐", "300L", "304不锈钢HALAR", "一般区", "一般区"),
    ("642", "SG64201", "整粒机", "U5", "不锈钢", "洁净区整粒间", "洁净区"),
    ("642", "DE64208", "真空干燥箱", "PFZG-8型", "不锈钢衬HALAR", "五楼干燥间", "一般区"),
    ("642", "RE64257", "超低温反应釜", "500L", "哈氏合金", "五楼干燥间", "一般区"),
    ("642", "EE64220", "旋转蒸发器", "2L", "玻璃", "五楼一般区", "一般区"),
    ("642", "ST64212", "滴加罐", "300L", "304不锈钢", "五楼一般区北", "一般区"),
    ("642", "EE64213", "旋转蒸发器", "5L", "玻璃", "五楼一般区", "一般区"),
    ("642", "PF64216", "钛棒过滤器", "10英寸3芯", "316L不锈钢", "一般区", "一般区"),
    ("642", "CT64218", "离心机", "600", "不锈钢衬HALAR", "642车间洁净区", "洁净区"),
    ("642", "RE64259", "哈氏合金釜", "100L", "玻璃+哈氏合金", "642车间洁净区精制间", "洁净区"),
    ("642", "VD64203", "真空干燥箱", "48盘", "304不锈钢", "一般区干燥间", "一般区"),
    ("646", "CT64601", "离心机", "800", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("646", "CT64602", "离心机", "600", "316L不锈钢", "一般区南平台下", "一般区"),
    ("646", "CT64603", "离心机", "600", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("646", "CT64604", "离心机", "600", "316L不锈钢", "洁净区精制间", "洁净区"),
    ("646", "CT64605", "离心机", "800", "304不锈钢衬HALAR", "一般区北平台下", "一般区"),
    ("646", "CT64606", "离心机", "800", "316L不锈钢", "一般区北平台下", "一般区"),
    ("646", "CT64609", "离心机", "450", "304不锈钢衬HALAR", "洁净区精制间", "洁净区"),
    ("646", "CT64610", "离心机", "600", "316L不锈钢", "一般区南平台下", "一般区"),
    ("646", "CT64611", "离心机", "800", "304不锈钢衬HALAR", "一般区北平台下", "一般区"),
    ("646", "CT64612", "离心机", "600", "304不锈钢衬HALAR", "一般区南平台下", "一般区"),
    ("646", "CT64613", "离心机", "800", "316L不锈钢", "一般区南平台下", "一般区"),
    ("646", "CT64614", "离心机", "800", "304不锈钢衬HALAR", "一般区北平台下", "一般区"),
    ("646", "DE64601", "热风循环干燥箱", "48盘", "316L不锈钢", "一般区干燥间", "一般区"),
    ("646", "DE64602", "真空干燥箱", "24盘", "304不锈钢", "一般区干燥间", "一般区"),
    ("646", "VD64603", "真空干燥箱", "48盘", "304不锈钢", "一般区干燥间", "一般区"),
    ("646", "DE64604", "电热鼓风干燥箱", "12盘", "316L不锈钢衬HALAR", "洁净区干燥间", "洁净区"),
    ("646", "DE64605", "电热真空干燥箱", "8盘", "不锈钢衬HALAR", "一般区干燥间", "一般区"),
    ("646", "DE64606", "电热真空干燥箱", "6053", "316L不锈钢", "一般区干燥间", "一般区"),
    ("646", "DE64607", "电热鼓风干燥箱", "BGX-198", "316L不锈钢", "一般区干燥间", "一般区"),
    ("646", "EE64601", "旋蒸", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "EE64602", "旋蒸", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "EE64603", "旋蒸", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "EE64604", "旋蒸", "20L", "玻璃", "洁净区精制间", "洁净区"),
    ("646", "EE64605", "旋蒸", "2L", "玻璃", "五楼实验隔断", "一般区"),
    ("646", "EE64606", "旋蒸", "5L", "玻璃", "五楼实验隔断", "一般区"),
    ("646", "EE64611", "旋蒸", "50L", "玻璃", "五楼实验隔断", "一般区"),
    ("646", "EE64612", "旋蒸", "50L", "玻璃", "五楼实验隔断", "一般区"),
    ("646", "EE64616", "旋转蒸发器", "20L", "玻璃", "一般区", "一般区"),
    ("646", "EE64617", "旋转蒸发器", "20L", "玻璃", "一般区", "一般区"),
    ("646", "EE64618", "旋蒸", "50L", "玻璃", "", "一般区"),
    ("646", "EE64619", "旋蒸", "50L", "玻璃", "", "一般区"),
    ("646", "PF64601", "微压柱系统", "300*2000mm", "不锈钢", "五楼制备间", "一般区"),
    ("646", "PF64602", "微压柱系统", "400*1600mm", "不锈钢", "五楼制备间", "一般区"),
    ("646", "PF64603", "微压柱系统", "500*2000mm", "不锈钢", "五楼制备间", "一般区"),
    ("646", "PF64604", "高压制备液相系统", "DAC200", "不锈钢", "五楼制备间", "一般区"),
    ("646", "PF64605", "高压制备液相系统", "DAC200", "不锈钢", "一般区", "一般区"),
    ("646", "PF64611", "可移动式压滤器", "30L", "不锈钢衬HALAR", "三楼一般区", "一般区"),
    ("646", "PF64612", "可移动式压滤器", "30L", "不锈钢", "三楼一般区", "一般区"),
    ("646", "PF64613", "可移动式压滤器", "50L", "不锈钢衬HALAR", "三楼一般区", "一般区"),
    ("646", "PF64614", "压滤器", "50L", "", "", "一般区"),
    ("646", "RE64601", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64602", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64603", "搪玻璃反应釜", "300L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64604", "搪玻璃反应釜", "300L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64605", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64606", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64607", "搪玻璃反应釜", "100L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64608", "搪玻璃反应釜", "100L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64609", "搪玻璃反应釜", "50L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64610", "搪玻璃反应釜", "500L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64611", "搪玻璃反应釜", "200L", "搪玻璃", "一般区南平台", "一般区"),
    ("646", "RE64612", "搪玻璃反应釜", "200L", "搪玻璃", "洁净区精制间钢平台", "洁净区"),
    ("646", "RE64613", "搪玻璃反应釜", "500L", "搪玻璃", "洁净区精制间钢平台", "洁净区"),
    ("646", "RE64614", "搪玻璃反应釜", "500L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64615", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64616", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64617", "搪玻璃反应釜", "1000L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64618", "搪玻璃反应釜", "2000L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64619", "搪玻璃反应釜", "2000L", "搪玻璃", "一般区北平台", "一般区"),
    ("646", "RE64620", "玻璃釜", "100L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64621", "玻璃釜", "100L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64622", "玻璃釜", "50L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64623", "玻璃釜", "50L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64624", "超低温反应釜", "50L", "哈氏合金", "五楼一般区南", "一般区"),
    ("646", "RE64625", "玻璃釜", "20L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64626", "玻璃釜", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64627", "玻璃釜", "50L", "玻璃", "洁净区精制间", "洁净区"),
    ("646", "RE64628", "玻璃釜", "10L", "玻璃", "洁净区精制间", "洁净区"),
    ("646", "RE64629", "玻璃釜", "10L", "玻璃", "五楼一般区南", "一般区"),
    ("646", "RE64630", "玻璃釜", "100L", "玻璃", "洁净区精制间", "洁净区"),
    ("646", "RE64631", "哈氏合金釜", "200L", "哈氏合金", "", "一般区"),
    ("646", "RE64632", "玻璃釜", "50L", "玻璃", "一般区", "一般区"),
    ("646", "RE64633", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("646", "RE64634", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("646", "RE64635", "玻璃釜", "10L", "玻璃", "一般区", "一般区"),
    ("646", "RE64636", "玻璃釜", "20L", "玻璃", "一般区", "一般区"),
    ("646", "RE64637", "超低温反应釜", "50L", "哈氏合金", "一般区", "一般区"),
    ("646", "RE64638", "玻璃釜", "10L", "玻璃", "一般区", "一般区"),
    ("646", "RE64641", "玻璃釜", "20L", "玻璃", "一般区", "一般区"),
    ("646", "RE64642", "玻璃釜", "100L", "玻璃", "一般区", "一般区"),
    ("646", "ST64631", "移动储罐", "50L", "316L不锈钢", "一般区", "一般区"),
    ("646", "ST64632", "移动储罐", "50L", "316L不锈钢", "一般区", "一般区"),
    ("646", "ST64633", "移动储罐", "50L", "316L", "一般区", "一般区"),
    ("646", "ST64634", "移动储罐", "50L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64635", "移动储罐", "50L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64636", "移动储罐", "50L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64637", "移动储罐", "100L", "316L", "一般区", "一般区"),
    ("646", "ST64638", "移动储罐", "100L", "316L", "一般区", "一般区"),
    ("646", "ST64639", "移动储罐", "100L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64640", "移动储罐", "100L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64641", "移动储罐", "200L", "316L", "一般区", "一般区"),
    ("646", "ST64642", "移动储罐", "200L", "316L", "一般区", "一般区"),
    ("646", "ST64643", "移动储罐", "200L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64644", "移动储罐", "200L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "ST64645", "移动储罐", "300L", "304不锈钢衬HALAR", "一般区", "一般区"),
    ("646", "SG64601", "整粒机", "U5", "不锈钢", "洁净区整粒间", "洁净区"),
    ("646", "DE64608", "真空干燥箱", "PFZG-8型", "不锈钢衬HALAR", "五楼干燥间", "一般区"),
    ("646", "RE64657", "超低温反应釜", "500L", "哈氏合金", "五楼干燥间", "一般区"),
    ("646", "EE64620", "旋转蒸发器", "2L", "玻璃", "五楼一般区", "一般区"),
    ("646", "ST64612", "滴加罐", "300L", "304不锈钢", "五楼一般区北", "一般区"),
    ("646", "EE64613", "旋转蒸发器", "5L", "玻璃", "五楼一般区", "一般区"),
    ("646", "PF64616", "钛棒过滤器", "10英寸3芯", "316L不锈钢", "一般区", "一般区"),
    ("646", "CT64618", "离心机", "600", "不锈钢衬HALAR", "646车间洁净区", "洁净区"),
    ("646", "RE64659", "哈氏合金釜", "100L", "玻璃+哈氏合金", "646车间洁净区精制间", "洁净区"),
]

# 设备编号 → EquipmentFact 索引 + 车间号 → [EquipmentFact] 索引。
_BY_ID: dict[str, EquipmentFact] = {}
_BY_WORKSHOP: dict[str, list[EquipmentFact]] = {}

for _row in _RAW:
    _f = _build_fact(*_row)
    _BY_ID[_f.equipment_id] = _f
    _BY_WORKSHOP.setdefault(_f.workshop_code, []).append(_f)


class MockEquipmentSource:
    """内存 canned 设备档案（mock 外部 API）；未知设备编号 → ``None``（优雅降级）。"""

    def resolve(self, equipment_id: str) -> EquipmentFact | None:
        return _BY_ID.get((equipment_id or "").strip())

    def list_by_workshop(self, workshop_code: str) -> list[EquipmentFact]:
        return list(_BY_WORKSHOP.get((workshop_code or "").strip(), []))

    def evidence_record(self, equipment_id: str):
        """Versioned raw record for candidate creation, never report-time enrichment."""
        from app.services.extraction.external_records import ResolvedRecord, record_version

        raw = next((row for row in _RAW if row[1] == equipment_id), None)
        if raw is None:
            return None
        fields = dict(zip(("workshop", "equipment_id", "name", "specification", "material",
                           "location", "area_type"), raw, strict=True))
        return ResolvedRecord(
            system="mock_equipment", dataset="equipment_archive", key=equipment_id,
            version=record_version(fields), class_iri=PROCESS_EQUIPMENT_IRI,
            label_field="equipment_id", fields=fields,
            field_predicates={"equipment_id": f"{_EQUIP_NS}equipmentID",
                              "name": f"{_EQUIP_NS}equipmentName",
                              "specification": f"{_EQUIP_NS}modelSpecification"},
        )


def get_equipment_source() -> EquipmentSource:
    """返回当前生效的设备档案事实源。

    现为 mock（Equipment 尚未对接）；真实内网设备主数据 API 接入时在此切换为
    RestConnector 支撑的实现即可，调用方 finder 无需改动。
    """
    return MockEquipmentSource()


# ---------------------------------------------------------------------------
# 设备 edge 识别 / 遍历 / 富化 —— 抽取期与报告期共用的唯一实现（消除三处口径漂移）。
# ---------------------------------------------------------------------------
def equipment_class_iris() -> set[str]:
    """已知设备类 IRI 闭包：基类 ``Equipment``/``ProcessEquipment`` + 档案映射的具体子类。

    刻意**不**按命名空间前缀泛判——equipment 命名空间同样容纳 ``ConstructionMaterial``、
    ``EquipmentSurface`` 等非设备类，前缀判定会把它们误纳入设备表/覆盖计数。"""
    return {EQUIPMENT_IRI, PROCESS_EQUIPMENT_IRI, *(_EQUIP_CLASS.values())}


def is_equipment_edge(edge: dict) -> bool:
    """该 edge 的对象是否为设备端点。

    以谓词 ``usesEquipment`` 为**权威信号**——设备经两条路径进图谱（``extractionProfile``
    顶层边、合成步骤下 ``_step_equipment`` 嵌套子关系），两者谓词皆为 ``usesEquipment``，
    故谓词判定完备且不会误伤 ``constructedOf`` 的材料对象。辅以已知设备类闭包，兜底极少数
    缺谓词的历史 edge。"""
    if not isinstance(edge, dict):
        return False
    if (edge.get("predicate_iri") or "") == USES_EQUIPMENT_IRI:
        return True
    return (edge.get("object_class_iri") or "") in equipment_class_iris()


def iter_equipment_edges(edges: Sequence[dict]) -> Iterator[dict]:
    """递归产出全部设备 edge（顶层 + ``sub_relationships``），**不去重**（聚合交由调用方）。"""
    def _walk(edge: dict) -> Iterator[dict]:
        if is_equipment_edge(edge):
            yield edge
        for sub in edge.get("sub_relationships") or []:
            if isinstance(sub, dict):
                yield from _walk(sub)

    for e in edges:
        if isinstance(e, dict):
            yield from _walk(e)


def _norm(value: str | None) -> str:
    """归一化文本比较：去内部空白，避免「316L」vs「316 L」这类假冲突。"""
    return re.sub(r"\s+", "", value or "")


def _merge_fact_into_edge(edge: dict, fact: EquipmentFact) -> list[str]:
    """把外部设备档案事实并入单个设备 edge；返回该设备的「文档 vs 档案」冲突说明。

    档案值写入**文档规范标签**（``设备名称/设备规格/材质``，见 ``_ARCHIVE_TO_DOC_LABEL``）：
    文档侧为空则填入；文档侧已有非空值则**保留**（不静默覆盖），仅当与档案不一致时记冲突
    —— GMP 忠实性：报告作者声明值优先，档案分歧交人工确认。类仅在泛化时升级为具体子类且
    同步 ``object_class_label``；``object_source``→``external``，``source_ref`` 追溯档案与车间。
    """
    props: list[dict] = edge.get("object_data_properties") or []
    conflicts: list[str] = []

    def _current(label: str) -> str:
        for p in props:
            if p.get("label") == label and p.get("value") not in (None, ""):
                return str(p.get("value"))
        return ""

    for dp in fact.data_properties:
        arc_label = dp.get("label", "")
        arc_value = dp.get("value", "")
        if arc_label in _SKIP_ARCHIVE_LABELS or not arc_value:
            continue
        doc_label = _ARCHIVE_TO_DOC_LABEL.get(arc_label, arc_label)
        current = _current(doc_label)
        if not current:
            props.append({
                "iri": dp.get("iri"),
                "label": doc_label,
                "value": arc_value,
                "source": "external",
            })
        elif _norm(current) != _norm(arc_value):
            field = doc_label
            conflicts.append(
                f"{fact.equipment_id} {field}：文档「{current}」/ 档案「{arc_value}」，请人工确认。"
            )

    edge["object_data_properties"] = props

    cur_cls = edge.get("object_class_iri") or ""
    if cur_cls in ("", EQUIPMENT_IRI, PROCESS_EQUIPMENT_IRI) and fact.equipment_class_iri:
        edge["object_class_iri"] = fact.equipment_class_iri
        edge["object_class_label"] = fact.equipment_class_iri.rsplit("/", 1)[-1]

    edge["object_source"] = "external"
    edge["object_iri"] = fact.iri
    # source_ref 双形：legacy str 直接串接溯源标签；017-profile 结构化 dict 必须**保留**
    # （前端据此锚定原文位置），故把档案标签写入 dict 的 ``enrichment`` 键而非覆盖整个 dict。
    tag = f"外部设备档案（record={fact.equipment_id}；workshop={fact.workshop_code}车间）"
    base = edge.get("source_ref")
    if isinstance(base, dict):
        base["enrichment"] = tag
    else:
        base_str = base or ""
        edge["source_ref"] = f"{base_str} + {tag}" if base_str else tag
    return conflicts


# 已知车间集合——写入端 ``_merge_fact_into_edge`` 的 ``fact.workshop_code`` 只会产出这几个码；
# 解析端全部按此闭集约束，故任何越界数字（``workshop=999车间``、坐标数字）都不会伪造出车间分组。
_KNOWN_WORKSHOP_CODES = ("642", "646", "644")
_WORKSHOP_CODE_ALT = "|".join(_KNOWN_WORKSHOP_CODES)

# 报告期设备归组的两级车间信号，均为**共享解析器**（两个报告消费点复用，杜绝口径分叉）：
#   1) **权威**：``_merge_fact_into_edge`` 写入的 ``外部设备档案（…；workshop=<码>车间）`` 富化标签
#      （配对读/写，改一处须同步另一处）。正则**锚定完整标签前缀** ``外部设备档案（`` 且车间号限已知
#      闭集——否则文档正文里偶发的裸 ``workshop=642车间``（如恰好如此命名的表头）会被误当权威标签，
#      抢在真实档案/设备编号（如 646）之前命中（Codex #1：需标签边界；Codex round-3 #3：限已知集，
#      拒绝 ``workshop=999车间`` 之类越界值）。``[^）]*`` 贪婪回溯确保取末尾真实 workshop，而非
#      record 值里可能注入的前置 ``workshop=``。
#   2) **兜底**：文本里出现已知车间号即归组。用**数字边界** ``(?<!\d)…(?!\d)`` 而非 ``\b``——Python
#      Unicode 下数字与汉字「车」同属 word char，``\b`` 在「642车间」处不成立会漏判；纯子串 ``in``
#      又会误配「1642」「6420」。数字边界两头兼顾（Codex #2：统一两个报告消费点的 legacy 扫描语义）。
_WORKSHOP_TAG_RE = re.compile(rf"外部设备档案（[^）]*workshop=({_WORKSHOP_CODE_ALT})车间")
_KNOWN_WORKSHOP_RE = re.compile(rf"(?<!\d)({_WORKSHOP_CODE_ALT})(?!\d)")

# 结构化 source_ref 里**可承载业务车间文本**的键（section/标题/参数/富化标签、及外部系统定位）。
# 兜底扫描**只**看这些文本键，**绝不**看 ``table``/``row``/``column``/``*_index`` 等**位置坐标**——
# 后者是 0→1based 的渲染产物（``table=641`` → ``表 642``），扫描它会把结构坐标数字误当车间号
# （Codex round-3 #1：未富化 017-dict 因坐标 ``表 642`` 被误归 642车间并吞掉「人工确认」提示）。
_LOOSE_SCAN_TEXT_KEYS = ("section", "parameter", "key", "header", "enrichment", "system", "entity", "record")


def workshop_from_enrichment(source_ref_text: str | None) -> str | None:
    """从富化溯源展示串解析**权威**车间号：``…外部设备档案（…；workshop=642车间）…`` → ``"642车间"``。

    入参是 ``format_source_ref`` 归一后的展示串（dict 的 enrichment 键或 legacy 串接标签，两者都含
    完整 ``外部设备档案（…；workshop=<码>车间）`` 标签）。**仅**识别带该档案标签前缀、且车间号属已知
    闭集的车间——裸 ``workshop=…车间`` 或越界码不算权威标签，返回 ``None``（交由设备编号/兜底处理）。
    """
    m = _WORKSHOP_TAG_RE.search(source_ref_text or "")
    return f"{m.group(1)}车间" if m else None


def _loose_scan_text(source_ref: Any) -> str:
    """把 source_ref 归一为**仅含业务文本**的可扫描串：legacy str 原样；dict 只取文本键，**丢弃位置坐标**。

    这是兜底扫描不误判结构坐标的关键——绝不能把 ``table``/``row``/``column`` 的 1-based 渲染
    （``表 642``）喂给数字扫描。dict 无文本键（纯坐标定位）→ 空串 → 兜底不命中 → 正确归「未分组」。
    """
    if isinstance(source_ref, str):
        return source_ref
    if isinstance(source_ref, dict):
        return " ".join(
            source_ref[k].strip()
            for k in _LOOSE_SCAN_TEXT_KEYS
            if isinstance(source_ref.get(k), str) and source_ref[k].strip()
        )
    return ""


def workshop_from_loose_scan(source_ref: Any) -> str | None:
    """legacy 兜底：source_ref 的**业务文本**里出现已知车间号(642/646/644)即归组；无 → ``None``。

    入参是**原始** source_ref（str | 017-profile dict | None），而非 ``format_source_ref`` 展示串——
    因为展示串已把结构坐标渲染进文本，会让数字扫描误命中坐标（见 ``_loose_scan_text``）。**仅**当无
    权威富化标签、也无法从设备编号锚定时才使用（最低优先级）。数字边界匹配，「642车间」命中而
    「1642」「6420」不误配。两个报告消费点共用本函数，确保 legacy 兜底口径完全一致。
    """
    m = _KNOWN_WORKSHOP_RE.search(_loose_scan_text(source_ref))
    return f"{m.group(1)}车间" if m else None


def enrich_equipment_facts(edges: Sequence[dict]) -> list[str]:
    """按设备编号从外部设备档案富化全部设备 edge（**幂等**；抽取期与报告期均可调用）。

    统一覆盖 ``extractionProfile`` 顶层设备边与 ``_step_equipment`` 嵌套子关系（两者
    ``object_text`` 均为设备编号）。就地改写 edge（追加档案属性、升级类、标注溯源），返回
    「文档 vs 档案」冲突说明列表（供报告侧并入设备表注记）。未命中（未知编号 / 源不可用）
    → 原文保留（优雅降级，Principle VI）。同一编号查询结果缓存复用。

    幂等保证：已富化过（``object_source==external`` 且 ``source_ref`` 含档案标签）的 edge 跳过，
    故在抽取期与报告期重复调用不会二次追加溯源标签或重复填值。
    """
    source = get_equipment_source()
    cache: dict[str, EquipmentFact | None] = {}
    conflicts: list[str] = []

    def _lookup(code: str) -> EquipmentFact | None:
        if code not in cache:
            try:
                cache[code] = source.resolve(code)
            except Exception:  # 外部源异常 → 优雅降级
                logger.debug("设备档案富化跳过 code=%s", code, exc_info=True)
                cache[code] = None
        return cache[code]

    for edge in iter_equipment_edges(edges):
        # 幂等：已富化 edge 的溯源含档案标签。source_ref 双形——dict 把标签写在
        # ``enrichment`` 键，str 直接串接，故用 format_source_ref 归一后统一判定。
        if edge.get("object_source") == "external" and "外部设备档案" in _source_ref_text(
            edge.get("source_ref")
        ):
            continue  # 幂等：跳过已富化 edge
        code = str(edge.get("object_text") or "").strip()
        if not code:
            continue
        fact = _lookup(code)
        if fact is not None:
            conflicts.extend(_merge_fact_into_edge(edge, fact))
    return conflicts
