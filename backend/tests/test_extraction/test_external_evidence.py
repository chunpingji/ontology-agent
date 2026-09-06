import pytest

from app.schemas.evidence import Candidate, CandidateRef, ExternalRecordProvenance
from app.services.extraction.candidate_validation import validate_entered_candidate
from app.services.extraction.external_records import (
    ExternalRecordRegistry,
    ResolvedRecord,
    configured_external_records,
)
from app.services.extraction.literal_normalizer import normalize_literal


def test_external_record_value_version_identity_and_binding_are_independent():
    record = ResolvedRecord(
        "registry",
        "items",
        "E1",
        "v7",
        "urn:test:Equipment",
        "id",
        {"id": "E1", "material": "steel"},
        {"material": "urn:test:material"},
    )
    records = ExternalRecordRegistry(
        {("registry", "items"): lambda key: record if key == "E1" else None}
    )
    schema = {
        "urn:test:Equipment": {"properties": [{"iri": "urn:test:material", "datatype": "string"}]}
    }

    def source(field, value):
        return ExternalRecordProvenance(
            system="registry",
            dataset="items",
            record_key="E1",
            record_version="v7",
            field_path=field,
            value=value,
            record_snapshot={"forged": True},
        )

    entity = validate_entered_candidate(
        Candidate(
            candidate_id="E",
            kind="entity",
            text="E1",
            class_iri="urn:test:Equipment",
            provenance=[source("id", "E1")],
        ),
        {},
        schema,
        actor="analyst",
        reason="引用档案",
        records=records,
    )
    assert entity.identity["record_key"] == "E1"
    prop = Candidate(
        candidate_id="value",
        kind="property",
        subject=CandidateRef(candidate_id="E", revision=1),
        predicate_iri="urn:test:material",
        literal=normalize_literal("steel"),
        provenance=[source("material", "steel")],
    )
    validated = validate_entered_candidate(
        prop, {"E": entity}, schema, actor="analyst", reason="引用字段", records=records
    )
    assert validated.validation_status == "passed" and validated.scope is None
    assert validated.provenance[0].record_snapshot == record.fields
    assert validated.bindings[0].record_mapping["field_path"] == "material"
    prop.provenance[0].record_version = "v6"
    with pytest.raises(ValueError, match="version_mismatch"):
        validate_entered_candidate(
            prop, {"E": entity}, schema, actor="analyst", reason="过期", records=records
        )
    prop.provenance[0].record_version = "v7"
    entity.identity["record_key"] = "E2"
    with pytest.raises(ValueError, match="different subject"):
        validate_entered_candidate(
            prop, {"E": entity}, schema, actor="analyst", reason="错主体", records=records
        )


def test_equipment_adapter_reports_actual_mock_version_and_no_material_object_guess():
    from app.services.extraction.equipment_source import MockEquipmentSource

    record = MockEquipmentSource().evidence_record("CT64201")
    assert record and record.system == "mock_equipment" and len(record.version) == 64
    assert "material" not in record.field_predicates  # no text-to-object inference
    source = ExternalRecordProvenance(
        system=record.system,
        dataset=record.dataset,
        record_key=record.key,
        record_version=record.version,
        field_path="specification",
        value=record.fields["specification"],
    )
    assert configured_external_records().resolve(source) == record
