"""Root-schema routing over frozen document metadata; never a fact authority."""

from __future__ import annotations

import re
from collections import deque
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.evidence import EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.contracts import (
    MetadataNode,
    MetadataSnapshot,
    OntologySnapshot,
)
from app.services.extraction.ontology_guided.ontology_plan import compile_class_predicates

SCHEMA_REGION_ROUTING_VERSION = "schema-region-routing-v1"


class SchemaRegionRoutingPolicy(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["schema-region-routing-v1"] = SCHEMA_REGION_ROUTING_VERSION
    max_regions_per_card: int = Field(default=2, ge=1, le=16)
    minimum_similarity: float = Field(default=0.25, ge=-1, le=1, allow_inf_nan=False)
    # A summary-only parent can cover most or all of a document.  Such a node
    # is useful for navigation, but admitting every descendant defeats region
    # routing and turns the run back into a full-document scan.
    max_region_records: int = Field(default=32, ge=1, le=256)
    # Legacy v1 artifacts omitted these fields and therefore retain the old
    # contextual limits. Newly frozen runs choose smaller values explicitly.
    max_group_chars: int = Field(default=1200, ge=1, le=1200)
    max_group_records: int = Field(default=8, ge=1, le=8)
    # Frozen v1 runs omitted these switches and keep their original per-card /
    # separate-field execution. New runs opt into one bounded region request.
    execution_mode: Literal["per_card", "region_batch"] = "per_card"
    property_field_mode: Literal["separate", "region_batch"] = "separate"


class RoutingProperty(EvidenceModel):
    iri: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""


class RootMetadataSchemaCard(EvidenceModel):
    """Document-root properties owned by the artifact/cover metadata path."""

    card_id: str = Field(min_length=1)
    root_class_iri: str = Field(min_length=1)
    properties: list[RoutingProperty] = Field(default_factory=list)


class RoutingSchemaCard(EvidenceModel):
    """Compact retrieval projection for one formal outgoing root relationship."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    routing_card_id: str = Field(min_length=1)
    ontology_snapshot_id: str = Field(min_length=1)
    analysis_scope_ref: str = Field(min_length=1)
    root_class_iri: str = Field(min_length=1)
    predicate_iri: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    direction: Literal["outgoing"] = "outgoing"
    range_class_iris: list[str] = Field(min_length=1)
    range_type_labels: list[str] = Field(min_length=1)
    range_type_descriptions: list[str] = Field(default_factory=list)
    class_iris: list[str] = Field(min_length=1)
    class_labels: list[str] = Field(min_length=1)
    routing_terms: list[str] = Field(default_factory=list)
    restricted_terms: list[str] = Field(default_factory=list)
    execution_card_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def canonical_scope(self):
        for name in (
            "range_class_iris",
            "range_type_labels",
            "range_type_descriptions",
            "class_iris",
            "class_labels",
            "routing_terms",
            "restricted_terms",
            "execution_card_ids",
        ):
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"routing_card_{name}_not_canonical")
        if not set(self.range_class_iris) <= set(self.class_iris):
            raise ValueError("routing_card_range_outside_scope")
        return self


class SchemaRegionRoutingPlan(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["schema-region-routing-v1"] = SCHEMA_REGION_ROUTING_VERSION
    analysis_scope_ref: str = Field(min_length=1)
    root_property_card: RootMetadataSchemaCard
    routing_cards: list[RoutingSchemaCard]

    @model_validator(mode="after")
    def unique_relationships(self):
        predicates = [card.predicate_iri for card in self.routing_cards]
        if len(predicates) != len(set(predicates)):
            raise ValueError("duplicate_root_routing_relationship")
        return self


def _terms(ontology: OntologySnapshot, iris) -> list[str]:
    if ontology.lexical_context is None:
        return []
    return sorted({
        term.text
        for iri in iris
        for term in ontology.lexical_context.annotations.get(iri, [])
        if term.text.strip()
    })


def compile_schema_region_routing(
    ontology: OntologySnapshot,
    root_class_iri: str,
    *,
    analysis_scope_ref: str,
    max_hops: int,
    focus_paths=(),
) -> SchemaRegionRoutingPlan:
    """Compile independent root-relation branches from the frozen T-Box.

    The result is retrieval input only. It neither creates an instance edge nor
    extends any formal range.
    """
    if max_hops < 1:
        raise ValueError("schema_region_routing_requires_relationship_hop")
    paths = tuple(tuple(path) for path in focus_paths)
    properties, relationships, _ = compile_class_predicates(
        ontology, root_class_iri,
    )
    root_properties = [
        RoutingProperty(iri=item.iri, label=item.label, description=item.description)
        for item in properties
    ]
    property_payload = {
        "root_class_iri": root_class_iri,
        "properties": [item.model_dump(mode="json") for item in root_properties],
    }
    property_card = RootMetadataSchemaCard(
        card_id=evidence_hash([SCHEMA_REGION_ROUTING_VERSION, property_payload]),
        **property_payload,
    )
    cards = []
    for relationship in relationships:
        if relationship.constraint_status != "resolved":
            continue
        if paths and not any(path and path[0] == relationship.iri for path in paths):
            continue
        class_iris = set(relationship.range_class_iris)
        pending = deque((iri, 1, (relationship.iri,)) for iri in relationship.range_class_iris)
        visited = set()
        while pending:
            class_iri, depth, prefix = pending.popleft()
            key = (class_iri, depth, prefix if paths else ())
            if key in visited:
                continue
            visited.add(key)
            if class_iri not in ontology.classes or depth >= max_hops:
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
                    if target in ontology.classes:
                        class_iris.add(target)
                        pending.append((target, depth + 1, next_prefix))
        ranges = sorted(set(relationship.range_class_iris) & set(ontology.classes))
        scope = sorted(class_iris & set(ontology.classes))
        if not ranges or not scope:
            continue
        range_labels = sorted({ontology.classes[iri].label for iri in ranges})
        range_descriptions = sorted({
            ontology.classes[iri].description
            for iri in ranges
            if ontology.classes[iri].description.strip()
        })
        class_labels = sorted({ontology.classes[iri].label for iri in scope})
        payload = {
            "ontology_snapshot_id": ontology.snapshot_id,
            "analysis_scope_ref": analysis_scope_ref,
            "root_class_iri": root_class_iri,
            "predicate_iri": relationship.iri,
            "label": relationship.label,
            "description": relationship.description,
            "range_class_iris": ranges,
            "range_type_labels": range_labels,
            "range_type_descriptions": range_descriptions,
            "class_iris": scope,
            "class_labels": class_labels,
            "routing_terms": _terms(ontology, [relationship.iri, *ranges]),
            "restricted_terms": _terms(
                ontology, [relationship.iri, *ranges, *scope],
            ),
        }
        cards.append(RoutingSchemaCard(
            routing_card_id=evidence_hash([SCHEMA_REGION_ROUTING_VERSION, payload]),
            **payload,
        ))
    cards.sort(key=lambda item: (item.predicate_iri, item.routing_card_id))
    return SchemaRegionRoutingPlan(
        analysis_scope_ref=analysis_scope_ref,
        root_property_card=property_card,
        routing_cards=cards,
    )


def routing_card_text(card: RoutingSchemaCard) -> str:
    """Return the compact semantic view; opaque identities stay out of embeddings."""
    values = [
        card.label,
        card.description,
        *card.range_type_labels,
        *card.range_type_descriptions,
        *card.routing_terms,
    ]
    return "\n".join(dict.fromkeys(value.strip() for value in values if value.strip()))


def metadata_node_text(node: MetadataNode) -> str:
    values = [*node.path, node.heading, node.summary or ""]
    return "\n".join(dict.fromkeys(value.strip() for value in values if value.strip()))


_ROUTING_TEXT_NORMALIZER = re.compile(r"[^0-9a-z\u4e00-\u9fff]+", re.IGNORECASE)


def routing_heading_match(card: RoutingSchemaCard, node: MetadataNode) -> int:
    """Prefer explicit ontology terms in headings before semantic similarity."""
    headings = {
        _ROUTING_TEXT_NORMALIZER.sub("", value).casefold()
        for value in [node.heading, *node.path]
        if value.strip()
    }
    relation_terms = {
        _ROUTING_TEXT_NORMALIZER.sub("", value).casefold()
        for value in [card.label]
        if value.strip()
    }
    range_terms = {
        _ROUTING_TEXT_NORMALIZER.sub("", value).casefold()
        # Descendant labels enrich the embedding view, but they are often
        # shared by unrelated root branches (for example 存放条件). Only the
        # direct range may use a bounded substring match.
        for value in card.range_type_labels
        if value.strip()
    }
    if any(
        heading == term
        for heading in headings
        for term in relation_terms | range_terms
    ):
        return 2
    if any(
        min(len(heading), len(term)) >= 2 and (heading in term or term in heading)
        for heading in headings
        for term in range_terms
    ):
        return 1
    # A qualified Chinese type often appears in a heading without its
    # ontology qualifier (for example ``药物产品`` -> ``产品的基本性质``).
    # Derive the head uniformly for every direct range and accept only bounded
    # title forms. This does not turn an arbitrary longer title ending in the
    # same two characters into a match.
    heading = _ROUTING_TEXT_NORMALIZER.sub("", node.heading).casefold()
    aliases = {
        term[-2:]
        for term in range_terms
        if len(term) > 2 and re.fullmatch(r"[\u4e00-\u9fff]+", term)
    }
    if any(
        heading in {alias, f"{alias}信息", f"{alias}基本信息"}
        or heading.startswith(f"{alias}的")
        for alias in aliases
    ):
        return 1
    return 0


def resolve_metadata_region(
    node_id: str,
    metadata: MetadataSnapshot,
    index,
) -> tuple[tuple[str, ...], Literal["direct", "descendant", "adjacent"]] | None:
    """Resolve a structure hit to records without treating metadata as evidence."""
    nodes = {node.node_id: node for node in metadata.node_summaries}
    node = nodes.get(node_id)
    if node is None:
        raise ValueError("metadata_routing_node_missing")
    valid = set(index.by_id)

    def direct(item: MetadataNode) -> tuple[str, ...]:
        return tuple(identity for identity in item.source_record_refs if identity in valid)

    records = direct(node)
    if records:
        return records, "direct"

    children = {}
    for identity, raw in index.nodes_by_id.items():
        children.setdefault(raw.get("parent_id"), []).append(identity)

    def descendants(identity: str) -> tuple[str, ...]:
        result = []
        pending = deque(children.get(identity, ()))
        while pending:
            child = pending.popleft()
            item = nodes.get(child)
            if item is not None:
                result.extend(direct(item))
            pending.extend(children.get(child, ()))
        return tuple(dict.fromkeys(result))

    records = descendants(node_id)
    if records:
        return records, "descendant"

    parent_id = index.nodes_by_id.get(node_id, {}).get("parent_id")
    ordered = [item.node_id for item in metadata.node_summaries]
    position = ordered.index(node_id)
    candidates = []
    for candidate_position, candidate_id in enumerate(ordered):
        if candidate_id == node_id or (
            index.nodes_by_id.get(candidate_id, {}).get("parent_id") != parent_id
        ):
            continue
        candidate = nodes[candidate_id]
        candidate_records = direct(candidate) or descendants(candidate_id)
        if candidate_records:
            candidates.append((
                abs(candidate_position - position),
                candidate_position < position,
                candidate_position,
                candidate_records,
            ))
    if not candidates:
        return None
    _, _, _, records = min(candidates)
    return records, "adjacent"


def metadata_region_is_bounded(
    node: MetadataNode,
    resolved: tuple[tuple[str, ...], Literal["direct", "descendant", "adjacent"]],
    *,
    max_records: int,
) -> bool:
    """Reject navigation summaries that would authorize a broad document scan."""
    records, resolution = resolved
    if len(records) > max_records:
        return False
    # The first heading commonly summarizes the whole document.  It has no
    # direct evidence records and resolves only by swallowing every child.
    return not (resolution == "descendant" and len(node.path) <= 1)
