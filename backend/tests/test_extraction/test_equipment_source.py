"""Mock 外部设备档案事实源单测（Equipment A-Box 尚未对接的 interim 替身）。

契约：按设备编号解析为带合成 A-Box IRI / 名称 / 本体类 IRI / 设备属性的 Equipment 事实；
未知设备编号 → ``None``（优雅降级）。``list_by_workshop`` 按车间号列出全部设备。
"""

from __future__ import annotations

from app.services.extraction.equipment_source import (
    EquipmentFact,
    MockEquipmentSource,
    get_equipment_source,
)

_EQUIP_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"


# --- resolve by equipment ID ------------------------------------------------

def test_resolve_known_equipment_returns_fact():
    fact = get_equipment_source().resolve("CT64201")
    assert isinstance(fact, EquipmentFact)
    assert fact.equipment_id == "CT64201"
    assert fact.label == "离心机 800"
    assert fact.iri == "http://slpra.org/facts#equipment-CT64201"
    assert fact.equipment_class_iri == f"{_EQUIP_NS}Centrifuge"
    assert fact.workshop_code == "642"


def test_resolve_646_equipment():
    fact = get_equipment_source().resolve("RE64601")
    assert fact is not None
    assert fact.equipment_id == "RE64601"
    assert fact.label == "搪玻璃反应釜 500L"
    assert fact.equipment_class_iri == f"{_EQUIP_NS}Reactor"
    assert fact.workshop_code == "646"


def test_resolve_data_properties_structure():
    fact = get_equipment_source().resolve("CT64201")
    labels = {d["label"]: d for d in fact.data_properties}
    assert labels["设备编号"]["value"] == "CT64201"
    assert labels["设备名称"]["value"] == "离心机"
    assert labels["规格型号"]["value"] == "800"
    assert labels["主体材质"]["value"] == "304不锈钢衬HALAR"
    assert labels["安装位置"]["value"] == "一般区南平台下"
    assert labels["是否洁净区"]["value"] == "false"


def test_resolve_clean_area_equipment():
    fact = get_equipment_source().resolve("CT64204")
    labels = {d["label"]: d["value"] for d in fact.data_properties}
    assert labels["是否洁净区"] == "true"


def test_resolve_data_property_iris():
    fact = get_equipment_source().resolve("CT64201")
    labels = {d["label"]: d["iri"] for d in fact.data_properties}
    assert labels["设备编号"] == f"{_EQUIP_NS}equipmentID"
    assert labels["设备名称"] == f"{_EQUIP_NS}equipmentName"
    assert labels["规格型号"] == f"{_EQUIP_NS}modelSpecification"
    assert labels["安装位置"] == f"{_EQUIP_NS}locatedIn"
    assert labels["是否洁净区"] == f"{_EQUIP_NS}isInCleanArea"


def test_resolve_material_with_known_iri():
    fact = get_equipment_source().resolve("CT64201")
    mat = next(d for d in fact.data_properties if d["label"] == "主体材质")
    assert mat["iri"] == f"{_EQUIP_NS}constructedOf"


def test_resolve_material_without_iri():
    fact = get_equipment_source().resolve("RE64224")
    mat = next(d for d in fact.data_properties if d["label"] == "主体材质")
    assert mat["value"] == "哈氏合金"
    assert mat["iri"] is None


def test_resolve_unknown_returns_none():
    assert get_equipment_source().resolve("UNKNOWN") is None


def test_resolve_strips_whitespace():
    assert get_equipment_source().resolve("  CT64201 ").equipment_id == "CT64201"


def test_resolve_empty_returns_none():
    source = get_equipment_source()
    assert source.resolve("") is None
    assert source.resolve("   ") is None


# --- equipment class IRI mapping -------------------------------------------

def test_class_iri_centrifuge():
    assert get_equipment_source().resolve("CT64201").equipment_class_iri == f"{_EQUIP_NS}Centrifuge"


