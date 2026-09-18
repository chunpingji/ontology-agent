"""Frozen equipment archive lookup; lexical identity is not relationship evidence.

Only the existing three equipment fields have predicate mappings. Other archive
metadata can assist a separate semantic review, but cannot become field facts.
The catalog never reads a live source, database, or the mutable archive file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy

from app.schemas.evidence import ExternalRecordProvenance
from app.services.extraction.equipment_source import EQUIPMENT_NS, PROCESS_EQUIPMENT_IRI
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.external_records import (
    ExternalRecordRegistry,
    ResolvedRecord,
    record_version,
)
from app.services.extraction.tool_validation.evidence import resolve_citation

SYSTEM = "mock_equipment"
DATASET = "equipment_archive"
FIELD_PREDICATES = {
    "equipment_id": EQUIPMENT_NS + "equipmentID",
    "name": EQUIPMENT_NS + "equipmentName",
    "specification": EQUIPMENT_NS + "modelSpecification",
}
_METADATA = ("workshop", "material", "location", "area_type")
_ADJACENT = re.compile(r"[ \t（():：）]*")


def _id_pattern(key: str) -> re.Pattern:
    # Chinese conjunctions may directly surround a complete archive identifier.
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(key) + r"(?![A-Za-z0-9_-])")


def _present_fields(fields: dict) -> dict:
    return {key: deepcopy(fields[key]) for key in FIELD_PREDICATES
            if key in fields and fields[key] != ""}


class FrozenEquipmentCatalog:
    """One supplied static archive snapshot, addressed by exact record key."""

    def __init__(self, records: dict[str, ResolvedRecord]):
        self._records = deepcopy(records)
        self._patterns = {key: _id_pattern(key) for key in self._records}
        self._names = sorted({record.fields["name"] for record in self._records.values()})
        self._snapshot_hash = evidence_hash({
            "system": SYSTEM, "dataset": DATASET,
            "records": [{"key": key, "version": record.version}
                        for key, record in sorted(self._records.items())],
        })
        self._registry = ExternalRecordRegistry({(SYSTEM, DATASET): self._resolve})

    @classmethod
    def from_records(cls, records: list[dict]) -> FrozenEquipmentCatalog:
        if not isinstance(records, list):
            raise ValueError("equipment_records_must_be_list")
        frozen = {}
        for raw in records:
            if not isinstance(raw, Mapping):
                raise ValueError("equipment_record_invalid")
            fields = deepcopy(dict(raw))
            key, name = fields.get("equipment_id"), fields.get("name")
            if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key)
                    or not isinstance(name, str) or not name.strip()):
                raise ValueError("equipment_record_identity_invalid")
            if "specification" in fields and not isinstance(fields["specification"], str):
                raise ValueError("equipment_record_specification_invalid")
            if key in frozen:
                raise ValueError("duplicate_equipment_record_key:" + key)
            frozen[key] = ResolvedRecord(
                system=SYSTEM, dataset=DATASET, key=key, version=record_version(fields),
                class_iri=PROCESS_EQUIPMENT_IRI, label_field="equipment_id", fields=fields,
                field_predicates=dict(FIELD_PREDICATES),
            )
        return cls(frozen)

    @property
    def snapshot_hash(self) -> str:
        return self._snapshot_hash

    def _resolve(self, key: str) -> ResolvedRecord | None:
        return deepcopy(self._records.get(key))

    def _base(self) -> dict:
        return {"execution_status": "completed", "system": SYSTEM, "dataset": DATASET,
                "catalog_snapshot_hash": self.snapshot_hash,
                "relationship_status": "not_checked", "fact_eligible": False}

    @staticmethod
    def _provenance(record: ResolvedRecord, field: str) -> dict:
        return ExternalRecordProvenance(
            system=SYSTEM, dataset=DATASET, record_key=record.key, record_version=record.version,
            field_path=field, value=deepcopy(record.fields[field]),
            record_snapshot=deepcopy(record.fields),
        ).model_dump(mode="json")

    def search(self, sources: dict, limit: int = 8) -> dict:
        """Return bounded exact candidates with complete counts and excluded keys."""
        if type(limit) is not int or limit < 1:
            raise ValueError("equipment_candidate_limit_invalid")
        candidates = []
        match_counts = {"exact_identifier": 0, "exact_name": 0}
        for key, record in sorted(self._records.items()):
            matches = []
            for ref, source in sources.items():
                text = source["text"]
                for method, pattern in (
                    ("exact_identifier", self._patterns[key]),
                    ("exact_name", re.compile(re.escape(record.fields["name"]))),
                ):
                    for match in pattern.finditer(text):
                        matches.append({"ref": ref, "quote": match[0], "start": match.start(),
                                        "end": match.end(), "method": method})
                        match_counts[method] += 1
            if matches:
                fields = _present_fields(record.fields)
                candidates.append({
                    "key": key, "version": record.version, "class_iri": record.class_iri,
                    "fields": fields,
                    "field_predicates": {field: FIELD_PREDICATES[field] for field in fields},
                    "metadata": {field: deepcopy(record.fields[field]) for field in _METADATA
                                 if record.fields.get(field) not in (None, "")},
                    "unmapped_metadata_only": True, "matches": matches,
                })
        candidates.sort(key=lambda candidate: (
            not any(match["method"] == "exact_identifier" for match in candidate["matches"]),
            candidate["key"],
        ))
        selected, excluded = candidates[:limit], candidates[limit:]
        return {**self._base(), "tool": "search_equipment_archive", "candidates": selected,
                "identity_status": "not_checked", "candidate_overflow": bool(excluded),
                "budget_excluded": [{"key": item["key"], "version": item["version"],
                                     "reason": "candidate_limit"} for item in excluded],
                "coverage": {
                    "source_units": len(sources), "archive_records": len(self._records),
                    "inspected_records": len(self._records), "matched_records": len(candidates),
                    "returned_records": len(selected), "excluded_records": len(excluded),
                    "candidate_limit": limit, "matches_by_method": match_counts,
                }}

    def _conflicting_name(self, text: str, mentions: list, record: ResolvedRecord) -> bool:
        expected = record.fields["name"]
        all_identifiers = [(key, match) for key, pattern in self._patterns.items()
                           for match in pattern.finditer(text)]
        for name in self._names:
            # Nested lexical names are not sufficient proof of a contradiction.
            if name in expected or expected in name:
                continue
            for match in re.finditer(re.escape(name), text):
                adjacent_keys = set()
                for other_key, other in all_identifiers:
                    if match.end() <= other.start():
                        gap = text[match.end():other.start()]
                    elif other.end() <= match.start():
                        gap = text[other.end():match.start()]
                    else:
                        continue
                    if _ADJACENT.fullmatch(gap):
                        adjacent_keys.add(other_key)
                # A list such as ID1 name2 ID2 does not assign name2 to ID1.
                if adjacent_keys != {record.key}:
                    continue
                for mention in mentions:
                    if match.end() <= mention.start():
                        gap = text[match.end():mention.start()]
                    elif mention.end() <= match.start():
                        gap = text[mention.end():match.start()]
                    else:
                        continue
                    if _ADJACENT.fullmatch(gap):
                        return True
        return False

    def validate_link(self, key: str, version: str, citation: dict, sources: dict) -> dict:
        """Validate one lexical mention's archive identity, never its document role."""
        result = {**self._base(), "validation_status": "failed",
                  "identity_status": "undetermined", "issues": [], "external_record": None,
                  "citation": None}
        record = self._records.get(key)
        if record is None:
            result["issues"] = ["external_record_unavailable"]
            return result
        provenance = self._provenance(record, "equipment_id")
        provenance["record_version"] = version
        try:
            record = self._registry.resolve(ExternalRecordProvenance.model_validate(provenance))
        except ValueError as exc:
            result["issues"] = [str(exc)]
            return result
        result["external_record"] = self._provenance(record, "equipment_id")
        try:
            resolved = resolve_citation(sources, citation)
        except ValueError as exc:
            result["issues"] = [str(exc)]
            return result
        result["citation"] = resolved
        text = sources[resolved["ref"]]["text"]
        # Match against the full unit so a cropped quote cannot bypass ID boundaries.
        identifiers = {
            other_key: [match for match in pattern.finditer(text)
                        if resolved["start"] <= match.start() and match.end() <= resolved["end"]]
            for other_key, pattern in self._patterns.items()
        }
        identifiers = {other_key: matches for other_key, matches in identifiers.items() if matches}
        if len(identifiers) > 1:
            result.update(validation_status="incomplete",
                          issues=["multiple_identifiers_in_citation"])
        elif identifiers and key not in identifiers:
            result.update(identity_status="unsupported", issues=["identifier_record_mismatch"])
        elif key not in identifiers:
            result.update(validation_status="incomplete", issues=["exact_identifier_required"])
        elif self._conflicting_name(text, identifiers[key], record):
            result.update(identity_status="unsupported",
                          issues=["equipment_name_identifier_conflict"])
        else:
            result.update(validation_status="passed", identity_status="supported")
        return result

    def external_fields(self, key: str) -> dict:
        """Return only existing equipment predicates, with static external provenance."""
        record = self._records.get(key)
        result = {**self._base(), "key": key, "version": None, "class_iri": None,
                  "validation_status": "failed", "issues": ["external_record_unavailable"],
                  "fields": []}
        if record is not None:
            result.update(
                version=record.version, class_iri=record.class_iri, validation_status="passed",
                issues=[], fields=[
                    {"field_path": field, "predicate_iri": FIELD_PREDICATES[field],
                     "value": value, "external_record": self._provenance(record, field)}
                    for field, value in _present_fields(record.fields).items()
                ],
            )
        return result
