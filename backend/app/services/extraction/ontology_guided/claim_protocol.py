"""Ontology tool extraction contracts and pure frozen-claim identity checks.

Model proposals never contain trusted normalization, acceptance, or permissions.
Schemas are generated from these types; the application does not load specs/.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_serializer, model_validator

from app.schemas.evidence import EvidenceAnchor, EvidenceModel
from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    LocalMenu,
    SemanticDecision,
    SlotSpec,
    TraversalScope,
    VersionedRef,
)

FacetName = Literal[
    "type", "referent", "subject_role", "subject_binding", "object_binding",
    "field_role", "predicate", "bridge", "selection", "qualifiers", "counterevidence",
    "value", "unit", "identity_owner", "identity_key", "identity_scope",
    "reference_identity", "reference_scope",
]
Modality = Literal["asserted", "required", "possible", "planned", "unspecified"]
Selection = Literal["all", "one_of", "alternatives", "undetermined"]
AllowedBridge = Literal[
    "explicit_assertion", "owned_field_group", "role_mapped_table",
    "resolved_reference_chain", "document_subject_description",
]
TargetKind = Literal["entity", "reference_binding", "property", "relation", "external_link"]


class Quote(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    evidence_id: str
    text: str
    context_text: str | None


class ScopeQualifier(EvidenceModel):
    predicate_iri: str | None
    quote: Quote


class Qualifiers(EvidenceModel):
    polarity: Literal["affirmed", "negated"]
    modality: Modality
    condition_support: list[Quote]
    scope_qualifiers: list[ScopeQualifier]


class RecordComponent(EvidenceModel):
    role: Literal["subject", "field", "value", "context"]
    quote: Quote


class IdentifierProposal(EvidenceModel):
    predicate_iri: str
    value_quote: Quote


class EntityProposal(EvidenceModel):
    local_id: str
    class_iri: str
    representation: Literal["mention", "record"]
    mentions: list[Quote]
    record_components: list[RecordComponent]
    identifier_claims: list[IdentifierProposal]

    @model_validator(mode="after")
    def source_required(self):
        if not self.local_id or not self.class_iri:
            raise ValueError("entity_identity_required")
        if self.representation == "mention" and not self.mentions:
            raise ValueError("entity_mention_required")
        if self.representation == "record" and not self.record_components:
            raise ValueError("record_composition_required")
        return self


class PropertyProposal(EvidenceModel):
    local_id: str
    subject_id: str
    predicate_iri: str
    value_quote: Quote
    field_support: list[Quote]
    unit_support: list[Quote]
    qualifiers: Qualifiers
    bridge_kind: AllowedBridge
    bridge_ref_ids: list[str]


class ReferenceBindingProposal(EvidenceModel):
    local_id: str
    source_id: str
    target_id: str
    binding_kind: Literal["anaphora", "coreference"]
    support: list[Quote] = Field(min_length=1)


class ObjectSourceSupport(EvidenceModel):
    object_id: str
    support: list[Quote] = Field(min_length=1)


class SourceAssertion(EvidenceModel):
    subject_support: list[Quote]
    object_support: list[ObjectSourceSupport]
    predicate_support: list[Quote] = Field(min_length=1)
    binding_ids: list[str]


class RelationProposal(EvidenceModel):
    local_id: str
    subject_id: str
    predicate_iri: str
    object_ids: list[str]
    selection: Selection
    bridge_support: list[Quote]
    selection_support: list[Quote]
    qualifiers: Qualifiers
    bridge_kind: AllowedBridge
    bridge_ref_ids: list[str]
    source_assertion: SourceAssertion | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "source_assertion" not in self.model_fields_set:
            data.pop("source_assertion", None)
        return data

    @model_validator(mode="after")
    def unique_objects(self):
        if not self.object_ids or len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("relation_objects_must_be_nonempty_and_unique")
        if len(self.object_ids) == 1 and self.selection != "all":
            raise ValueError("single_object_requires_all")
        return self


class ExternalLinkProposal(EvidenceModel):
    local_id: str
    subject_id: str
    external_candidate_id: str
    identity_support: list[Quote]


class ObservationProposal(EvidenceModel):
    subject_id: str | None
    predicate_iri: str | None
    quote: Quote
    kind: Literal["missing", "unknown", "ambiguous", "unbound"]
    reason: str


class DiscoveryEnvelope(EvidenceModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    entities: list[EntityProposal]
    properties: list[PropertyProposal]
    relations: list[RelationProposal]
    external_links: list[ExternalLinkProposal]
    observations: list[ObservationProposal]
    reference_bindings: list[ReferenceBindingProposal] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        if "reference_bindings" not in self.model_fields_set:
            data.pop("reference_bindings", None)
        return data

    @model_validator(mode="after")
    def unique_local_ids(self):
        ids = [p.local_id for _, p in iter_proposals(self)]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("duplicate_or_empty_local_id")
        # Registered context entities are resolved by the freeze/input boundary.
        nonentities = {p.local_id for kind, p in iter_proposals(self) if kind != "entity"}
        for _, proposal in iter_proposals(self):
            if any(endpoint in nonentities for endpoint in endpoint_ids(proposal)):
                raise ValueError("endpoint_is_not_an_entity")
        return self


def iter_proposals(proposal: DiscoveryEnvelope):
    for kind, collection in (
        ("entity", proposal.entities), ("reference_binding", proposal.reference_bindings),
        ("property", proposal.properties),
        ("relation", proposal.relations), ("external_link", proposal.external_links),
    ):
        for item in collection:
            yield kind, item


def endpoint_ids(proposal) -> list[str]:
    if isinstance(proposal, ReferenceBindingProposal):
        return [proposal.source_id, proposal.target_id]
    if isinstance(proposal, RelationProposal):
        return [proposal.subject_id, *proposal.object_ids]
    if isinstance(proposal, (PropertyProposal, ExternalLinkProposal)):
        return [proposal.subject_id]
    return []


class FacetVerification(EvidenceModel):
    name: FacetName
    verdict: Literal["supported", "unsupported", "undetermined"]
    support: list[Quote]
    counterevidence_support: list[Quote]
    reason: str


class TargetVerification(EvidenceModel):
    target_id: str
    content_hash: str
    facets: list[FacetVerification]

    @model_validator(mode="after")
    def unique_facets(self):
        if len({f.name for f in self.facets}) != len(self.facets):
            raise ValueError("duplicate_verification_facet")
        return self


class VerificationEnvelope(EvidenceModel):
    verifications: list[TargetVerification]

    @model_validator(mode="after")
    def unique_targets(self):
        if len({v.target_id for v in self.verifications}) != len(self.verifications):
            raise ValueError("duplicate_verification_target")
        return self


class VerifiedTarget(EvidenceModel):
    """Source-checked decisions; this contains no editable copy of a proposal."""
    target_id: str
    content_hash: str
    decisions: list[SemanticDecision]
    missing_facets: list[FacetName]
    validation_issues: list[str]


class VerifiedClaimSet(EvidenceModel):
    context_hash: str
    targets: list[VerifiedTarget]


def _anchor_covers(outer: EvidenceAnchor, inner: EvidenceAnchor) -> bool:
    identity_fields = ("document_hash", "parser_version", "structure_hash", "evidence_id",
                       "section_node_id", "block_id")
    return (
        all(getattr(outer, key) == getattr(inner, key) for key in identity_fields)
        and outer.span_start is not None and inner.span_start is not None
        and outer.span_start <= inner.span_start and outer.span_end >= inner.span_end
    )


def validate_verification_targets(
    response: VerificationEnvelope, *, targets: list[VerificationTargetSpec],
) -> dict[str, TargetVerification]:
    """Check the frozen response structure without interpreting any verdict or proof."""
    expected = {target.target_id: target for target in targets}
    received = {answer.target_id: answer for answer in response.verifications}
    if len(expected) != len(targets):
        raise ValueError("verification_target_set_mismatch")

    def reject(loc, code):
        raise ValidationError.from_exception_data("VerificationEnvelope", [{
            "type": "value_error", "loc": loc, "ctx": {"error": ValueError(code)},
        }])

    if set(expected) != set(received):
        reject(("verifications",), "verification_target_set_mismatch")
    positions = {answer.target_id: i for i, answer in enumerate(response.verifications)}
    for target in targets:
        # Revalidate after loading a result; model_copy(update=...) is not a trust boundary.
        VerificationTargetSpec.model_validate(target.model_dump(mode="json"), strict=True)
        answer = received[target.target_id]
        location = ("verifications", positions[target.target_id])
        if target.content_hash != answer.content_hash:
            reject((*location, "content_hash"), "verification_content_hash_mismatch")
        if set(target.required_facets) != {facet.name for facet in answer.facets}:
            reject((*location, "facets"), "verification_facet_set_mismatch")
    return received


def validate_verification(
    response: VerificationEnvelope, *, targets: list[VerificationTargetSpec], context,
) -> VerifiedClaimSet:
    """Reject protocol changes, then check each target's original-source decisions.

    A malformed answer never becomes an empty success. Bad proof for one target
    remains an explicit gap on that target, leaving independent claims usable.
    """
    from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

    received = validate_verification_targets(response, targets=targets)
    verified = []
    for target in targets:
        answer = received[target.target_id]
        by_facet = {facet.name: facet for facet in answer.facets}
        decisions, issues, missing = [], [], []
        for name in target.required_facets:
            facet = by_facet[name]
            support, counter = [], []
            source_issues = []
            for quotes, destination, fact_required in (
                # Supplemental original text may verify an existing frozen
                # claim. Only freezing grants permission to propose new facts.
                (facet.support, support, False),
                (facet.counterevidence_support, counter, False),
            ):
                for quote in quotes:
                    try:
                        anchor, _ = resolve_fragment_quote(
                            quote.evidence_id, quote.text, context.fragments,
                            context_text=quote.context_text, fact_required=fact_required,
                        )
                        destination.append(anchor)
                    except ValueError as exc:
                        source_issues.append(f"{name}:{exc}")
            if facet.verdict == "supported" and not support:
                source_issues.append(f"{name}:support_missing")
            if name == "counterevidence" and any(
                not any(_anchor_covers(ref, expected_ref) for ref in [*support, *counter])
                for expected_ref in context.counterevidence_refs
            ):
                source_issues.append("counterevidence_not_reviewed")
            verdict = "undetermined" if source_issues else facet.verdict
            if verdict != "supported":
                missing.append(name)
            issues.extend(source_issues)
            identity = [target.target_id, target.content_hash, context.target.context_hash,
                        name, verdict, support, counter]
            decisions.append(SemanticDecision(
                decision_id=stable_id("claim-facet", identity), target_id=target.target_id,
                check_kind=name, verdict=verdict,
                reason_code=source_issues[0] if source_issues else f"facet_{verdict}",
                reason=facet.reason or "模型未提供理由", reason_status=(
                    "provided" if facet.reason else "missing"
                ), support_refs=support, counterevidence_refs=counter,
                searched_context_refs=[fragment.anchor for fragment in context.fragments],
                verifier_version="ontology-tool-extraction-v1",
                attempt_id=stable_id("claim-verification", identity[:3]),
            ))
        verified.append(VerifiedTarget(
            target_id=target.target_id, content_hash=target.content_hash,
            decisions=decisions, missing_facets=missing,
            validation_issues=list(dict.fromkeys(issues)),
        ))
    return VerifiedClaimSet(context_hash=context.target.context_hash, targets=verified)


class ConstraintIssue(EvidenceModel):
    model_config = ConfigDict(serialize_by_alias=True)
    predicate_iri: str
    constraint_construct: str = Field(alias="construct")
    reason_code: str


class QuantityPolicy(EvidenceModel):
    predicate_iri: str
    allowed_forms: list[Literal["scalar", "interval", "lower_bound", "upper_bound"]]
    endpoint_role: Literal["lower", "upper"] | None
    allowed_target_units: list[str]
    unit_requirement: Literal["physical", "dimensionless", "count", "not_declared"]
    declaration_ref: str


class IdentityKeySpec(EvidenceModel):
    class_iri: str
    property_iris: list[str]
    namespace: str | None
    scope: Literal["document", "dataset", "global"]
    declaration_ref: str


class ExtractionProfile(EvidenceModel):
    quantity_policies: list[QuantityPolicy] = Field(default_factory=list)
    identity_keys: list[IdentityKeySpec] = Field(default_factory=list)


class SchemaCard(EvidenceModel):
    schema_card_id: str
    subject_ref: VersionedRef
    class_iris: list[str]
    ontology_snapshot_id: str
    menu_id: str
    predicates: list[SlotSpec | EdgeSpec]
    quantity_policies: list[QuantityPolicy]
    identity_keys: list[IdentityKeySpec]
    unsupported_constraints: list[ConstraintIssue]


def compile_schema_card(
    menu: LocalMenu, *, predicate_iri: str | None,
    profile: ExtractionProfile, scope: TraversalScope,
) -> SchemaCard:
    """Narrow a frozen one-hop menu without reinterpreting ontology constraints."""
    predicates = [
        predicate.model_copy(deep=True)
        for predicate in [*menu.properties, *menu.relationships]
        if predicate_iri is None or predicate.iri == predicate_iri
    ]
    if predicate_iri is not None and not predicates:
        raise ValueError("predicate_outside_menu")
    predicate_ids = {predicate.iri for predicate in predicates}
    classes = {menu.subject.class_iri}
    for predicate in predicates:
        if isinstance(predicate, EdgeSpec):
            classes.update(predicate.range_class_iris)
    quantity_policies = [
        policy.model_copy(deep=True) for policy in profile.quantity_policies
        if policy.predicate_iri in predicate_ids
    ]
    if len({p.predicate_iri for p in quantity_policies}) != len(quantity_policies):
        raise ValueError("conflicting_quantity_policies")
    declared = {policy.predicate_iri for policy in quantity_policies}
    numeric_types = {
        "http://www.w3.org/2001/XMLSchema#" + name for name in (
            "decimal", "double", "float", "integer", "int", "nonNegativeInteger",
            "positiveInteger", "long", "short", "byte", "nonPositiveInteger", "negativeInteger",
        )
    }
    for predicate in predicates:
        if (
            isinstance(predicate, SlotSpec) and predicate.iri not in declared
            and predicate.constraint_status == "resolved" and len(predicate.datatype_iris) == 1
            and predicate.datatype_iris[0] in numeric_types
        ):
            quantity_policies.append(QuantityPolicy(
                predicate_iri=predicate.iri, allowed_forms=["scalar"], endpoint_role=None,
                allowed_target_units=[predicate.canonical_unit] if predicate.canonical_unit else [],
                unit_requirement="physical" if predicate.canonical_unit else "not_declared",
                declaration_ref=f"{menu.ontology_snapshot_id}:{predicate.iri}",
            ))
    payload = {
        "subject_ref": VersionedRef(id=menu.subject.entity_id, revision=menu.subject.revision),
        "class_iris": sorted(classes), "ontology_snapshot_id": menu.ontology_snapshot_id,
        "menu_id": menu.menu_id, "predicates": predicates, "quantity_policies": quantity_policies,
        "identity_keys": [key for key in profile.identity_keys if key.class_iri in classes],
        "unsupported_constraints": [
            ConstraintIssue(predicate_iri=p.iri, construct="predicate_constraint",
                            reason_code="constraint_unresolved")
            for p in predicates if p.constraint_status != "resolved"
        ],
    }
    return SchemaCard(schema_card_id=evidence_hash({"card": payload, "scope": scope}), **payload)


def compile_stage_schema(
    stage: Literal["discovery", "verification"], *, card: SchemaCard,
    evidence_ids: list[str], targets: list[VerificationTargetSpec],
    reference_resolution: bool = False,
    relation_bridges: list[AllowedBridge] | None = None,
) -> dict:
    """Generate then narrow model schemas; local semantic validation remains mandatory."""
    if stage not in {"discovery", "verification"}:
        raise ValueError("invalid_model_stage")
    model = DiscoveryEnvelope if stage == "discovery" else VerificationEnvelope
    schema = model.model_json_schema()
    definitions = schema["$defs"]
    if stage == "discovery":
        if reference_resolution:
            schema["required"].append("reference_bindings")
            definitions["RelationProposal"]["required"].append("source_assertion")
            definitions["RelationProposal"]["properties"]["source_assertion"] = {
                "$ref": "#/$defs/SourceAssertion",
            }
        else:
            schema["properties"].pop("reference_bindings", None)
            definitions["RelationProposal"]["properties"].pop("source_assertion", None)
            for name in ("ReferenceBindingProposal", "SourceAssertion", "ObjectSourceSupport"):
                definitions.pop(name, None)
    definitions["Quote"]["properties"]["evidence_id"]["enum"] = sorted(set(evidence_ids))
    if stage == "verification":
        definitions["TargetVerification"]["properties"]["target_id"]["enum"] = [
            target.target_id for target in targets
        ]
        definitions["TargetVerification"]["properties"]["content_hash"]["enum"] = [
            target.content_hash for target in targets
        ]
    else:
        definitions["EntityProposal"]["properties"]["class_iri"]["enum"] = sorted(
            allowed_entity_classes(card)
        )
        for name, kind in (("PropertyProposal", SlotSpec), ("RelationProposal", EdgeSpec)):
            definitions[name]["properties"]["predicate_iri"]["enum"] = [
                predicate.iri for predicate in card.predicates if isinstance(predicate, kind)
            ]
        definitions["IdentifierProposal"]["properties"]["predicate_iri"]["enum"] = sorted({
            iri for key in card.identity_keys for iri in key.property_iris
        })
        if relation_bridges is not None:
            definitions["RelationProposal"]["properties"]["bridge_kind"]["enum"] = relation_bridges
            definitions["RelationProposal"]["properties"]["subject_id"]["enum"] = [
                card.subject_ref.id,
            ]
    return schema


def allowed_entity_classes(card: SchemaCard) -> set[str]:
    return set(card.class_iris).union(*(
        set(predicate.range_class_iris)
        for predicate in card.predicates if isinstance(predicate, EdgeSpec)
    ))


class QuantityValue(EvidenceModel):
    form: Literal["scalar", "interval", "lower_bound", "upper_bound"]
    raw: str
    source_unit: str | None
    target_unit: str | None
    scalar: str | None
    lower: str | None
    upper: str | None
    lower_inclusive: bool | None
    upper_inclusive: bool | None
    comparator: str | None
    endpoint_role: Literal["lower", "upper"] | None
    datatype_iri: str
    dimension: str | None
    conversion_record: dict[str, str]

    @model_validator(mode="after")
    def coherent_quantity(self):
        numbers = {}
        for name in ("scalar", "lower", "upper"):
            value = getattr(self, name)
            if value is not None:
                try:
                    numbers[name] = Decimal(value)
                except InvalidOperation as exc:
                    raise ValueError("quantity_decimal_invalid") from exc
                if not numbers[name].is_finite():
                    raise ValueError("quantity_decimal_invalid")
        expected = {"scalar": {"scalar"}, "interval": {"lower", "upper"},
                    "lower_bound": {"lower"}, "upper_bound": {"upper"}}[self.form]
        if set(numbers) != expected:
            raise ValueError("quantity_form_value_mismatch")
        if self.form == "scalar":
            if any(value is not None for value in (
                self.lower_inclusive, self.upper_inclusive, self.comparator, self.endpoint_role,
            )):
                raise ValueError("scalar_has_range_fields")
        elif self.form == "interval":
            if (self.lower_inclusive is None or self.upper_inclusive is None
                    or self.comparator is not None or numbers["lower"] > numbers["upper"]
                    or (numbers["lower"] == numbers["upper"]
                        and not (self.lower_inclusive and self.upper_inclusive))):
                raise ValueError("quantity_interval_invalid")
        else:
            lower = self.form == "lower_bound"
            included = self.lower_inclusive if lower else self.upper_inclusive
            unused = self.upper_inclusive if lower else self.lower_inclusive
            comparison = ("ge" if included else "gt") if lower else ("le" if included else "lt")
            if (included is None or unused is not None or self.endpoint_role is not None
                    or self.comparator != comparison):
                raise ValueError("quantity_bound_invalid")
        return self


class ClaimCheckResult(EvidenceModel):
    """Controller results; absent/blocked mandatory checks never mean success."""
    checks: dict[Literal["binding", "metric", "shacl", "relation_graph"], bool | None] = Field(
        default_factory=dict,
    )
    issues: list[str] = Field(default_factory=list)
    quantity: QuantityValue | None = None
    normalized_literal: str | bool | None = None


class ExternalMatch(EvidenceModel):
    predicate_iri: str | None
    document_quote: Quote
    record_field: str
    record_value: str
    match_kind: Literal["exact_key", "key_component", "name", "alias"]


class MappedExternalField(EvidenceModel):
    predicate_iri: str
    raw_value: str
    datatype_iri: str | None


class ExternalMetadata(EvidenceModel):
    name: str
    value: str


class ExternalCandidate(EvidenceModel):
    candidate_id: str
    source_id: str
    system: str
    dataset: str
    record_key: str
    record_version: str
    class_iri: str
    matches: list[ExternalMatch]
    mapped_fields: list[MappedExternalField]
    metadata: list[ExternalMetadata]


def semantic_content(kind: TargetKind, payload: EvidenceModel) -> dict:
    data = payload.model_dump(mode="json")
    if kind in {"entity", "reference_binding"}:
        return data
    if kind == "external_link":
        return {key: data[key] for key in ("local_id", "subject_id", "external_candidate_id")}
    result = {key: data[key] for key in (
        "local_id", "subject_id", "predicate_iri", "bridge_kind", "bridge_ref_ids",
    )}
    qualifiers = data["qualifiers"]
    result["qualifiers"] = {
        "polarity": qualifiers["polarity"], "modality": qualifiers["modality"],
        "condition_texts": [q["text"] for q in qualifiers["condition_support"]],
        "scope_qualifiers": [
            {"predicate_iri": q["predicate_iri"], "text": q["quote"]["text"]}
            for q in qualifiers["scope_qualifiers"]
        ],
    }
    if kind == "property":
        result["raw_value"] = data["value_quote"]["text"]
        result["source_unit_texts"] = sorted({q["text"] for q in data["unit_support"]})
    else:
        result.update(object_ids=data["object_ids"], selection=data["selection"])
        if "source_assertion" in data:
            result["source_assertion"] = data["source_assertion"]
    return result


def claim_content_hash(kind, payload, scope, dependency_refs) -> str:
    return evidence_hash({
        "target_kind": kind, "semantic_content": semantic_content(kind, payload),
        "scope": scope, "dependency_refs": dependency_refs,
    })


class VerificationTargetSpec(EvidenceModel):
    target_id: str
    target_kind: TargetKind
    claim_ref: VersionedRef
    content_hash: str
    required_facets: list[FacetName]
    payload: (EntityProposal | ReferenceBindingProposal | PropertyProposal
              | RelationProposal | ExternalLinkProposal)
    scope: TraversalScope
    dependency_refs: list[VersionedRef]

    @model_validator(mode="after")
    def matches_frozen_content(self):
        expected = {
            "entity": EntityProposal, "property": PropertyProposal,
            "relation": RelationProposal, "external_link": ExternalLinkProposal,
            "reference_binding": ReferenceBindingProposal,
        }[self.target_kind]
        if not isinstance(self.payload, expected):
            raise ValueError("verification_payload_kind_mismatch")
        if self.content_hash != claim_content_hash(
            self.target_kind, self.payload, self.scope, self.dependency_refs,
        ):
            raise ValueError("verification_claim_hash_mismatch")
        if not self.required_facets or len(set(self.required_facets)) != len(self.required_facets):
            raise ValueError("invalid_required_facets")
        return self


def ref_key(ref: VersionedRef):
    return ref.id, ref.revision


class EntityDependencyView(EvidenceModel):
    entity_ref: VersionedRef
    class_iri: str
    grounding_kind: Literal["document_root", "mention", "record"]
    root_origin: str | None
    proposal: EntityProposal | None
    source_refs: list[EvidenceAnchor]
    dependency_refs: list[VersionedRef]
    content_hash: str

    @model_validator(mode="after")
    def validates_identity(self):
        if self.grounding_kind == "document_root":
            if self.proposal is not None or not self.root_origin:
                raise ValueError("document_root_requires_configured_origin")
        elif (self.proposal is None or self.proposal.class_iri != self.class_iri
              or not self.source_refs):
            raise ValueError("entity_dependency_content_required")
        if self.content_hash != evidence_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("entity_dependency_hash_mismatch")
        return self


class BridgeStepView(EvidenceModel):
    kind: Literal["referent_binding", "owner_binding", "predicate_assertion"]
    subject_ref: VersionedRef
    object_ref: VersionedRef | None
    predicate_iri: str | None
    source_refs: list[EvidenceAnchor]


class BridgeDependencyView(EvidenceModel):
    bridge_id: str
    bridge_ref: VersionedRef
    bridge_kind: str
    steps: list[BridgeStepView]
    dependency_refs: list[VersionedRef]
    content_hash: str

    @model_validator(mode="after")
    def validates_bridge(self):
        if not any(step.kind == "predicate_assertion" for step in self.steps):
            raise ValueError("bridge_predicate_assertion_missing")
        if self.content_hash != evidence_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("bridge_dependency_hash_mismatch")
        return self


class GroundedScopeQualifier(EvidenceModel):
    predicate_iri: str | None
    text: str
    evidence_refs: list[EvidenceAnchor]


class VerificationScopeStep(EvidenceModel):
    relation_ref: VersionedRef
    member_ref: VersionedRef
    selection: Selection | None
    polarity: Literal["affirmed", "negated"]
    modality: Modality
    conditions: list[str]
    applicability: list[GroundedScopeQualifier]
    evidence_refs: list[EvidenceAnchor]


class VerificationScopeResolution(EvidenceModel):
    scope_id: str
    steps: list[VerificationScopeStep]


class VerificationInput(EvidenceModel):
    discovery_ref: str
    targets: list[VerificationTargetSpec]
    local_ref_map: dict[str, VersionedRef]
    entity_dependencies: list[EntityDependencyView]
    external_candidates: list[ExternalCandidate]
    bridge_dependencies: list[BridgeDependencyView]
    scope_resolutions: list[VerificationScopeResolution]

    @model_validator(mode="after")
    def exact_reference_closure(self):
        targets = {t.target_id: t for t in self.targets}
        claims = {ref_key(t.claim_ref) for t in self.targets}
        if len(targets) != len(self.targets) or len(claims) != len(self.targets):
            raise ValueError("duplicate_verification_target")
        entities = {ref_key(t.claim_ref) for t in self.targets if t.target_kind == "entity"}
        context_entities = [ref_key(e.entity_ref) for e in self.entity_dependencies]
        if (
            len(context_entities) != len(set(context_entities))
            or entities.intersection(context_entities)
        ):
            raise ValueError("duplicate_entity_dependency")
        entities.update(context_entities)
        external = {candidate.candidate_id for candidate in self.external_candidates}
        bridges = {bridge.bridge_id: bridge for bridge in self.bridge_dependencies}
        scopes = {scope.scope_id: scope for scope in self.scope_resolutions}
        if (len(external) != len(self.external_candidates)
                or len(bridges) != len(self.bridge_dependencies)
                or len(scopes) != len(self.scope_resolutions)):
            raise ValueError("duplicate_verification_dependency")
        for bridge in self.bridge_dependencies:
            for step in bridge.steps:
                if any(ref_key(ref) not in entities for ref in (
                    step.subject_ref, *([step.object_ref] if step.object_ref else []),
                )):
                    raise ValueError("bridge_endpoint_outside_entity_dependencies")
                if (step.kind == "predicate_assertion" and not step.predicate_iri
                        or not step.source_refs):
                    raise ValueError("bridge_step_content_missing")
        for target in self.targets:
            payload = target.payload
            if self.local_ref_map.get(payload.local_id) != target.claim_ref:
                raise ValueError("claim_reference_mismatch")
            dependencies = {ref_key(ref) for ref in target.dependency_refs}
            for endpoint in endpoint_ids(payload):
                ref = self.local_ref_map.get(endpoint)
                if ref is None or ref_key(ref) not in entities or ref_key(ref) not in dependencies:
                    raise ValueError("entity_reference_outside_frozen_dependencies")
            if (
                isinstance(payload, ExternalLinkProposal)
                and payload.external_candidate_id not in external
            ):
                raise ValueError("external_candidate_outside_context")
            for identity in getattr(payload, "bridge_ref_ids", []):
                bridge = bridges.get(identity)
                if bridge is None or ref_key(bridge.bridge_ref) not in dependencies:
                    raise ValueError("bridge_reference_outside_dependencies")
            assertion = getattr(payload, "source_assertion", None)
            for identity in assertion.binding_ids if assertion else []:
                binding = next((t for t in self.targets if t.payload.local_id == identity
                                and t.target_kind == "reference_binding"), None)
                if binding is None or ref_key(binding.claim_ref) not in dependencies:
                    raise ValueError("binding_reference_outside_dependencies")
            if target.scope.members:
                scope = scopes.get(target.scope.scope_id)
                expected = {
                    (ref_key(m.relation_ref), ref_key(m.member_ref)) for m in target.scope.members
                }
                if scope is None or expected != {
                    (ref_key(s.relation_ref), ref_key(s.member_ref)) for s in scope.steps
                }:
                    raise ValueError("scope_resolution_missing")
        return self


class FrozenClaimSet(DiscoveryEnvelope):
    assertion_generation: int = Field(ge=1)
    evidence_revision: int = Field(ge=1)
    content_hash: str
    local_ref_map: dict[str, VersionedRef]
    claim_issues: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def frozen_hash(self):
        if not set(self.claim_issues) <= {payload.local_id for _, payload in iter_proposals(self)}:
            raise ValueError("unknown_claim_issue")
        if self.content_hash != evidence_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        ):
            raise ValueError("frozen_claim_set_hash_mismatch")
        return self


def required_facets(kind: TargetKind, payload, card: SchemaCard) -> list[FacetName]:
    facets = {
        "entity": ["type", "referent", "subject_role"],
        "reference_binding": ["reference_identity", "reference_scope", "counterevidence"],
        "property": ["subject_binding", "field_role", "predicate", "bridge", "value",
                     "unit", "qualifiers", "counterevidence"],
        "relation": ["subject_binding", "object_binding", "predicate", "bridge", "selection",
                     "qualifiers", "counterevidence"],
        "external_link": ["identity_owner", "identity_key", "identity_scope"],
    }[kind].copy()
    if kind == "relation" and len(payload.object_ids) == 1:
        facets.remove("selection")
    if kind == "property" and payload.predicate_iri not in {
        policy.predicate_iri for policy in card.quantity_policies
    }:
        facets.remove("unit")
    return facets


def build_verification_input(
    claims: FrozenClaimSet, *, discovery_ref: str, context,
    card: SchemaCard, scope: TraversalScope,
    entity_dependencies: list[EntityDependencyView], external_candidates: list[ExternalCandidate],
    bridge_dependencies: list[BridgeDependencyView],
    scope_resolutions: list[VerificationScopeResolution],
) -> VerificationInput:
    """Build the entire verifier input from one confirmed discovery generation.

    Card/scope are explicit frozen inputs, not inferred from reference strings.
    The caller loads discovery_ref and supplies only already-authorized objects.
    """
    from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote

    claims = FrozenClaimSet.model_validate(claims.model_dump(mode="json"), strict=True)
    if claims.evidence_revision != context.protocol_state.get("evidence_revision", 1):
        # Additional evidence is allowed; it never silently changes the claim generation.
        if claims.evidence_revision > context.protocol_state.get("evidence_revision", 1):
            raise ValueError("future_claim_evidence_revision")
    bridge_map = {b.bridge_id: b for b in bridge_dependencies}
    for dependency in [*entity_dependencies, *bridge_dependencies]:
        type(dependency).model_validate(dependency.model_dump(mode="json"), strict=True)
    anchors = [a for e in entity_dependencies for a in e.source_refs]
    anchors.extend(a for b in bridge_dependencies for step in b.steps for a in step.source_refs)
    for resolution in scope_resolutions:
        for step in resolution.steps:
            anchors.extend(step.evidence_refs)
            anchors.extend(a for qualifier in step.applicability for a in qualifier.evidence_refs)
    allowed_anchors = [fragment.bounded_anchor() for fragment in context.fragments]
    if any(not any(_anchor_covers(allowed, anchor) for allowed in allowed_anchors)
           for anchor in anchors):
        raise ValueError("dependency_source_outside_context")
    for candidate in external_candidates:
        for match in candidate.matches:
            quote = match.document_quote
            resolve_fragment_quote(quote.evidence_id, quote.text, context.fragments,
                                   context_text=quote.context_text, fact_required=True)
    targets = []
    for kind, payload in iter_proposals(claims):
        if payload.local_id in claims.claim_issues:
            continue
        if kind == "entity" and payload.class_iri not in allowed_entity_classes(card):
            raise ValueError("class_outside_menu")
        if kind in {"property", "relation"}:
            predicate_type = SlotSpec if kind == "property" else EdgeSpec
            if not any(isinstance(p, predicate_type) and p.iri == payload.predicate_iri
                       for p in card.predicates):
                raise ValueError("predicate_outside_menu")
        for quote in iter_quotes(payload):
            resolve_fragment_quote(
                quote.evidence_id, quote.text, context.fragments, context_text=quote.context_text,
            )
        if any(e not in claims.local_ref_map for e in endpoint_ids(payload)):
            raise ValueError("entity_reference_missing")
        if any(identity not in bridge_map for identity in getattr(payload, "bridge_ref_ids", [])):
            raise ValueError("bridge_reference_missing")
        if payload.local_id not in claims.local_ref_map:
            raise ValueError("claim_reference_missing")
        dependencies = [claims.local_ref_map[e] for e in endpoint_ids(payload)]
        dependencies.extend(
            bridge_map[identity].bridge_ref for identity in getattr(payload, "bridge_ref_ids", [])
        )
        assertion = getattr(payload, "source_assertion", None)
        dependencies.extend(claims.local_ref_map[identity]
                            for identity in (assertion.binding_ids if assertion else []))
        dependencies.extend(member.relation_ref for member in scope.members)
        dependencies = [VersionedRef(id=identity, revision=revision)
                        for identity, revision in sorted({ref_key(r) for r in dependencies})]
        digest = claim_content_hash(kind, payload, scope, dependencies)
        claim_ref = claims.local_ref_map[payload.local_id]
        targets.append(VerificationTargetSpec(
            target_id=evidence_hash({"claim_ref": claim_ref, "content_hash": digest}),
            target_kind=kind, claim_ref=claim_ref, content_hash=digest,
            required_facets=required_facets(kind, payload, card),
            payload=payload.model_copy(deep=True),
            scope=scope, dependency_refs=dependencies,
        ))
    current_entities = {ref_key(t.claim_ref) for t in targets if t.target_kind == "entity"}
    return VerificationInput(
        discovery_ref=discovery_ref, targets=targets, local_ref_map=claims.local_ref_map,
        entity_dependencies=[e for e in entity_dependencies
                             if ref_key(e.entity_ref) not in current_entities],
        external_candidates=external_candidates, bridge_dependencies=bridge_dependencies,
        scope_resolutions=scope_resolutions,
    )


def iter_quotes(value):
    if isinstance(value, Quote):
        yield value
    elif isinstance(value, EvidenceModel):
        for name in type(value).model_fields:
            yield from iter_quotes(getattr(value, name))
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_quotes(item)


def finalize_claims(
    claims: FrozenClaimSet, verified: VerifiedClaimSet, *,
    deterministic_results: dict[str, ClaimCheckResult], scope: TraversalScope,
    context, card: SchemaCard, verification_input: VerificationInput, registry,
    entity_proofs: dict | None = None,
    reference_resolution: bool = False,
    known_resolutions: list[dict] | None = None,
    validation_feedback: dict[str, list[str]] | None = None,
):
    """Build only proven graph statements; never shorten a failed relationship group."""
    from app.services.extraction.ontology_guided.contracts import (
        GraphEdge,
        GraphNode,
        GraphProperty,
        GraphRelationshipGroup,
    )
    from app.services.extraction.ontology_guided.executor import TaskOutcome
    from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
    from app.services.extraction.ontology_guided.verification import (
        ProofGate,
        build_generic_proof_menu,
    )

    FrozenClaimSet.model_validate(claims.model_dump(mode="json"), strict=True)
    if (verified.context_hash != context.context_hash
            or verified.context_hash != context.target.context_hash):
        raise ValueError("verification_context_mismatch")
    targets = {target.target_id: target for target in verification_input.targets}
    results = {result.target_id: result for result in verified.targets}
    if len(results) != len(verified.targets) or set(targets) != set(results):
        raise ValueError("verification_target_set_mismatch")
    proposals = {payload.local_id: payload for _, payload in iter_proposals(claims)}
    if {target.payload.local_id for target in targets.values()} != (
        set(proposals) - set(claims.claim_issues)
    ):
        raise ValueError("verification_frozen_target_set_mismatch")
    for target in targets.values():
        VerificationTargetSpec.model_validate(target.model_dump(mode="json"), strict=True)
        if (target.scope != scope or target.payload != proposals.get(target.payload.local_id)
                or claims.local_ref_map.get(target.payload.local_id) != target.claim_ref
                or results[target.target_id].content_hash != target.content_hash):
            raise ValueError("verification_frozen_content_mismatch")
    if (not proposals and not claims.claim_issues
            and all(observation.kind == "missing" for observation in claims.observations)):
        return TaskOutcome(
            semantic_outcome="not_checked", complete=True, reason_code="record_no_claims",
            reason="已检查当前记录，未提出可核验声明；不表示全文没有此类事实。",
        )
    proofs = dict(entity_proofs or {})
    for dependency in verification_input.entity_dependencies:
        if dependency.grounding_kind == "document_root":
            proofs[ref_key(dependency.entity_ref)] = dependency
    nodes, edges, groups, properties, proof_payloads, decision_payloads = [], [], [], [], [], []
    reference_proofs, canonical, resolutions = {}, {}, {}
    statuses, issues = [], [issue for values in claims.claim_issues.values() for issue in values]
    if claims.claim_issues:
        statuses.append("undetermined")

    def anchors(quotes):
        return [resolve_fragment_quote(q.evidence_id, q.text, context.fragments,
                                       context_text=q.context_text)[0] for q in quotes]

    def record_id(anchor):
        candidates = [view.record_id for view in registry.records.record_views
                      if any(_anchor_covers(source, anchor) for source in (
                          *view.source_refs, *view.header_refs, *view.note_refs,
                          *view.parent_context_refs,
                      ))]
        if context.record_id in candidates:
            return context.record_id
        if len(candidates) == 1:
            return candidates[0]
        raise ValueError("record_composition_ambiguous")

    def graph_ref(reference):
        return canonical.get(ref_key(reference), reference)

    for target in sorted(targets.values(), key=lambda t: {
        "entity": 0, "reference_binding": 1,
    }.get(t.target_kind, 2)):
        result = results[target.target_id]
        payload = target.payload
        checks = deterministic_results.get(payload.local_id, ClaimCheckResult())
        bundle = ProofGate().evaluate_frozen_claim(
            target, result.decisions, entity_proofs=proofs,
            proof_menu=build_generic_proof_menu(card, context, target.required_facets,
                                               reference_bindings=bool(claims.reference_bindings)),
            checks=checks, context=context, verification_input=verification_input,
            reference_proofs=reference_proofs,
        )
        missing = [*result.validation_issues, *bundle.validation_issues]
        if result.missing_facets:
            missing.extend(f"{facet}_not_supported" for facet in result.missing_facets)
        if validation_feedback is not None and missing:
            validation_feedback[payload.local_id] = list(dict.fromkeys(missing))
        accepted = bundle.policy_eligible and not missing
        status = ("supported" if accepted else "unsupported" if any(
            decision.verdict == "unsupported" for decision in result.decisions
        ) else "undetermined")
        statuses.append(status)
        issues.extend(missing)
        decision_payloads.extend(decision.model_dump(mode="json") for decision in result.decisions)
        if bundle.predicate_evidence is not None:
            proof_payloads.append(bundle.predicate_evidence.model_dump(mode="json"))
        if target.target_kind == "entity":
            proofs[ref_key(target.claim_ref)] = bundle
        if target.target_kind == "reference_binding" and accepted:
            reference_proofs[ref_key(target.claim_ref)] = bundle
        if not accepted:
            continue
        by_facet = {decision.check_kind: decision for decision in result.decisions}

        def decision_ref(name):
            return VersionedRef(id=by_facet[name].decision_id, revision=1)

        def support(name):
            decision = by_facet.get(name)
            return decision.support_refs if decision else []

        if target.target_kind == "reference_binding":
            source_ref = claims.local_ref_map[payload.source_id]
            destination = claims.local_ref_map[payload.target_id]
            current_ref = graph_ref(source_ref)
            if (ref_key(current_ref) in (entity_proofs or {}) and current_ref != destination):
                statuses[-1] = "undetermined"
                issues.append("reference_binding_conflicts_with_registered_identity")
                reference_proofs.pop(ref_key(target.claim_ref), None)
                continue
            resolution = resolutions.get(source_ref.id)
            if resolution is None:
                statuses[-1] = "undetermined"
                issues.append("reference_binding_source_not_materialized")
                reference_proofs.pop(ref_key(target.claim_ref), None)
                continue
            canonical[ref_key(source_ref)] = destination
            resolution.update(
                entity_ref=destination.model_dump(mode="json"),
                binding_ref=target.claim_ref.model_dump(mode="json"),
                binding_source_refs=[a.model_dump(mode="json") for a in anchors(payload.support)],
                dependency_refs=[ref.model_dump(mode="json") for ref in (
                    *target.dependency_refs, target.claim_ref,
                    *(VersionedRef(id=d.decision_id, revision=1) for d in result.decisions),
                )],
                binding_dependency_refs=[ref.model_dump(mode="json") for ref in (
                    *target.dependency_refs,
                    *(VersionedRef(id=d.decision_id, revision=1) for d in result.decisions),
                )],
            )
            continue

        if target.target_kind == "entity":
            sources = anchors(payload.mentions if payload.representation == "mention" else
                              [component.quote for component in payload.record_components])
            try:
                if payload.representation == "mention":
                    mentions = [registry.register(
                        evidence_id=anchor.evidence_id,
                        start=anchor.span_start, end=anchor.span_end,
                        text=registry.ir.resolve(anchor), record_view_ref=(
                            registry.records.record_views_by_id[record_id(anchor)].record_view_id
                        ),
                    ) for anchor in sources]
                    referent = registry.create_referent([
                        mention.mention_id for mention in mentions
                    ])
                else:
                    views = list(dict.fromkeys(
                        registry.records.record_views_by_id[record_id(anchor)].record_view_id
                        for anchor in sources
                    ))
                    referent = registry.create_record_referent(
                        views, component_refs=sources,
                        composition_decision=by_facet["referent"],
                        subject_role_decision=by_facet["subject_role"],
                    )
            except ValueError as exc:
                # A failed grounding never becomes an eligible endpoint.
                proofs.pop(ref_key(target.claim_ref), None)
                statuses[-1] = "undetermined"
                issues.append(str(exc))
                continue
            node_dependencies = [*target.dependency_refs, decision_ref("type"),
                                 decision_ref("referent"), decision_ref("subject_role")]
            nodes.append(GraphNode(
                entity_id=target.claim_ref.id, revision=target.claim_ref.revision,
                class_iri=payload.class_iri, class_label=payload.class_iri,
                label=" / ".join(dict.fromkeys(registry.ir.resolve(anchor) for anchor in sources)),
                evidence_refs=list({evidence_hash(anchor): anchor for anchor in [
                    *sources, *(anchor for decision in result.decisions
                                for anchor in decision.support_refs),
                ]}.values()), grounding_kind=payload.representation,
                referent_ref=VersionedRef(id=referent.referent_id, revision=referent.revision),
                type_decision_ref=decision_ref("type"),
                referent_decision_ref=decision_ref("referent"),
                composition_decision_ref=(decision_ref("referent")
                                          if payload.representation == "record" else None),
                dependency_refs=node_dependencies,
            ))
            if reference_resolution:
                # Identity is document-local and derived only from an independently
                # accepted physical grounding + exact class, never a normalized label.
                signature = {
                    "class_iri": payload.class_iri, "representation": payload.representation,
                    "sources": sorted(evidence_hash(a) for a in sources),
                }
                entity_ref = VersionedRef(id=stable_id("document-entity", signature), revision=1)
                existing = [dependency for dependency in verification_input.entity_dependencies
                            if dependency.proposal is not None
                            and dependency.class_iri == payload.class_iri
                            and dependency.proposal.representation == payload.representation]
                # Physical mention reuse is exact. Record compositions keep their
                # original claim identity unless a separate binding is proven.
                if payload.representation == "mention":
                    matches = [dependency.entity_ref for dependency in existing
                               if {evidence_hash(a) for a in anchors(dependency.proposal.mentions)}
                               == {evidence_hash(a) for a in sources}]
                    if len(matches) == 1:
                        entity_ref = matches[0]
                    previous = [item for item in known_resolutions or []
                                if item["class_iri"] == payload.class_iri
                                and item["scope"] == scope.model_dump(mode="json")
                                and {evidence_hash(a) for a in item["source_refs"]}
                                == {evidence_hash(a) for a in sources}
                                and ref_key(VersionedRef.model_validate(item["entity_ref"]))
                                in (entity_proofs or {})]
                    prior_refs = {ref_key(VersionedRef.model_validate(item["entity_ref"]))
                                  for item in previous}
                    if len(prior_refs) == 1:
                        entity_ref = VersionedRef.model_validate(previous[0]["entity_ref"])
                else:
                    entity_ref = target.claim_ref
                canonical[ref_key(target.claim_ref)] = entity_ref
                resolutions[target.claim_ref.id] = {
                    "claim_ref": target.claim_ref.model_dump(mode="json"),
                    "entity_ref": entity_ref.model_dump(mode="json"),
                    "local_id": payload.local_id, "class_iri": payload.class_iri,
                    "source_refs": [a.model_dump(mode="json") for a in sources],
                    "binding_ref": None, "binding_source_refs": [],
                    "dependency_refs": [r.model_dump(mode="json") for r in node_dependencies],
                    "entity_dependency_refs": [r.model_dump(mode="json")
                                               for r in node_dependencies],
                    "binding_dependency_refs": [],
                    "scope": scope.model_dump(mode="json"),
                }
                if payload.representation == "mention" and previous and len(prior_refs) == 1:
                    inherited = previous[0]
                    resolutions[target.claim_ref.id].update(
                        binding_ref=inherited["binding_ref"],
                        binding_source_refs=inherited["binding_source_refs"],
                        binding_dependency_refs=inherited.get("binding_dependency_refs", []),
                        dependency_refs=[*resolutions[target.claim_ref.id]["dependency_refs"],
                                         *inherited["dependency_refs"]],
                    )
            continue
        if target.target_kind == "external_link":
            from app.schemas.evidence import ExternalRecordProvenance

            subject = claims.local_ref_map[payload.subject_id]
            node = next((item for item in nodes if (item.entity_id, item.revision)
                         == ref_key(subject)), None)
            if node is None:
                current = (entity_proofs or {}).get(ref_key(subject))
                if isinstance(current, GraphNode):
                    node = current.model_copy(deep=True)
                    nodes.append(node)
            if node is None:
                statuses[-1] = "undetermined"
                issues.append("external_identity_subject_unavailable")
                continue
            candidate = next(item for item in verification_input.external_candidates
                             if item.candidate_id == payload.external_candidate_id)
            references = list({evidence_hash(anchor): anchor for decision in result.decisions
                               for anchor in decision.support_refs}.values())
            provenance = [ExternalRecordProvenance(
                system=candidate.system, dataset=candidate.dataset,
                record_key=candidate.record_key, record_version=candidate.record_version,
                field_path=match.record_field, value=match.record_value,
                identity_match_evidence=references,
            ) for match in candidate.matches]
            node.external_provenance = list({evidence_hash(item): item for item in (
                *node.external_provenance, *provenance,
            )}.values())
            node.identity_decision_refs = list({ref_key(ref): ref for ref in (
                *node.identity_decision_refs,
                *(VersionedRef(id=decision.decision_id, revision=1)
                  for decision in result.decisions),
            )}.values())
            node.identity_status = "verified"
            # External fields remain provenance; no document property/edge is invented.
            continue
        predicate = next(p for p in card.predicates if p.iri == payload.predicate_iri)
        if reference_resolution:
            declared = {ref_key(ref) for ref in target.dependency_refs}
            fresh_bindings = {ref_key(item.claim_ref) for item in targets.values()
                              if item.target_kind == "reference_binding"}
            endpoint_bindings = [resolution["binding_ref"]
                                 for identity in endpoint_ids(payload)
                                 if (resolution := resolutions.get(
                                     claims.local_ref_map[identity].id,
                                 )) is not None and resolution["binding_ref"]]
            if any(ref_key(VersionedRef.model_validate(ref)) in fresh_bindings
                   and ref_key(VersionedRef.model_validate(ref)) not in declared
                   for ref in endpoint_bindings):
                statuses[-1] = "undetermined"
                issues.append("reference_binding_dependency_missing")
                continue
        qualifier = payload.qualifiers
        applicability = [GroundedScopeQualifier(
            predicate_iri=q.predicate_iri, text=q.quote.text, evidence_refs=anchors([q.quote]),
        ) for q in qualifier.scope_qualifiers]
        proof = bundle.predicate_evidence
        shared = dict(
            candidate_id=target.claim_ref.id, revision=target.claim_ref.revision,
            subject_ref=graph_ref(claims.local_ref_map[payload.subject_id]),
            predicate_iri=payload.predicate_iri,
            predicate_label=predicate.label, polarity=qualifier.polarity,
            modality=qualifier.modality, scope=scope,
            conditions=[quote.text for quote in qualifier.condition_support],
            condition_evidence_refs=anchors(qualifier.condition_support),
            applicability={"qualifiers": [item.model_dump(mode="json") for item in applicability]}
            if applicability else {}, decision_status="supported", structural_valid=True,
            model_supported=True, policy_eligible=True,
            proof_ref=VersionedRef(id=proof.proof_id, revision=proof.proof_revision),
            decision_refs=[VersionedRef(id=d.decision_id, revision=1) for d in result.decisions],
            dependency_refs=list({ref_key(ref): ref for ref in [
                *target.dependency_refs,
                *(graph_ref(claims.local_ref_map[identity]) for identity in endpoint_ids(payload)),
                *(VersionedRef.model_validate(resolutions[claims.local_ref_map[identity].id][
                    "binding_ref"]) for identity in endpoint_ids(payload)
                  if claims.local_ref_map[identity].id in resolutions
                  and resolutions[claims.local_ref_map[identity].id]["binding_ref"]),
            ]}.values()) if reference_resolution else target.dependency_refs,
            evidence_refs=anchors(list(iter_quotes(payload))),
            subject_evidence_refs=support("subject_binding"),
            predicate_evidence_refs=support("predicate"),
            counterevidence_refs=[a for d in result.decisions for a in d.counterevidence_refs],
            reason_code="claim_verified", reason="所有适用核验维度及确定性检查通过。",
        )
        if target.target_kind == "property":
            quantity = checks.quantity
            normalized = (checks.normalized_literal if checks.normalized_literal is not None
                          else payload.value_quote.text)
            if quantity is not None:
                normalized = (quantity.scalar if quantity.form == "scalar" else
                              getattr(quantity, quantity.endpoint_role)
                              if quantity.endpoint_role else quantity.model_dump(mode="json"))
            properties.append(GraphProperty(
                **shared, raw_value=payload.value_quote.text, normalized_value=normalized,
                normalization_record=quantity.model_dump(mode="json") if quantity else {},
                unit_evidence_refs=anchors(payload.unit_support),
                value_evidence_refs=anchors([payload.value_quote]),
            ))
        else:
            objects = [graph_ref(claims.local_ref_map[identity]) for identity in payload.object_ids]
            if len({ref_key(reference) for reference in objects}) != len(objects):
                statuses[-1] = "undetermined"
                issues.append("selection_members_resolve_to_same_entity")
                continue
            if len(objects) == 1:
                edges.append(GraphEdge(**shared, object_ref=objects[0],
                                       object_evidence_refs=support("object_binding")))
            else:
                groups.append(GraphRelationshipGroup(
                    **shared, object_refs=objects, selection=payload.selection,
                    selection_evidence_refs=support("selection"),
                    object_evidence_refs=support("object_binding"),
                ))
    if not statuses or claims.observations:
        statuses.append("undetermined")
    semantic = ("supported" if "supported" in statuses else "unsupported"
                if all(status == "unsupported" for status in statuses) else "undetermined")
    if reference_resolution:
        materialized = {}
        for node in nodes:
            claim_ref = VersionedRef(id=node.entity_id, revision=node.revision)
            entity_ref = graph_ref(claim_ref)
            existing = (entity_proofs or {}).get(ref_key(entity_ref))
            current = materialized.get(entity_ref.id, existing)
            if isinstance(current, GraphNode):
                if node.identity_status == "verified" and node.external_provenance:
                    materialized[entity_ref.id] = current.model_copy(update={
                        "identity_status": "verified",
                        "external_provenance": list({evidence_hash(item): item for item in (
                            *current.external_provenance, *node.external_provenance,
                        )}.values()),
                        "identity_decision_refs": list({ref_key(ref): ref for ref in (
                            *current.identity_decision_refs, *node.identity_decision_refs,
                        )}.values()),
                    })
                continue  # Keep the canonical node's exact frozen content.
            materialized.setdefault(entity_ref.id, node.model_copy(update={
                "entity_id": entity_ref.id, "revision": entity_ref.revision,
                "dependency_refs": [*node.dependency_refs, claim_ref],
            }))
        nodes = list(materialized.values())
    return TaskOutcome(
        semantic_outcome=semantic, complete="undetermined" not in statuses,
        reason_code=issues[0] if issues else f"record_{semantic}",
        reason="；".join(dict.fromkeys(issues)) or (
            "当前记录仍有待核实的声明或观察。" if "undetermined" in statuses
            else "已按声明及依赖完成独立核验。"
        ),
        nodes=nodes, edges=edges, properties=properties, relationship_groups=groups,
        proof_payloads=proof_payloads, decision_payloads=decision_payloads,
        **({"reference_resolutions": list(resolutions.values())} if reference_resolution else {}),
    )
