"""Allowlisted structured-record adapters; request-supplied snapshots are not authority."""

from dataclasses import dataclass
from typing import Any, Callable

from app.schemas.evidence import ExternalRecordProvenance
from app.services.extraction.evidence_identity import evidence_hash


@dataclass(frozen=True)
class ResolvedRecord:
    system: str
    dataset: str
    key: str
    version: str
    class_iri: str
    label_field: str
    fields: dict[str, Any]
    field_predicates: dict[str, str]


class ExternalRecordRegistry:
    def __init__(self, adapters: dict[tuple[str, str], Callable[[str], ResolvedRecord | None]]):
        self.adapters = adapters

    def resolve(self, source: ExternalRecordProvenance) -> ResolvedRecord:
        adapter = self.adapters.get((source.system, source.dataset))
        record = adapter(source.record_key) if adapter else None
        if record is None:
            raise ValueError("external_record_unavailable")
        if (record.system, record.dataset, record.key) != (
            source.system,
            source.dataset,
            source.record_key,
        ):
            raise ValueError("external_record_identity_mismatch")
        if record.version != source.record_version:
            raise ValueError("external_record_version_mismatch")
        if (
            source.field_path not in record.fields
            or record.fields[source.field_path] != source.value
        ):
            raise ValueError("external_record_field_mismatch")
        return record


def _equipment_record(key: str) -> ResolvedRecord | None:
    # Explicitly identifies the existing mock dataset. It does not pretend to
    # have fetched a live equipment service or to be a Word fact source.
    from app.services.extraction.equipment_source import get_equipment_source

    source = get_equipment_source()
    resolver = getattr(source, "evidence_record", None)
    return resolver(key) if resolver else None


def configured_external_records() -> ExternalRecordRegistry:
    return ExternalRecordRegistry({("mock_equipment", "equipment_archive"): _equipment_record})


def record_version(fields: dict) -> str:
    return evidence_hash(fields)
