"""format_source_ref: dual-shaped (legacy str | 017-profile dict) source_ref → display string.

Regression for the whole-report failure ``expected string or bytes-like object,
got 'dict'``: the 017 ontology-guided Document Profile emits a **structured dict**
``source_ref`` (kept intentionally so the annotation UI can anchor the highlight),
but report-time consumers that treated it as a string (``re.search`` for workshop
codes, coverage ``source_ref`` display, fact lines) crashed or rendered garbage.
Output mirrors the frontend ``formatRelationSourceRef`` byte-for-byte.
"""

from __future__ import annotations

from app.services.reporting.narrative_generator import _extract_entity_context
from app.services.reporting.source_ref import format_source_ref


def test_legacy_string_passthrough():
    assert format_source_ref("§ 简介") == "§ 简介"
    assert format_source_ref("表 设备需求") == "表 设备需求"


def test_none_and_empty():
    assert format_source_ref(None) == ""
    assert format_source_ref({}) == ""  # no renderable parts → empty, never crashes


def test_table_cell_is_one_based_with_header():
    # 0-based table/row/column → 1-based label, matches the WordViewer anchor legend.
    ref = {"kind": "table_cell", "table": 3, "row": 1, "column": 2, "header": "设备编号"}
    assert format_source_ref(ref) == "表 4 · 行 2 · 列 3 · 设备编号"


def test_section_and_paragraph_locators():
    assert format_source_ref(
        {"kind": "section", "section": "共线评估", "heading_index": 5}
    ) == "§ 共线评估"
    assert format_source_ref(
        {"kind": "paragraph", "section": "基本性质", "paragraph_index": 7, "key": "制剂剂型"}
    ) == "§ 基本性质 · 制剂剂型"


def test_external_parts_fallback():
    assert format_source_ref({"system": "MDM", "entity": "评估小组"}) == "MDM · 评估小组"


def test_bool_is_not_a_positional_index():
    # bool is an int subclass; a stray True must NOT render as "表 2".
    assert format_source_ref({"table": True, "header": "x"}) == "x"


def test_enrichment_tag_surfaces_for_workshop_detection():
    # equipment enrichment writes archive/workshop provenance onto the dict under
    # ``enrichment`` (structured anchor preserved); it must appear in the rendered
    # text so the workshop-code substring scan still finds it.
    ref = {
        "kind": "table_cell", "table": 0, "header": "设备编号",
        "enrichment": "外部设备档案（record=RE64202；workshop=642车间）",
    }
    out = format_source_ref(ref)
    assert "设备编号" in out
    assert "642车间" in out


def test_extract_entity_context_survives_dict_source_ref():
    # THE reported crash: a 017-profile equipment edge carries a dict source_ref;
    # _extract_entity_context ran re.search on it → "got 'dict'". Must not raise,
    # and must still derive the workshop from the equipment code.
    edge = {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": "RE64202",
        "object_data_properties": [],
        "source_ref": {
            "kind": "table_cell", "table": 3, "row": 1, "column": 1, "header": "设备编号",
        },
    }
    ctx = _extract_entity_context([edge])
    assert "RE64202" in ctx
    assert "642车间" in ctx  # derived from the equipment code, no crash


def test_external_ref_with_enrichment_keeps_both():
    # Codex #2: an external-system ref (system/entity/record) that later receives an
    # equipment enrichment tag must keep BOTH. Enrichment AUGMENTS the base locator
    # (appended last) — it must never preempt it, as an earlier structure did by
    # returning before the external fallback ran, silently dropping the source system.
    ref = {
        "system": "MDM", "entity": "equipment", "record": "CT64201",
        "enrichment": "外部设备档案（record=CT64201；workshop=642车间）",
    }
    out = format_source_ref(ref)
    assert "MDM" in out and "equipment" in out and "CT64201" in out
    assert "642车间" in out  # enrichment still present, appended after the base


