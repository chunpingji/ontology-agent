"""Static archive identities remain separate from document fact and relation proof."""

from copy import deepcopy

import pytest

from app.schemas.evidence import ExternalRecordProvenance
from app.services.extraction.equipment_source import EQUIPMENT_NS, PROCESS_EQUIPMENT_IRI
from app.services.extraction.external_records import record_version
from app.services.extraction.tool_validation.mock_entities import FrozenEquipmentCatalog


@pytest.fixture()
def records():
    return [
        {"equipment_id": "PF64216", "name": "钛棒过滤器", "specification": "10英寸3芯",
         "material": "钛", "location": "房间A", "workshop": "车间1", "area_type": "洁净区"},
        {"equipment_id": "PF64616", "name": "钛棒过滤器", "specification": "10英寸3芯"},
        {"equipment_id": "CT64610", "name": "离心机", "specification": "600"},
        {"equipment_id": "RE64611", "name": "搪玻璃反应釜", "specification": "200L"},
    ]


@pytest.fixture()
def catalog(records):
    return FrozenEquipmentCatalog.from_records(records)


def sources(text):
    return {"u0": {"text": text}}


def validate(catalog, text, key="PF64216", quote=None, **coordinates):
    return catalog.validate_link(
        key, catalog.external_fields(key)["version"],
        {"ref": "u0", "quote": quote if quote is not None else text, **coordinates}, sources(text),
    )


def test_freezes_input_and_returned_values_with_order_independent_snapshot(records):
    expected = deepcopy(records)
    catalog = FrozenEquipmentCatalog.from_records(records)
    assert catalog.snapshot_hash == FrozenEquipmentCatalog.from_records(records[::-1]).snapshot_hash
    before = catalog.snapshot_hash
    records[0]["material"] = "不锈钢"
    assert catalog.snapshot_hash == before
    assert catalog.snapshot_hash != FrozenEquipmentCatalog.from_records(records).snapshot_hash
    candidate = catalog.search(sources("PF64216"))["candidates"][0]
    assert candidate["version"] == record_version(expected[0])
    candidate["fields"]["name"] = "changed"
    candidate["metadata"]["material"] = "changed"
    provenance = catalog.external_fields("PF64216")["fields"][0]["external_record"]
    provenance["record_snapshot"]["material"] = "changed"
    fresh = catalog.search(sources("PF64216"))["candidates"][0]
    assert fresh["fields"]["name"] == "钛棒过滤器"
    assert fresh["metadata"]["material"] == "钛"


def test_duplicate_keys_rejected_even_when_fields_identical(records):
    with pytest.raises(ValueError, match="duplicate_equipment_record_key:PF64216"):
        FrozenEquipmentCatalog.from_records(records + [records[0]])


@pytest.mark.parametrize("change", [
    {"equipment_id": ""}, {"equipment_id": "PF 64216"}, {"equipment_id": 64216},
    {"name": " "}, {"name": None}, {"specification": 600},
])
def test_malformed_records_rejected(records, change):
    with pytest.raises(ValueError, match="equipment_record_"):
        FrozenEquipmentCatalog.from_records([{**records[0], **change}])


@pytest.mark.parametrize("text", [
    "XPF64216", "PF64216X", "PF642160", "_PF64216", "PF64216_", "pf64216",
])
def test_exact_identifier_boundaries_are_case_sensitive(catalog, text):
    assert catalog.search(sources(text))["candidates"] == []
    if "PF64216" in text:
        result = validate(catalog, text, quote="PF64216")
        assert result["identity_status"] == "undetermined"


@pytest.mark.parametrize("text", ["MOCK-RE64611", "RE64611-OLD", "RE64611-1"])
def test_identifier_cannot_be_cropped_from_a_hyphenated_key(catalog, text):
    assert catalog.search(sources(text))["candidates"] == []
    result = validate(catalog, text, key="RE64611", quote="RE64611")
    assert result["identity_status"] == "undetermined"
    assert result["issues"] == ["exact_identifier_required"]


def test_complete_hyphenated_archive_key_still_matches_without_shorter_key_leak(records):
    records.append({"equipment_id": "MOCK-RE64611", "name": "测试设备"})
    catalog = FrozenEquipmentCatalog.from_records(records)
    result = catalog.search(sources("使用MOCK-RE64611或PF64216"))
    assert {candidate["key"] for candidate in result["candidates"]} == {
        "MOCK-RE64611", "PF64216"}
    linked = validate(catalog, "使用MOCK-RE64611", key="MOCK-RE64611", quote="MOCK-RE64611")
    assert linked["identity_status"] == "supported"


