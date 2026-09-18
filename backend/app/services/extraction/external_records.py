"""Allowlisted structured-record adapters; request-supplied snapshots are not authority."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import ConfigDict, Field, model_validator

from app.schemas.evidence import EvidenceModel, ExternalRecordProvenance
from app.services.extraction.evidence_identity import canonical_json, evidence_hash, stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    ExternalCandidate,
    ExternalMatch,
    ExternalMetadata,
    IdentifierProposal,
    IdentityKeySpec,
    MappedExternalField,
    Quote,
)


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


class IdentityKeyComponent(EvidenceModel):
    model_config = ConfigDict(strict=True)
    predicate_iri: str = Field(min_length=1)
    quote: Quote
    value: str = Field(min_length=1)

    @model_validator(mode="after")
    def value_is_quoted(self):
        if self.value != self.quote.text:
            raise ValueError("identity_key_value_not_quoted")
        return self


class InstanceQuery(EvidenceModel):
    model_config = ConfigDict(strict=True)
    source_ids: list[str] = Field(min_length=1)
    class_iri: str = Field(min_length=1)
    name_quotes: list[Quote]
    key_components: list[IdentityKeyComponent]
    limit: int = Field(ge=1)

    @model_validator(mode="after")
    def bounded_criteria(self):
        if any(not value.strip() for value in self.source_ids):
            raise ValueError("instance_source_id_blank")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("instance_source_id_duplicate")
        predicates = [item.predicate_iri for item in self.key_components]
        if len(predicates) != len(set(predicates)):
            raise ValueError("identity_key_component_duplicate")
        if not self.name_quotes and not self.key_components:
            raise ValueError("instance_query_source_required")
        if any(not quote.text.strip() for quote in self.name_quotes):
            raise ValueError("instance_name_quote_blank")
        return self


class InstanceSearchResult(EvidenceModel):
    candidates: list[ExternalCandidate]
    searched_sources: list[str]
    incomplete_sources: list[str]
    excluded_count: int | None = Field(ge=0)

    @model_validator(mode="after")
    def explicit_completeness(self):
        if self.excluded_count is None and not self.incomplete_sources:
            raise ValueError("unknown_excluded_count_requires_incomplete_source")
        return self


class ExternalInstanceReader(Protocol):
    def search(self, query: InstanceQuery) -> InstanceSearchResult: ...

    def resolve(self, provenance: ExternalRecordProvenance) -> ResolvedRecord: ...


def build_instance_query(
    *,
    source_ids: list[str],
    class_iri: str,
    name_quotes: list[Quote],
    identifier_quotes: list[IdentifierProposal],
    identity_key: IdentityKeySpec | None,
    owner_scope_by_evidence: Mapping[str, str],
    limit: int,
) -> InstanceQuery:
    """Project authorized quotes into one declared key; never combine owner scopes.

    The caller has already resolved every quote against its authorized frozen IR.
    Scope IDs are controller evidence bindings, never model-supplied identifiers.
    A partial composite key remains unusable; source names may still recall it.
    """
    components = []
    if identity_key is not None:
        if identity_key.class_iri != class_iri:
            raise ValueError("identity_key_class_mismatch")
        selected = {
            iri: [item.value_quote for item in identifier_quotes if item.predicate_iri == iri]
            for iri in identity_key.property_iris
        }
        if selected and all(len(quotes) == 1 for quotes in selected.values()):
            quotes = [values[0] for values in selected.values()]
            scopes = {owner_scope_by_evidence.get(quote.evidence_id) for quote in quotes}
            name_scopes = {owner_scope_by_evidence.get(quote.evidence_id) for quote in name_quotes}
            if None in scopes or len(scopes) != 1 or (name_scopes and name_scopes != scopes):
                raise ValueError("identity_key_owner_scope_unproven")
            components = [
                IdentityKeyComponent(
                    predicate_iri=iri,
                    quote=values[0],
                    value=values[0].text,
                )
                for iri, values in selected.items()
            ]
    return InstanceQuery(
        source_ids=source_ids,
        class_iri=class_iri,
        name_quotes=name_quotes,
        key_components=components,
        limit=limit,
    )


def _external_text(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


class FrozenInstanceReader:
    """Explicit source snapshots and field mappings; no default business source.

    Exact names/aliases and all components of one declared key recall candidates.
    They never validate identity or turn archive metadata into document facts.
    """

    def __init__(
        self,
        sources: Mapping[str, Sequence[ResolvedRecord]],
        *,
        name_fields: Mapping[str, Sequence[str]],
        alias_fields: Mapping[str, Sequence[str]] | None = None,
        incomplete_sources: Sequence[str] = (),
    ):
        self._sources = deepcopy({key: tuple(records) for key, records in sources.items()})
        self._names = {key: tuple(fields) for key, fields in name_fields.items()}
        self._aliases = {key: tuple(fields) for key, fields in (alias_fields or {}).items()}
        self._incomplete = frozenset(incomplete_sources)
        if (set(self._names) | set(self._aliases) | self._incomplete) - set(self._sources):
            raise ValueError("instance_source_configuration_unknown")
        records_by_dataset = {}
        for source_id, records in self._sources.items():
            if not source_id.strip():
                raise ValueError("instance_source_id_blank")
            datasets = {(record.system, record.dataset) for record in records}
            if len(datasets) > 1 or datasets & set(records_by_dataset):
                raise ValueError("instance_source_dataset_ambiguous")
            keys = [record.key for record in records]
            if len(keys) != len(set(keys)):
                raise ValueError("instance_record_key_duplicate")
            if datasets:
                records_by_dataset[next(iter(datasets))] = {
                    record.key: record for record in records
                }
        self._registry = ExternalRecordRegistry(
            {
                dataset: (lambda key, records=records: deepcopy(records.get(key)))
                for dataset, records in records_by_dataset.items()
            }
        )

    def resolve(self, provenance: ExternalRecordProvenance) -> ResolvedRecord:
        return self._registry.resolve(provenance)

    def search(self, query: InstanceQuery) -> InstanceSearchResult:
        candidates = []
        searched, incomplete = [], []
        for source_id in query.source_ids:
            records = self._sources.get(source_id)
            if records is None:
                incomplete.append(source_id)
                continue
            searched.append(source_id)
            if source_id in self._incomplete:
                incomplete.append(source_id)
            for record in sorted(records, key=lambda item: item.key):
                if record.class_iri != query.class_iri:
                    continue
                matches = []
                for quote in query.name_quotes:
                    for kind, fields in (
                        ("name", self._names.get(source_id, ())),
                        ("alias", self._aliases.get(source_id, ())),
                    ):
                        for field in fields:
                            raw = record.fields.get(field)
                            values = raw if isinstance(raw, list) else [raw]
                            if quote.text in values:
                                matches.append(
                                    ExternalMatch(
                                        predicate_iri=record.field_predicates.get(field),
                                        document_quote=quote,
                                        record_field=field,
                                        record_value=quote.text,
                                        match_kind=kind,
                                    )
                                )
                key_matches = []
                for component in query.key_components:
                    fields = [
                        field
                        for field, predicate in record.field_predicates.items()
                        if predicate == component.predicate_iri
                        and field in record.fields
                        and _external_text(record.fields[field]) == component.value
                    ]
                    if not fields:
                        break
                    key_matches.append(
                        ExternalMatch(
                            predicate_iri=component.predicate_iri,
                            document_quote=component.quote,
                            record_field=sorted(fields)[0],
                            record_value=component.value,
                            match_kind="exact_key"
                            if len(query.key_components) == 1
                            else "key_component",
                        )
                    )
                if len(key_matches) == len(query.key_components):
                    matches.extend(key_matches)
                if not matches:
                    continue
                candidates.append(
                    ExternalCandidate(
                        candidate_id=stable_id(
                            "external-candidate",
                            [
                                source_id,
                                record.key,
                                record.version,
                                record.class_iri,
                                matches,
                            ],
                        ),
                        source_id=source_id,
                        system=record.system,
                        dataset=record.dataset,
                        record_key=record.key,
                        record_version=record.version,
                        class_iri=record.class_iri,
                        matches=matches,
                        mapped_fields=[
                            MappedExternalField(
                                predicate_iri=record.field_predicates[field],
                                raw_value=_external_text(value),
                                datatype_iri=None,
                            )
                            for field, value in sorted(record.fields.items())
                            if field in record.field_predicates
                        ],
                        metadata=[
                            ExternalMetadata(name=field, value=_external_text(value))
                            for field, value in sorted(record.fields.items())
                            if field not in record.field_predicates
                        ],
                    )
                )
        return InstanceSearchResult(
            candidates=candidates[: query.limit],
            searched_sources=searched,
            incomplete_sources=incomplete,
            excluded_count=None if incomplete else max(0, len(candidates) - query.limit),
        )
