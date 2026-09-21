"""Record-scoped discovery contracts; no synthetic graph subject or predicate."""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import ConfigDict, Field, model_serializer, model_validator

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.claim_protocol import (
    ConstraintIssue,
    ExtractionProfile,
    IdentityKeySpec,
    QuantityPolicy,
    SchemaCard,
)
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    EdgeSpec,
    OntologySnapshot,
    SlotSpec,
    TraversalScope,
    VersionedRef,
)
from app.services.extraction.ontology_guided.ontology_plan import compile_class_predicates
from app.services.extraction.ontology_guided.schema_region_routing import (
    SchemaRegionRoutingPolicy,
)

RECORD_PIPELINE = "record-entity-first-v1"
RECORD_PROTOCOL = "ontology-record-discovery-v1"


class ContextualDiscoveryPolicy(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["contextual-discovery-v2"] = "contextual-discovery-v2"
    max_section_chars: int = Field(default=240, ge=1)
    max_group_chars: int = Field(default=1200, ge=1)
    max_group_records: int = Field(default=8, ge=1, le=32)
    max_table_rows_per_group: int = Field(default=4, ge=1, le=4)
    max_attribute_candidates: int = Field(default=8, ge=1, le=32)
    max_disambiguation_attempts: int = Field(default=2, ge=1, le=2)


class RecordDiscoveryPolicy(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["record-discovery-v2"] = "record-discovery-v2"
    graph_phase: Literal[
        "candidate_graph", "evidence_verification", "evidence_review"
    ] = "evidence_verification"
    max_classes_per_card: int = Field(default=4, ge=1, le=32)
    endpoint_page_size: int = Field(default=8, ge=1, le=64)
    candidate_cards_per_record: int = Field(default=2, ge=1, le=16)
    minimum_similarity: float = Field(default=0.25, ge=-1, le=1, allow_inf_nan=False)
    max_feedback_reopens: int = Field(default=8, ge=0, le=8)
    attribute_calibration: Literal["source-observations-v1"] = "source-observations-v1"
    table_reading: Literal["bounded-table-rows-v2"] = "bounded-table-rows-v2"
    contextual: ContextualDiscoveryPolicy | None = None
    schema_region_routing: SchemaRegionRoutingPolicy | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if self.contextual is None:
            data.pop("contextual", None)
        if self.schema_region_routing is None:
            data.pop("schema_region_routing", None)
        return data


class RecordDiscoveryTask(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["record_discovery"] = "record_discovery"
    task_id: str = Field(min_length=1)
    claim_lineage_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    schema_card_id: str = Field(min_length=1)
    analysis_scope_ref: str = Field(min_length=1)
    dependency_hash: str = Field(min_length=1)
    scope: TraversalScope = Field(default_factory=TraversalScope.create)
    source_record_ids: list[str] = Field(default_factory=list)
    reading_section_ids: list[str] = Field(default_factory=list)
    purpose: Literal["entity_discovery", "property_disambiguation"] = "entity_discovery"
    field_id: str | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if not self.source_record_ids:
            data.pop("source_record_ids", None)
        if not self.reading_section_ids:
            data.pop("reading_section_ids", None)
        if self.purpose == "entity_discovery":
            data.pop("purpose", None)
        if self.field_id is None:
            data.pop("field_id", None)
        return data

    @model_validator(mode="after")
    def source_membership(self):
        if self.source_record_ids and (
            self.record_id not in self.source_record_ids
            or len(self.source_record_ids) != len(set(self.source_record_ids))
        ):
            raise ValueError("record_source_membership_invalid")
        if (self.purpose == "property_disambiguation") != (self.field_id is not None):
            raise ValueError("record_attribute_field_required")
        return self

    @classmethod
    def create(cls, *, run_fingerprint, record_id, schema_card_id,
               analysis_scope_ref, dependency_hash, source_record_ids=(), field_id=None,
               reading_section_ids=()):
        lineage = stable_id("record-discovery", [
            run_fingerprint, record_id, schema_card_id, analysis_scope_ref,
        ] if field_id is None else [run_fingerprint, "attribute-field", field_id])
        return cls(
            task_id=stable_id("record-discovery-task", [lineage, dependency_hash]),
            claim_lineage_id=lineage, record_id=record_id, schema_card_id=schema_card_id,
            analysis_scope_ref=analysis_scope_ref, dependency_hash=dependency_hash,
            source_record_ids=list(source_record_ids), field_id=field_id,
            reading_section_ids=list(reading_section_ids),
            purpose="property_disambiguation" if field_id else "entity_discovery",
        )


class RecordDiscoveryTarget(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["record_discovery"] = "record_discovery"
    target_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    document_context: DocumentContext
    schema_card_id: str = Field(min_length=1)
    analysis_scope_ref: str = Field(min_length=1)
    ontology_hash: str = Field(min_length=1)
    source_scope_hash: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)


class ClassPropertyCard(EvidenceModel):
    class_iri: str
    label: str
    description: str = ""
    parent_iris: list[str] = Field(default_factory=list)
    properties: list[SlotSpec]
    relationships: list[EdgeSpec] = Field(default_factory=list)
    quantity_policies: list[QuantityPolicy]
    identity_keys: list[IdentityKeySpec]
    unsupported_constraints: list[ConstraintIssue]

    def for_subject(self, subject: VersionedRef, *, parent) -> SchemaCard:
        return SchemaCard(
            schema_card_id=evidence_hash([parent.schema_card_id, self.class_iri, subject]),
            subject_ref=subject, class_iris=[self.class_iri],
            ontology_snapshot_id=parent.ontology_snapshot_id, menu_id=parent.schema_card_id,
            predicates=[*self.properties, *self.relationships],
            quantity_policies=self.quantity_policies,
            identity_keys=self.identity_keys, unsupported_constraints=self.unsupported_constraints,
        )


class RecordDiscoverySchemaCard(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["record_discovery"] = "record_discovery"
    schema_card_id: str
    ontology_snapshot_id: str
    analysis_scope_ref: str
    class_cards: list[ClassPropertyCard] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_classes(self):
        if len({card.class_iri for card in self.class_cards}) != len(self.class_cards):
            raise ValueError("duplicate_class_card")
        return self

    @property
    def class_iris(self):
        return [card.class_iri for card in self.class_cards]

    def for_class(self, class_iri):
        for card in self.class_cards:
            if card.class_iri == class_iri:
                return card
        raise ValueError("class_outside_menu")

    @property
    def predicates(self):
        # Transport/schema enum only; validation always resolves the owning class.
        return [predicate for card in self.class_cards
                for predicate in [*card.properties, *card.relationships]]

    @property
    def identity_keys(self):
        return [key for card in self.class_cards for key in card.identity_keys]


def merge_record_schema_cards(
    cards: list[RecordDiscoverySchemaCard],
) -> RecordDiscoverySchemaCard:
    """Merge routed execution cards into one bounded region request.

    Routing cards remain retrieval-only. This function combines the full
    execution cards selected for the same source region and rejects conflicting
    definitions instead of weakening either card.
    """
    if not cards:
        raise ValueError("record_schema_cards_required")
    ontology_snapshot_id = cards[0].ontology_snapshot_id
    analysis_scope_ref = cards[0].analysis_scope_ref
    def merge_items(left, right, *, key):
        merged = {}
        for item in [*left, *right]:
            identity = key(item)
            current = merged.get(identity)
            if current is not None and current != item:
                raise ValueError("record_schema_class_definition_conflict")
            merged[identity] = item
        return [merged[identity] for identity in sorted(merged)]

    def merge_class(left, right):
        if (left.label != right.label or left.description != right.description
                or left.parent_iris != right.parent_iris):
            raise ValueError("record_schema_class_definition_conflict")
        return left.model_copy(update={
            "properties": merge_items(
                left.properties, right.properties, key=lambda item: item.iri,
            ),
            "relationships": merge_items(
                left.relationships, right.relationships, key=lambda item: item.iri,
            ),
            "quantity_policies": merge_items(
                left.quantity_policies,
                right.quantity_policies,
                key=lambda item: item.predicate_iri,
            ),
            "identity_keys": merge_items(
                left.identity_keys,
                right.identity_keys,
                key=lambda item: item.declaration_ref,
            ),
            "unsupported_constraints": merge_items(
                left.unsupported_constraints,
                right.unsupported_constraints,
                key=lambda item: (item.predicate_iri, item.constraint_construct),
            ),
        })

    by_class = {}
    for card in cards:
        if (card.ontology_snapshot_id != ontology_snapshot_id
                or card.analysis_scope_ref != analysis_scope_ref):
            raise ValueError("record_schema_card_scope_mismatch")
        for class_card in card.class_cards:
            current = by_class.get(class_card.class_iri)
            by_class[class_card.class_iri] = (
                class_card if current is None else merge_class(current, class_card)
            )
    payload = {
        "ontology_snapshot_id": ontology_snapshot_id,
        "analysis_scope_ref": analysis_scope_ref,
        "class_cards": [by_class[iri] for iri in sorted(by_class)],
    }
    return RecordDiscoverySchemaCard(schema_card_id=evidence_hash(payload), **payload)


class DiscoveryCatalog(EvidenceModel):
    analysis_scope_ref: str
    root_class_iri: str
    class_depths: dict[str, int]
    # Paths retain position even where a class occurs at multiple depths.
    relation_routes: list[tuple[str, str, str, int]]


class CandidateChanges(EvidenceModel):
    created_entity_refs: list[VersionedRef] = Field(default_factory=list)
    changed_entity_refs: list[VersionedRef] = Field(default_factory=list)
    property_refs: list[VersionedRef] = Field(default_factory=list)
    affected_record_ids: list[str] = Field(default_factory=list)


def compile_discovery_catalog(
    ontology, root_class_iri, *, max_hops,
    focus_paths=(),
):
    """Traverse formal types once per route position, without claiming instance edges."""
    paths = tuple(tuple(path) for path in focus_paths)
    pending = deque([(root_class_iri, 0, ())])
    visited, depths, routes = set(), {}, set()
    while pending:
        class_iri, depth, prefix = pending.popleft()
        key = (class_iri, depth, prefix if paths else ())
        if key in visited:
            continue
        visited.add(key)
        depths[class_iri] = min(depth, depths.get(class_iri, depth))
        if depth >= max_hops:
            continue
        _, edges, _ = compile_class_predicates(
            ontology, class_iri,
        )
        for edge in edges:
            next_prefix = (*prefix, edge.iri)
            if edge.constraint_status != "resolved" or (
                paths and not any(path[:len(next_prefix)] == next_prefix for path in paths)
            ):
                continue
            for target in edge.range_class_iris:
                if target not in ontology.classes:
                    continue
                routes.add((class_iri, edge.iri, target, depth))
                pending.append((target, depth + 1, next_prefix))
    payload = dict(root_class_iri=root_class_iri, class_depths=depths,
                   relation_routes=sorted(routes))
    return DiscoveryCatalog(
        analysis_scope_ref=evidence_hash([ontology.snapshot_id, payload]), **payload,
    )


def compile_record_schema_card(
    ontology: OntologySnapshot, *, class_iris, analysis_scope_ref, profile: ExtractionProfile,
) -> RecordDiscoverySchemaCard:
    cards = []
    numeric = {"http://www.w3.org/2001/XMLSchema#" + name for name in (
        "decimal", "double", "float", "integer", "int", "nonNegativeInteger",
        "positiveInteger", "long", "short", "byte", "nonPositiveInteger", "negativeInteger",
    )}
    for iri in sorted(set(class_iris)):
        properties, relationships, _ = compile_class_predicates(ontology, iri)
        ids = {prop.iri for prop in properties}
        policies = [p.model_copy(deep=True) for p in profile.quantity_policies
                    if p.predicate_iri in ids]
        if len({p.predicate_iri for p in policies}) != len(policies):
            raise ValueError("conflicting_quantity_policies")
        declared = {p.predicate_iri for p in policies}
        for prop in properties:
            if (prop.iri not in declared and prop.constraint_status == "resolved"
                    and len(prop.datatype_iris) == 1 and prop.datatype_iris[0] in numeric):
                policies.append(QuantityPolicy(
                    predicate_iri=prop.iri, allowed_forms=["scalar"], endpoint_role=None,
                    allowed_target_units=[prop.canonical_unit] if prop.canonical_unit else [],
                    unit_requirement="physical" if prop.canonical_unit else "not_declared",
                    declaration_ref=f"{ontology.snapshot_id}:{iri}:{prop.iri}",
                ))
        cards.append(ClassPropertyCard(
            class_iri=iri, label=ontology.classes[iri].label, properties=properties,
            relationships=relationships,
            description=ontology.classes[iri].description,
            parent_iris=sorted(ontology.classes[iri].parent_iris),
            quantity_policies=policies,
            identity_keys=[key.model_copy(deep=True) for key in profile.identity_keys
                           if key.class_iri == iri],
            unsupported_constraints=[ConstraintIssue(
                predicate_iri=p.iri, construct="predicate_constraint",
                reason_code="constraint_unresolved",
            ) for p in [*properties, *relationships] if p.constraint_status != "resolved"],
        ))
    payload = dict(ontology_snapshot_id=ontology.snapshot_id,
                   analysis_scope_ref=analysis_scope_ref, class_cards=cards)
    return RecordDiscoverySchemaCard(schema_card_id=evidence_hash(payload), **payload)


def compile_partitioned_record_schema_cards(
    ontology: OntologySnapshot,
    *,
    class_iris,
    analysis_scope_ref: str,
    profile: ExtractionProfile,
    max_classes_per_card: int,
    max_card_tokens: int,
    token_counter,
) -> list[RecordDiscoverySchemaCard]:
    """Freeze bounded execution cards for one routed class scope."""
    if min(max_classes_per_card, max_card_tokens) < 1:
        raise ValueError("record_schema_card_partition_limit_invalid")
    result = []
    group = []
    for iri in sorted(set(class_iris)):
        trial = compile_record_schema_card(
            ontology,
            class_iris=[*group, iri],
            analysis_scope_ref=analysis_scope_ref,
            profile=profile,
        )
        # A single oversize class remains intact so request capacity handling
        # reports it explicitly instead of silently dropping its constraints.
        if group and (
            len(group) >= max_classes_per_card
            or token_counter(trial.model_dump_json()) > max_card_tokens
        ):
            result.append(compile_record_schema_card(
                ontology,
                class_iris=group,
                analysis_scope_ref=analysis_scope_ref,
                profile=profile,
            ))
            group = []
        group.append(iri)
    if group:
        result.append(compile_record_schema_card(
            ontology,
            class_iris=group,
            analysis_scope_ref=analysis_scope_ref,
            profile=profile,
        ))
    return result


def resolve_record_predicate(card, class_iri, predicate_iri):
    owner = card.for_class(class_iri)
    for prop in [*owner.properties, *owner.relationships]:
        if prop.iri == predicate_iri:
            return prop
    raise ValueError("predicate_outside_menu")


def reference_pages(task, index, nodes, origins, resolutions, classes, *, page_size):
    """Page optional candidates while keeping source-local and same-name groups intact."""
    from .reference_context import normalized_reference_name, select_reference_entities

    selected = select_reference_entities(
        task, index, nodes, origins, resolutions, classes, limit=None,
    )
    units = {unit.evidence_id for unit in index.by_id[task.record_id].source_units}
    aliases = {value["entity_ref"]["id"] for value in resolutions.values()
               if value["scope"] == task.scope.model_dump(mode="json")
               and any(ref["evidence_id"] in units for ref in value["source_refs"])}
    parents = {identity: identity for identity in selected}
    aliases_to_identity = {}

    def component(identity):
        while parents[identity] != identity:
            parents[identity] = parents[parents[identity]]
            identity = parents[identity]
        return identity

    required_ids = set()
    for identity in selected:
        node = nodes[identity]
        for label in node.label.split(" / "):
            name = normalized_reference_name(label)
            if name:
                previous = aliases_to_identity.setdefault(name, identity)
                parents[component(identity)] = component(previous)
        if identity in aliases or any(ref.evidence_id in units for ref in node.evidence_refs):
            required_ids.add(identity)
    groups = {}
    for identity in selected:
        groups.setdefault(component(identity), []).append(identity)
    mandatory = []
    # Shared aliases form transitive ambiguity groups, just as recall matches
    # each physical name. Never hide an alternative behind another page.
    for key in list(groups):
        if required_ids.intersection(groups[key]):
            mandatory.extend(groups.pop(key))
    # A complete local object group may exceed the nominal page size. It is
    # attempted intact under the existing request token gate, never shortened.
    pages, current = [], list(mandatory)
    for group in groups.values():
        if len(current) > len(mandatory) and len(current) + len(group) > page_size:
            pages.append(current)
            current = list(mandatory)
        current.extend(group)
    if current or not pages:
        pages.append(current)
    return pages


def resolve_claim_schema(card, payload, local_refs, entities, entity_dependencies):
    """Narrow a record card to this exact claim owner; legacy cards remain unchanged."""
    if not isinstance(card, RecordDiscoverySchemaCard):
        return card
    if hasattr(payload, "class_iri"):
        identity, class_iri = payload.local_id, payload.class_iri
    else:
        identity = (payload.source_id if hasattr(payload, "source_id") else payload.subject_id)
        entity = next((e for e in entities if e.local_id == identity), None)
        if entity is None:
            entity = next((e for e in entity_dependencies
                           if e.entity_ref == local_refs.get(identity)), None)
        if entity is None:
            raise ValueError("entity_reference_missing")
        class_iri = entity.class_iri
    return card.for_class(class_iri).for_subject(local_refs[identity], parent=card)