def test_detail_empty_parameter_falls_through_to_key():
    # Codex #3 (backend side kept as-is): truthy ``or`` — an empty ``parameter`` yields
    # to a meaningful ``key`` rather than rendering blank / raw JSON. The frontend
    # formatter was aligned to the same ``||`` precedence for byte-for-byte parity.
    assert format_source_ref({"parameter": "", "key": "制剂剂型"}) == "制剂剂型"


def test_workshop_tag_beats_spurious_coordinate():
    # Codex #1: a 017-profile dict can carry BOTH a structural coordinate whose 1-based
    # render contains a workshop-like digit (table=641 → "表 642") AND the authoritative
    # archive enrichment tag (workshop=646车间). Grouping must trust the explicit tag,
    # not the first workshop-shaped digit it scans in the display string. The equipment
    # code here encodes no workshop, so the tag is the only authority.
    edge = {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": "MX-01",
        "object_data_properties": [],
        "source_ref": {
            "kind": "table_cell", "table": 641, "header": "设备编号",
            "enrichment": "外部设备档案（record=MX-01；workshop=646车间）",
        },
    }
    ctx = _extract_entity_context([edge])
    assert "646车间" in ctx       # authoritative archive tag wins
    assert "642车间" not in ctx   # spurious "表 642" coordinate must not group it


def test_pure_enrichment_dict_renders_tag_exactly():
    # Codex round-2 #3: a dict carrying ONLY the enrichment tag (no section / coordinate /
    # detail / external field) must render the tag text verbatim — never raw JSON — so the
    # workshop parser still finds it. Exact-output guard against a future parts-order regression.
    assert format_source_ref(
        {"enrichment": "外部设备档案（record=Y；workshop=646车间）"}
    ) == "外部设备档案（record=Y；workshop=646车间）"


def test_detail_then_enrichment_exact_order():
    # Codex round-2 #3: detail (header/key/parameter) renders FIRST, enrichment appended
    # LAST, joined by " · ". Exact-output lock so enrichment can never preempt the locator.
    assert format_source_ref(
        {"header": "设备编号", "enrichment": "外部设备档案（record=Y；workshop=646车间）"}
    ) == "设备编号 · 外部设备档案（record=Y；workshop=646车间）"


def test_unenriched_equipment_code_beats_loose_scan():
    # Codex round-2 #3: the reordering's other half. For an UN-enriched edge, the equipment
    # code (646) is authoritative over a conflicting workshop digit that a loose scan would
    # pull from the display string (642). The old order ran loose-scan first and would have
    # mis-grouped this into 642车间; code-before-loose fixes it.
    edge = {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": "RE64602",          # equipment code → 646车间
        "object_data_properties": [],
        "source_ref": "§ 642车间参考资料",   # loose scan alone would say 642
    }
    ctx = _extract_entity_context([edge])
    assert "646车间" in ctx
    assert "642车间" not in ctx


def test_unenriched_coordinate_does_not_fabricate_workshop():
    # Codex round-3 #1: THE key regression. An unenriched 017-dict with a structural
    # coordinate whose 1-based render contains a workshop-like digit (table=641 → "表 642")
    # but NO enrichment tag and NO workshop-bearing equipment code must NOT be grouped into
    # 642车间 — it must be absent from the workshop set entirely. Before the structure-aware
    # fix, the loose scanner ran over the formatted "表 642 · …" string and silently
    # fabricated a 642车间 grouping.
    edge = {
        "predicate_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/usesEquipment",
        "object_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
        "object_text": "MX-01",            # no workshop digits in code
        "object_data_properties": [],
        "source_ref": {
            "kind": "table_cell", "table": 641, "row": 0, "column": 1, "header": "设备编号",
        },  # NO enrichment — purely structural coordinate
    }
    ctx = _extract_entity_context([edge])
    assert "642车间" not in ctx     # spurious coordinate must NOT fabricate a workshop
    assert "MX-01" in ctx          # equipment code still appears in context