def test_alternative_identifiers_produce_distinct_candidates_without_relation(catalog):
    result = catalog.search(sources("使用PF64216或PF64616"))
    assert [candidate["key"] for candidate in result["candidates"]] == ["PF64216", "PF64616"]
    assert [candidate["matches"] for candidate in result["candidates"]] == [
        [{"ref": "u0", "quote": "PF64216", "start": 2, "end": 9, "method": "exact_identifier"}],
        [{"ref": "u0", "quote": "PF64616", "start": 10, "end": 17, "method": "exact_identifier"}],
    ]
    assert result["relationship_status"] == "not_checked"
    assert result["fact_eligible"] is False
    ambiguous = validate(catalog, "PF64216或PF64616")
    assert ambiguous["identity_status"] == "undetermined"
    assert ambiguous["issues"] == ["multiple_identifiers_in_citation"]
    mention = validate(catalog, "PF64216或PF64616", quote="PF64216")
    assert mention["identity_status"] == "supported"
    assert mention["relationship_status"] == "not_checked"
    assert mention["fact_eligible"] is False


def test_search_does_not_join_units_and_keeps_every_real_occurrence(catalog):
    assert catalog.search({"u0": {"text": "PF64"}, "u1": {"text": "216"}})["candidates"] == []
    result = catalog.search(sources("PF64216 PF64216"))
    assert [match["start"] for match in result["candidates"][0]["matches"]] == [0, 8]
    assert result["coverage"]["matches_by_method"]["exact_identifier"] == 2


def test_names_never_resolve_identity_even_when_catalog_name_is_unique(catalog):
    result = catalog.search(sources("钛棒过滤器"))
    assert {item["key"] for item in result["candidates"]} == {"PF64216", "PF64616"}
    for name, key in [("钛棒过滤器", "PF64216"), ("离心机", "CT64610")]:
        link = validate(catalog, name, key=key)
        assert link["validation_status"] == "incomplete"
        assert link["identity_status"] == "undetermined"
        assert link["issues"] == ["exact_identifier_required"]


def test_budget_counts_all_matches_and_prioritizes_exact_identifier(catalog):
    result = catalog.search(sources("钛棒过滤器；RE64611；离心机"), limit=1)
    assert [item["key"] for item in result["candidates"]] == ["RE64611"]
    assert result["candidate_overflow"] is True
    assert {item["key"] for item in result["budget_excluded"]} == {"CT64610", "PF64216", "PF64616"}
    assert all(item["version"] and item["reason"] == "candidate_limit"
               for item in result["budget_excluded"])
    assert result["coverage"] == {
        "source_units": 1, "archive_records": 4, "inspected_records": 4,
        "matched_records": 4, "returned_records": 1, "excluded_records": 3,
        "candidate_limit": 1, "matches_by_method": {"exact_identifier": 1, "exact_name": 3},
    }


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2", None])
def test_invalid_budget_rejected(catalog, limit):
    with pytest.raises(ValueError, match="equipment_candidate_limit_invalid"):
        catalog.search(sources("PF64216"), limit=limit)


def test_valid_link_has_static_provenance_and_separate_local_citation(catalog, records):
    result = validate(catalog, "设备PF64216", quote="PF64216")
    assert result["validation_status"] == "passed"
    assert result["identity_status"] == "supported"
    assert result["issues"] == []
    provenance = ExternalRecordProvenance.model_validate(result["external_record"])
    assert provenance.system == "mock_equipment"
    assert provenance.dataset == "equipment_archive"
    assert provenance.record_version == record_version(records[0])
    assert provenance.record_snapshot == records[0]
    assert provenance.fetched_at is None
    assert provenance.identity_match_evidence == []
    assert result["citation"] == {"ref": "u0", "quote": "PF64216", "start": 2, "end": 9}


def test_missing_and_stale_records_are_mechanical_failures_not_semantic_denials(catalog):
    for key, version, issue in [
        ("DE64603", "v1", "external_record_unavailable"),
        ("PF64216", "v1", "external_record_version_mismatch"),
    ]:
        result = catalog.validate_link(key, version, {"ref": "u0", "quote": key}, sources(key))
        assert result["validation_status"] == "failed"
        assert result["identity_status"] == "undetermined"
        assert result["issues"] == [issue]
        assert result["external_record"] is None


