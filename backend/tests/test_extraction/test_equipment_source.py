"""Mock 外部设备档案事实源单测（Equipment A-Box 尚未对接的 interim 替身）。

契约：按设备编号解析为带合成 A-Box IRI / 名称 / 本体类 IRI / 设备属性的 Equipment 事实；
未知设备编号 → ``None``（优雅降级）。``list_by_workshop`` 按车间号列出全部设备。
"""

from __future__ import annotations

from app.services.extraction.equipment_source import (
    EquipmentFact,
    MockEquipmentSource,
    enrich_equipment_facts,
    get_equipment_source,
    workshop_from_enrichment,
    workshop_from_loose_scan,
)

_EQUIP_NS = "https://ontology.pharma-gmp.cn/slpra/equipment/"

_USES_EQUIP = _EQUIP_NS + "usesEquipment"
_EQUIP_CLASS = _EQUIP_NS + "Equipment"


def _equip_edge(code: str, source_ref):
    return {
        "predicate_iri": _USES_EQUIP,
        "object_class_iri": _EQUIP_CLASS,
        "object_text": code,
        "object_data_properties": [],
        "source_ref": source_ref,
    }


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


# --- enrich_equipment_facts: dual-shaped source_ref -------------------------

def test_enrich_preserves_structured_dict_source_ref():
    # 017-profile edges carry a STRUCTURED dict source_ref (frontend anchors on it).
    # Enrichment must NOT flatten it to a string — the archive/workshop tag goes into
    # the dict's ``enrichment`` key, leaving the positional locator intact.
    edge = _equip_edge("CT64201", {
        "kind": "table_cell", "table": 2, "row": 0, "column": 1, "header": "设备编号",
    })
    enrich_equipment_facts([edge])
    sr = edge["source_ref"]
    assert isinstance(sr, dict)  # NOT overwritten by a string
    assert sr["kind"] == "table_cell" and sr["header"] == "设备编号"
    assert "外部设备档案" in sr["enrichment"] and "642车间" in sr["enrichment"]
    assert edge["object_source"] == "external"


def test_enrich_legacy_string_source_ref_appends_tag():
    edge = _equip_edge("CT64201", "表 设备需求")
    enrich_equipment_facts([edge])
    assert isinstance(edge["source_ref"], str)
    assert edge["source_ref"].startswith("表 设备需求 + ")
    assert "外部设备档案" in edge["source_ref"]


def test_enrich_is_idempotent_for_both_shapes():
    # Report-time re-enrichment (over an already-enriched cache) must not re-append
    # the tag or re-fill properties — the idempotency guard reads the tag through
    # either shape.
    for source_ref in (
        {"kind": "table_cell", "table": 2, "row": 0, "column": 1, "header": "设备编号"},
        "表 设备需求",
    ):
        edge = _equip_edge("CT64201", source_ref)
        enrich_equipment_facts([edge])
        after_first = {
            "props": len(edge["object_data_properties"]),
            "source_ref": dict(edge["source_ref"]) if isinstance(edge["source_ref"], dict)
            else edge["source_ref"],
        }
        enrich_equipment_facts([edge])
        assert len(edge["object_data_properties"]) == after_first["props"]
        current = (
            dict(edge["source_ref"]) if isinstance(edge["source_ref"], dict)
            else edge["source_ref"]
        )
        assert current == after_first["source_ref"]


# --- workshop_from_enrichment: authoritative archive tag parse --------------

def test_workshop_from_enrichment_parses_tag_from_either_shape():
    # The report-time formatter renders the archive tag differently per shape, but the
    # ``workshop=<码>车间`` literal survives both — dict → after " · ", legacy → after " + ".
    assert workshop_from_enrichment(
        "表 4 · 外部设备档案（record=CT64201；workshop=642车间）"
    ) == "642车间"
    assert workshop_from_enrichment(
        "表 设备需求 + 外部设备档案（record=RE64202；workshop=646车间）"
    ) == "646车间"