def test_class_iri_reactor_glass_lined():
    assert get_equipment_source().resolve("RE64201").equipment_class_iri == f"{_EQUIP_NS}Reactor"


def test_class_iri_reactor_glass():
    assert get_equipment_source().resolve("RE64220").equipment_class_iri == f"{_EQUIP_NS}Reactor"


def test_class_iri_reactor_hastelloy():
    assert get_equipment_source().resolve("RE64231").equipment_class_iri == f"{_EQUIP_NS}Reactor"


def test_class_iri_reactor_cryogenic():
    assert get_equipment_source().resolve("RE64224").equipment_class_iri == f"{_EQUIP_NS}Reactor"


def test_class_iri_rotary_evaporator():
    assert get_equipment_source().resolve("EE64201").equipment_class_iri == f"{_EQUIP_NS}RotaryEvaporator"


def test_class_iri_rotary_evaporator_alt_name():
    assert get_equipment_source().resolve("EE64216").equipment_class_iri == f"{_EQUIP_NS}RotaryEvaporator"


def test_class_iri_hot_air_dryer():
    assert get_equipment_source().resolve("DE64201").equipment_class_iri == f"{_EQUIP_NS}HotAirDryer"


def test_class_iri_vacuum_dryer():
    assert get_equipment_source().resolve("DE64202").equipment_class_iri == f"{_EQUIP_NS}VacuumDryer"


def test_class_iri_electric_vacuum_dryer():
    assert get_equipment_source().resolve("DE64205").equipment_class_iri == f"{_EQUIP_NS}ElectricVacuumDryer"


def test_class_iri_hot_air_blast_dryer():
    assert get_equipment_source().resolve("DE64204").equipment_class_iri == f"{_EQUIP_NS}HotAirBlastDryer"


def test_class_iri_granulator():
    assert get_equipment_source().resolve("SG64201").equipment_class_iri == f"{_EQUIP_NS}Granulator"


def test_class_iri_filter_press():
    assert get_equipment_source().resolve("PF64211").equipment_class_iri == f"{_EQUIP_NS}FilterPress"


def test_class_iri_filter_press_fixed():
    assert get_equipment_source().resolve("PF64214").equipment_class_iri == f"{_EQUIP_NS}FilterPress"


def test_class_iri_chromatography_column_system():
    assert get_equipment_source().resolve("PF64201").equipment_class_iri == f"{_EQUIP_NS}ChromatographyColumnSystem"


def test_class_iri_process_equipment_storage_tank():
    assert get_equipment_source().resolve("ST64231").equipment_class_iri == f"{_EQUIP_NS}ProcessEquipment"


# --- list_by_workshop -------------------------------------------------------

def test_list_by_workshop_642_count():
    items = get_equipment_source().list_by_workshop("642")
    assert len(items) == 105
    assert all(f.workshop_code == "642" for f in items)


def test_list_by_workshop_646_count():
    items = get_equipment_source().list_by_workshop("646")
    assert len(items) == 104
    assert all(f.workshop_code == "646" for f in items)


def test_list_by_workshop_unknown_returns_empty():
    assert get_equipment_source().list_by_workshop("999") == []


def test_list_by_workshop_returns_copies():
    source = get_equipment_source()
    a = source.list_by_workshop("642")
    b = source.list_by_workshop("642")
    assert a is not b
    assert len(a) == len(b)


# --- IRI uniqueness ---------------------------------------------------------

def test_all_equipment_ids_unique():
    source = get_equipment_source()
    all_items = source.list_by_workshop("642") + source.list_by_workshop("646")
    ids = [f.equipment_id for f in all_items]
    assert len(ids) == len(set(ids))


def test_all_iris_unique():
    source = get_equipment_source()
    all_items = source.list_by_workshop("642") + source.list_by_workshop("646")
    iris = [f.iri for f in all_items]
    assert len(iris) == len(set(iris))


# --- factory ----------------------------------------------------------------

def test_factory_returns_mock():
    assert isinstance(get_equipment_source(), MockEquipmentSource)