@pytest.mark.parametrize(("citation", "issue"), [
    ({"ref": "u99", "quote": "PF64216"}, "citation_ref_missing"),
    ({"ref": "u0", "quote": "PF64616"}, "citation_quote_not_in_source"),
    ({"ref": "u0", "quote": "PF64216", "start": 1, "end": 8}, "citation_coordinates_invalid"),
    ({"ref": "u0", "quote": "PF64216", "start": True}, "citation_coordinates_invalid"),
])
def test_forged_citations_rejected(catalog, citation, issue):
    result = catalog.validate_link(
        "PF64216", catalog.external_fields("PF64216")["version"], citation, sources("PF64216"),
    )
    assert result["identity_status"] == "undetermined"
    assert result["validation_status"] == "failed"
    assert result["issues"] == [issue]


def test_repeated_quotes_require_explicit_coordinates(catalog):
    assert validate(catalog, "PF64216 PF64216", quote="PF64216")["issues"] == [
        "citation_quote_ambiguous"]
    result = validate(catalog, "PF64216 PF64216", quote="PF64216", start=8, end=15)
    assert result["identity_status"] == "supported"
    assert result["citation"]["start"] == 8


def test_other_identifier_is_explicitly_unsupported(catalog):
    result = validate(catalog, "PF64616")
    assert result["identity_status"] == "unsupported"
    assert result["issues"] == ["identifier_record_mismatch"]


@pytest.mark.parametrize("text", ["离心机（RE64611）", "RE64611（离心机）", "离心机：RE64611"])
def test_adjacent_conflicting_name_cannot_be_hidden_by_id_only_quote(catalog, text):
    result = validate(catalog, text, key="RE64611", quote="RE64611")
    assert result["identity_status"] == "unsupported"
    assert result["issues"] == ["equipment_name_identifier_conflict"]


def test_unrelated_name_elsewhere_is_not_an_identity_conflict(catalog):
    result = validate(catalog, "搪玻璃反应釜 RE64611 连接离心机 CT64610",
                      key="RE64611", quote="RE64611")
    assert result["identity_status"] == "supported"
    result = validate(catalog, "离心机\nRE64611", key="RE64611", quote="RE64611")
    assert result["identity_status"] == "supported"


def test_name_between_two_ids_does_not_prove_a_name_conflict(catalog):
    result = validate(catalog, "RE64611 离心机 CT64610", key="RE64611", quote="RE64611")
    assert result["identity_status"] == "supported"
    assert result["relationship_status"] == "not_checked"


def test_external_fields_do_not_infer_material_location_or_subclass(records):
    records[0].update(class_iri=EQUIPMENT_NS + "TitaniumFilter",
                      field_predicates={"material": "bad"})
    catalog = FrozenEquipmentCatalog.from_records(records)
    result = catalog.external_fields("PF64216")
    assert result["class_iri"] == PROCESS_EQUIPMENT_IRI
    assert {field["predicate_iri"] for field in result["fields"]} == {
        EQUIPMENT_NS + "equipmentID", EQUIPMENT_NS + "equipmentName",
        EQUIPMENT_NS + "modelSpecification",
    }
    for field in result["fields"]:
        provenance = ExternalRecordProvenance.model_validate(field["external_record"])
        assert provenance.field_path == field["field_path"]
        assert provenance.value == field["value"]
        assert provenance.record_snapshot == records[0]
    candidate = catalog.search(sources("PF64216"))["candidates"][0]
    assert candidate["unmapped_metadata_only"] is True
    assert set(candidate["metadata"]) == {"material", "workshop", "location", "area_type"}
    assert set(candidate["fields"]) == {"equipment_id", "name", "specification"}
    assert result["fact_eligible"] is False


def test_missing_fields_are_not_generated_and_missing_record_is_explicit():
    catalog = FrozenEquipmentCatalog.from_records([{"equipment_id": "A1", "name": "设备"}])
    assert len(catalog.external_fields("A1")["fields"]) == 2
    assert catalog.external_fields("missing")["issues"] == ["external_record_unavailable"]
    assert catalog.external_fields("missing")["fields"] == []


def test_supplied_snapshot_never_reads_configured_source_or_archive(catalog, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A frozen catalog must never query the mutable archive or live source")

    monkeypatch.setattr("app.services.extraction.equipment_source.get_equipment_source", forbidden)
    monkeypatch.setattr("app.services.extraction.external_records._equipment_record", forbidden)
    monkeypatch.setattr("pathlib.Path.read_text", forbidden)
    assert catalog.search(sources("PF64216"))["candidates"]
    assert validate(catalog, "PF64216")["identity_status"] == "supported"
    assert catalog.external_fields("PF64216")["validation_status"] == "passed"