def test_workshop_from_enrichment_ignores_non_tag_digits():
    # A structural coordinate digit ("表 642") is NOT an archive tag — the ``workshop=``
    # prefix is required, so a spurious coordinate can never fabricate a grouping.
    assert workshop_from_enrichment("表 642 · 设备编号") is None
    assert workshop_from_enrichment("") is None
    assert workshop_from_enrichment(None) is None


def test_workshop_from_enrichment_requires_archive_label_prefix():
    # Codex round-2 #1: the parser is AUTHORITATIVE, so it must key off the full archive
    # tag ``外部设备档案（…；workshop=…车间）`` — not a bare ``workshop=NNN车间`` literal that
    # could surface in ordinary document content (e.g. a header that renders to that text).
    # Otherwise a spurious 642 in the display string would beat a real 646 equipment code.
    assert workshop_from_enrichment("列 2 · workshop=642车间") is None  # bare, no 档案 prefix
    assert workshop_from_enrichment("外部设备档案（record=X；workshop=646车间）") == "646车间"


def test_workshop_from_enrichment_rejects_unknown_workshop_code():
    # Codex round-3 #3: the write side (``_merge_fact_into_edge``) only ever emits the known
    # workshop set (642/646/644), so the authoritative parser is constrained to it. A garbage
    # ``workshop=999车间`` in document text is NOT a valid archive tag → None (never a 999车间
    # phantom group beating a real equipment code).
    assert workshop_from_enrichment("外部设备档案（record=X；workshop=999车间）") is None
    assert workshop_from_enrichment("外部设备档案（record=X；workshop=644车间）") == "644车间"


def test_workshop_from_loose_scan_digit_boundary():
    # Codex round-2 #2: the SHARED legacy fallback both report consumers now call. Digit
    # boundaries match a CJK-adjacent code ("642车间", 车 is not a digit) yet reject
    # digit-adjacent noise ("1642"/"6420") — fixing the old \b-vs-substring divergence
    # (\b never matched "642车间" under Python Unicode; substring wrongly matched "1642").
    assert workshop_from_loose_scan("§ 642车间 设备需求") == "642车间"
    assert workshop_from_loose_scan("646 号线设备") == "646车间"
    assert workshop_from_loose_scan("批号 1642 / 6420 无车间") is None
    assert workshop_from_loose_scan("") is None
    assert workshop_from_loose_scan(None) is None


def test_workshop_from_loose_scan_ignores_structural_coordinates():
    # Codex round-3 #1 (the real bug): loose scan now takes the RAW source_ref and must scan
    # ONLY business-text fields — never positional coordinates. A 017-dict whose 1-based
    # coordinate render would be "表 642" (table=641) carries NO business workshop text, so
    # loose scan returns None → the edge correctly stays 未分组 instead of a phantom 642车间.
    assert workshop_from_loose_scan(
        {"kind": "table_cell", "table": 641, "row": 0, "column": 1, "header": "设备编号"}
    ) is None
    # …but a workshop mentioned in a genuine TEXT field (header/section) is still found.
    assert workshop_from_loose_scan(
        {"kind": "table_cell", "table": 0, "header": "642车间设备清单"}
    ) == "642车间"
    assert workshop_from_loose_scan(
        {"kind": "section", "section": "646车间共线评估"}
    ) == "646车间"


def test_workshop_from_loose_scan_reads_enrichment_text_field():
    # The archive tag lands in the dict's ``enrichment`` key; loose scan (as last resort)
    # can still recover the code from that text field via the raw dict.
    assert workshop_from_loose_scan(
        {"kind": "table_cell", "table": 5, "enrichment": "外部设备档案（record=X；workshop=644车间）"}
    ) == "644车间"


# --- factory ----------------------------------------------------------------

def test_factory_returns_mock():
    assert isinstance(get_equipment_source(), MockEquipmentSource)
