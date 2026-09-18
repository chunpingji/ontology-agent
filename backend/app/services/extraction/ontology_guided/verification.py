"""Predicate policies, target binding, value presence and proof eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.extraction.evidence_identity import evidence_hash, stable_id
from app.services.extraction.ontology_guided.contracts import (
    PredicateEvidence,
    SemanticDecision,
    ValueObservation,
    VerificationBundle,
    VerificationTarget,
    VersionedRef,
)

ACCEPTED_BRIDGES = frozenset(
    {
        "explicit_assertion",
        "owned_field_group",
        "role_mapped_table",
        "resolved_reference_chain",
        "document_subject_description",
    }
)
RETRIEVAL_ONLY_BRIDGES = frozenset(
    {"same_document", "same_name", "adjacent_only", "path_reachability"}
)
PROOF_BUNDLE_CHECK_KINDS = frozenset(
    {
        "type",
        "field_role",
        "unit_binding",
        "local_coreference",
        "global_identity",
        "predicate_entailment",
        "bridge_entailment",
        "applicability",
    }
)


@dataclass(frozen=True)
class PredicatePolicy:
    policy_id: str
    kind: Literal["document_description", "composition", "data_property", "generic_relation"]
    allowed_bridges: frozenset[str]
    requires_object_role: bool
    requires_value_role: bool
    requires_predicate_step: bool = False


class PredicatePolicyRegistry:
    version = "predicate-policy-v1"

    def task_menu(self, predicate_iri: str, *, is_property: bool, context) -> dict:
        policy = self.policy_for(predicate_iri, is_property=is_property)
        available = {"explicit_assertion", "document_subject_description"}
        if any(b.kind == "table" and b.mapping_status == "structural_candidate"
               for b in context.field_bindings):
            available.add("role_mapped_table")
        if any(b.kind == "field_group" for b in context.field_bindings):
            available.add("owned_field_group")
        # A source chain needs explicit linking evidence; co-occurrence is not a chain.
        if any(f.purpose == "resolved_reference_chain" for f in context.fragments):
            available.add("resolved_reference_chain")
        required = {"field_role", "predicate_entailment", "bridge_entailment", "applicability"}
        if not is_property:
            required.add("type")
        if not context.target.subject_ref.is_document_root:
            required.add("local_coreference")
        menu = {
            "version": "proof-menu-v1", "registry_version": self.version,
            "predicate_iri": predicate_iri, "kind": "property" if is_property else "relationship",
            "allowed_bridges": sorted(policy.allowed_bridges & available),
            "required_checks": sorted(required),
            "structure_hash": evidence_hash(sorted([
                b.model_dump(mode="json", exclude={"field_binding_id"})
                for b in context.field_bindings
            ], key=evidence_hash)),
        }
        return {**menu, "menu_hash": evidence_hash(menu)}

    def policy_for(self, predicate_iri: str, *, is_property: bool = False) -> PredicatePolicy:
        local_name = predicate_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        if local_name == "describes":
            return PredicatePolicy(
                policy_id=f"{self.version}:document-description",
                kind="document_description",
                allowed_bridges=frozenset({"explicit_assertion", "document_subject_description"}),
                requires_object_role=True,
                requires_value_role=False,
            )
        if local_name == "hasActiveIngredient":
            return PredicatePolicy(
                policy_id=f"{self.version}:composition",
                kind="composition",
                allowed_bridges=frozenset(
                    {"explicit_assertion", "role_mapped_table", "resolved_reference_chain"}
                ),
                requires_object_role=True,
                requires_value_role=False,
                requires_predicate_step=True,
            )
        if is_property:
            return PredicatePolicy(
                policy_id=f"{self.version}:data-property",
                kind="data_property",
                allowed_bridges=frozenset(
                    {"explicit_assertion", "owned_field_group", "role_mapped_table"}
                ),
                requires_object_role=False,
                requires_value_role=True,
            )
        return PredicatePolicy(
            policy_id=f"{self.version}:generic-relation",
            kind="generic_relation",
            allowed_bridges=frozenset(
                {"explicit_assertion", "role_mapped_table", "resolved_reference_chain"}
            ),
            requires_object_role=True,
            requires_value_role=False,
            requires_predicate_step=True,
        )


def validate_decision(
    target: VerificationTarget,
    decision: SemanticDecision,
    *,
    compatible_check_kinds: frozenset[str] | None = None,
) -> None:
    if decision.target_id != target.target_id:
        raise ValueError("decision belongs to another verification target")
    allowed_kinds = compatible_check_kinds or frozenset({target.check_kind})
    if decision.check_kind not in allowed_kinds:
        raise ValueError("decision check kind does not match target")
    searched = {item.evidence_id for item in decision.searched_context_refs}
    cited = {item.evidence_id for item in [*decision.support_refs, *decision.counterevidence_refs]}
    if not cited.issubset(searched):
        raise ValueError("decision cites evidence outside its searched context")


def observe_value(
    *,
    slot_ref: VersionedRef,
    raw_text: str | None,
    source_refs,
) -> ValueObservation:
    if raw_text is None:
        presence, interpretation, normalized = "not_mentioned", "unspecified", None
        serialized = ""
    else:
        serialized = raw_text.strip()
        placeholder = serialized.casefold() in {
            "n/a",
            "na",
            "not applicable",
            "unknown",
            "未知",
            "不适用",
            "—",
            "-",
        }
        if placeholder:
            presence, interpretation, normalized = "placeholder", "unspecified", None
        else:
            presence, interpretation, normalized = "present", "value", serialized
    identity = [slot_ref.model_dump(mode="json"), serialized, source_refs]
    return ValueObservation(
        observation_id=stable_id("value-observation", identity),
        slot_ref=slot_ref,
        raw_text=serialized,
        source_refs=source_refs,
        presence=presence,
        interpretation=interpretation,
        normalized_value=normalized,
    )


def build_generic_proof_menu(card, context, required_facets, *, reference_bindings=False) -> dict:
    """Only structure selects bridges; ontology IRIs never select hidden policy."""
    available = {"explicit_assertion"}
    if context.target.subject_ref.is_document_root:
        available.add("document_subject_description")
    if any(binding.kind == "field_group" for binding in context.field_bindings):
        available.add("owned_field_group")
    if any(binding.kind == "table" and binding.mapping_status == "structural_candidate"
           for binding in context.field_bindings):
        available.add("role_mapped_table")
    if context.proof_dependencies or reference_bindings:
        available.add("resolved_reference_chain")
    menu = {
        "version": "ontology-tool-proof-v1", "schema_card_id": card.schema_card_id,
        "context_hash": context.target.context_hash,
        "required_facets": sorted(required_facets), "allowed_bridges": sorted(available),
        "quantity_predicates": sorted(policy.predicate_iri for policy in card.quantity_policies),
        "identity_keys": [key.model_dump(mode="json") for key in card.identity_keys],
        "unsupported_predicates": sorted({
            issue.predicate_iri for issue in card.unsupported_constraints
        }),
        "structure_hash": evidence_hash(context.field_bindings),
    }
    return {**menu, "menu_hash": evidence_hash(menu)}


class ProofGate:
    """Structural/policy gate; semantic support remains an independent decision."""

    def __init__(self, registry: PredicatePolicyRegistry | None = None):
        self.registry = registry or PredicatePolicyRegistry()

    def evaluate_frozen_claim(
        self, claim, decisions, *, entity_proofs, proof_menu, checks, context,
        verification_input, reference_proofs=None,
    ) -> VerificationBundle:
        """027 gate: exact facets and dependencies, with no predicate-name policy."""
        from app.services.extraction.ontology_guided.claim_protocol import (
            EntityDependencyView,
            _anchor_covers,
            endpoint_ids,
            ref_key,
        )
        from app.services.extraction.ontology_guided.contracts import (
            BridgeStep,
            GraphNode,
            SubjectRef,
        )

        payload = claim.payload
        issues = list(checks.issues)
        if (proof_menu.get("version") != "ontology-tool-proof-v1"
                or proof_menu.get("context_hash") != context.target.context_hash
                or proof_menu.get("menu_hash") != evidence_hash({
                    key: value for key, value in proof_menu.items() if key != "menu_hash"
                }) or proof_menu.get("required_facets") != sorted(claim.required_facets)):
            issues.append("proof_menu_mismatch")
        by_kind = {decision.check_kind: decision for decision in decisions}
        if len(by_kind) != len(decisions) or set(by_kind) != set(claim.required_facets):
            issues.append("proof_facet_set_mismatch")
        allowed_anchors = [fragment.bounded_anchor() for fragment in context.fragments]
        for decision in decisions:
            if decision.target_id != claim.target_id:
                issues.append("proof_target_mismatch")
            for anchor in [*decision.support_refs, *decision.counterevidence_refs]:
                if not any(_anchor_covers(allowed, anchor) for allowed in allowed_anchors):
                    issues.append("proof_source_outside_context")
            if decision.verdict != "supported" or not decision.support_refs:
                issues.append(f"{decision.check_kind}_not_supported")

        predicate = getattr(payload, "predicate_iri", None)
        if claim.target_kind == "external_link":
            from app.services.extraction.ontology_guided.source_citations import (
                resolve_fragment_quote,
            )

            candidate = next((item for item in verification_input.external_candidates
                              if item.candidate_id == payload.external_candidate_id), None)
            subject_ref = verification_input.local_ref_map[payload.subject_id]
            entity = next((target.payload for target in verification_input.targets
                           if target.target_kind == "entity" and target.claim_ref == subject_ref),
                          None)
            if entity is None:
                entity = next((dep.proposal for dep in verification_input.entity_dependencies
                               if dep.entity_ref == subject_ref), None)
            key_matches = [(match.predicate_iri, match.document_quote, match.record_value)
                           for match in candidate.matches
                           if match.match_kind in {"exact_key", "key_component"}]
            if entity is not None:
                key_matches.extend((identifier.predicate_iri, identifier.value_quote,
                                    field.raw_value)
                                   for identifier in entity.identifier_claims
                                   for field in candidate.mapped_fields
                                   if field.predicate_iri == identifier.predicate_iri
                                   and field.raw_value == identifier.value_quote.text)
            keys = [key for key in proof_menu.get("identity_keys", [])
                    if key["class_iri"] == candidate.class_iri and key["property_iris"]
                    and set(key["property_iris"]) <= {
                        predicate_iri for predicate_iri, _, _ in key_matches
                    }]
            if not keys:
                issues.append("external_identity_key_not_proven")
            else:
                required = set().union(*(set(key["property_iris"]) for key in keys))
                support = by_kind.get("identity_key")
                for predicate_iri, quote, record_value in key_matches:
                    if predicate_iri not in required:
                        continue
                    try:
                        anchor, _ = resolve_fragment_quote(
                            quote.evidence_id, quote.text, context.fragments,
                            context_text=quote.context_text,
                        )
                        covered = support is not None and any(
                            _anchor_covers(ref, anchor) for ref in support.support_refs
                        )
                    except ValueError:
                        covered = False
                    if record_value != quote.text or not covered:
                        issues.append("external_identity_component_not_verified")
        if predicate in proof_menu.get("unsupported_predicates", []):
            issues.append("constraint_unresolved")
        for endpoint in endpoint_ids(payload):
            ref = verification_input.local_ref_map.get(endpoint)
            proof = entity_proofs.get(ref_key(ref)) if ref else None
            endpoint_entity = next((target.payload for target in verification_input.targets
                                    if target.target_kind == "entity" and target.claim_ref == ref),
                                   None)
            if endpoint_entity is None:
                endpoint_entity = next((dependency
                                        for dependency in verification_input.entity_dependencies
                                        if dependency.entity_ref == ref), None)
            expected_class = endpoint_entity.class_iri if endpoint_entity is not None else None
            if isinstance(proof, EntityDependencyView):
                valid = (proof.grounding_kind == "document_root"
                         and proof.class_iri == expected_class
                         and proof.entity_ref == context.target.document_context.root_ref
                         and proof.entity_ref == ref and proof.root_origin in {
                             "user_specified", "user_specified_document_root",
                         })
            elif isinstance(proof, GraphNode):
                # Only the coordinator supplies current nodes; model proposals
                # cannot set this map. Exact decision dependencies stay attached.
                identity_refs = [proof.type_decision_ref, proof.referent_decision_ref]
                valid = (
                    ref is not None and not proof.root
                    and (proof.entity_id, proof.revision) == ref_key(ref)
                    and proof.class_iri == expected_class
                    and proof.decision_status == "supported"
                    and proof.independent_review != "rejected"
                    and proof.referent_ref is not None
                    and all(value is not None and value in proof.dependency_refs
                            for value in identity_refs)
                    and (proof.grounding_kind != "record"
                         or proof.composition_decision_ref in proof.dependency_refs)
                )
            else:
                valid = (
                    isinstance(proof, VerificationBundle) and proof.policy_eligible
                    and proof.target.claim_ref == ref and proof.target.check_kind == "type"
                    and proof.target.subject_ref.class_iri == expected_class
                    and {d.check_kind for d in proof.decisions} == {
                        "type", "referent", "subject_role",
                    }
                    and all(d.verdict == "supported" for d in proof.decisions)
                )
            if not valid:
                issues.append("entity_dependency_not_verified")

        required_checks = {"binding"} if claim.target_kind in {"property", "relation"} else set()
        if claim.target_kind == "property":
            required_checks.update({"metric", "shacl"})
        if claim.target_kind == "relation":
            required_checks.add("relation_graph")
        for check in sorted(required_checks):
            if checks.checks.get(check) is not True:
                issues.append(f"{check}_not_passed")
        if predicate in proof_menu.get("quantity_predicates", []) and checks.quantity is None:
            issues.append("quantity_representation_missing")

        source_assertion = None
        if claim.target_kind == "relation":
            from app.services.extraction.ontology_guided.source_assertions import (
                validate_source_assertion,
            )

            source_assertion = validate_source_assertion(
                claim, context=context, verification_input=verification_input,
                decisions=decisions, reference_proofs=reference_proofs,
            )
            issues.extend(source_assertion.issues)
        elif claim.target_kind == "reference_binding":
            from app.services.extraction.ontology_guided.source_assertions import (
                validate_reference_binding,
            )

            issues.extend(validate_reference_binding(
                claim, context=context, verification_input=verification_input,
                decisions=decisions,
            ))

        def refs(name):
            decision = by_kind.get(name)
            return decision.support_refs if decision is not None else []

        def decision_ref(name):
            decision = by_kind.get(name)
            return [VersionedRef(id=decision.decision_id, revision=1)] if decision else []

        local_refs = verification_input.local_ref_map
        subject = context.target.subject_ref
        if claim.target_kind == "entity":
            subject = SubjectRef(entity_id=claim.claim_ref.id, revision=claim.claim_ref.revision,
                                 class_iri=payload.class_iri)
        else:
            subject_ref = local_refs[payload.source_id if claim.target_kind == "reference_binding"
                                     else payload.subject_id]
            if subject_ref.id != subject.entity_id or subject_ref.revision != subject.revision:
                entity = next((t.payload for t in verification_input.targets
                               if t.target_kind == "entity" and t.claim_ref == subject_ref), None)
                existing = next((e for e in verification_input.entity_dependencies
                                 if e.entity_ref == subject_ref), None)
                subject = SubjectRef(entity_id=subject_ref.id, revision=subject_ref.revision,
                                     class_iri=(entity or existing).class_iri)
        objects = ([local_refs[oid] for oid in payload.object_ids]
                   if claim.target_kind == "relation" else [])
        target_data = context.target.model_dump(mode="json")
        target_data.update(
            target_id=claim.target_id, claim_ref=claim.claim_ref, subject_ref=subject,
            check_kind="type" if claim.target_kind == "entity" else "predicate_entailment",
            predicate_iri=predicate, object_ref=objects[0] if len(objects) == 1 else None,
            object_refs=objects if len(objects) > 1 else [],
            selection=payload.selection if len(objects) > 1 else None,
            literal_hash=claim.content_hash,
        )
        if hasattr(payload, "qualifiers"):
            target_data.update(assertion_polarity=payload.qualifiers.polarity)
        target = VerificationTarget.model_validate(target_data)
        predicate_proof = None
        if claim.target_kind in {"property", "relation"}:
            if payload.bridge_kind not in proof_menu.get("allowed_bridges", []):
                issues.append("bridge_kind_not_available")
            if len(objects) > 1 and (payload.selection == "undetermined" or not refs("selection")):
                issues.append("selection_not_supported")
            steps = source_assertion.steps if source_assertion is not None else []
            if (payload.bridge_kind == "resolved_reference_chain"
                    and getattr(payload, "source_assertion", None) is None):
                bridges = {b.bridge_id: b for b in verification_input.bridge_dependencies}
                bindings, assertions = {}, []
                for bridge_id in payload.bridge_ref_ids:
                    bridge = bridges.get(bridge_id)
                    if bridge is None:
                        issues.append("bridge_reference_missing")
                        continue
                    for step in bridge.steps:
                        if step.kind == "predicate_assertion":
                            assertions.append(step)
                        elif step.object_ref is not None:
                            left, right = ref_key(step.subject_ref), ref_key(step.object_ref)
                            bindings.setdefault(left, set()).add(right)
                            if step.kind == "referent_binding":
                                bindings.setdefault(right, set()).add(left)
                        kind = {
                            "referent_binding": "same_referent", "owner_binding": "role_assignment",
                            "predicate_assertion": "predicate_assertion",
                        }[step.kind]
                        if not step.source_refs or not decision_ref("bridge"):
                            issues.append("bridge_step_proof_missing")
                            continue
                        if step.kind == "predicate_assertion" and step.predicate_iri != predicate:
                            issues.append("bridge_predicate_mismatch")
                        steps.append(BridgeStep(
                            step_kind=kind, input_refs=[step.subject_ref, *(
                                [step.object_ref] if step.object_ref else []
                            )], output_role=step.kind, source_refs=step.source_refs,
                            decision_ref=decision_ref("bridge")[0],
                        ))
                if not any(step.step_kind == "predicate_assertion" for step in steps):
                    issues.append("bridge_predicate_assertion_missing")
                def reachable(start, destination):
                    pending, visited = [ref_key(start)], set()
                    while pending:
                        current = pending.pop()
                        if current == ref_key(destination):
                            return True
                        if current not in visited:
                            visited.add(current)
                            pending.extend(bindings.get(current, ()))
                    return False

                subject_ref = local_refs[payload.subject_id]
                matching = [step for step in assertions
                            if step.predicate_iri == predicate
                            and reachable(subject_ref, step.subject_ref)]
                if (not matching or any(not any(
                    step.object_ref is not None and reachable(obj, step.object_ref)
                    for step in matching
                ) for obj in objects)):
                    issues.append("bridge_endpoint_chain_not_closed")
            if decision_ref("subject_binding"):
                predicate_proof = PredicateEvidence(
                    proof_id=stable_id("claim-proof", [claim.target_id, decisions]),
                    target_id=claim.target_id, predicate_iri=predicate,
                    subject_role_refs=decision_ref("subject_binding"),
                    object_role_refs=decision_ref("object_binding"),
                    value_role_refs=decision_ref("value"), predicate_support_refs=refs("predicate"),
                    bridge_kind=payload.bridge_kind, bridge_steps=steps,
                    dependency_refs=claim.dependency_refs,
                    counterevidence_refs=[
                        anchor for d in decisions for anchor in d.counterevidence_refs
                    ],
                    applicability_refs=decision_ref("qualifiers"),
                    unit_evidence_refs=refs("unit"), selection_support_refs=refs("selection"),
                    modality_support_refs=refs("qualifiers"), verdict="supported" if not issues
                    else "undetermined", proof_policy_version="ontology-tool-proof-v1",
                    normalization_record=(checks.quantity.model_dump(mode="json")
                                          if checks.quantity else {}),
                )
        supported = bool(decisions) and all(d.verdict == "supported" for d in decisions)
        return VerificationBundle(
            bundle_id=stable_id("claim-bundle", [claim.target_id, claim.content_hash,
                                                context.target.context_hash, decisions, checks]),
            target=target, decisions=decisions, predicate_evidence=predicate_proof,
            structural_valid=not issues, model_supported=supported,
            policy_eligible=not issues and supported, validation_issues=list(dict.fromkeys(issues)),
        )

    def evaluate(
        self,
        target: VerificationTarget,
        proof: PredicateEvidence | None,
        decisions: list[SemanticDecision],
        *,
        is_property: bool,
        independent_review: str = "unreviewed",
        proof_menu: dict | None = None,
        context=None,
    ) -> VerificationBundle:
        issues: list[str] = []
        for decision in decisions:
            try:
                # A proof bundle evaluates several controlled facets of the
                # same frozen claim target.  Every facet must retain the exact
                # server-issued target id and cite only its searched context,
                # but its check kind need not equal the bundle's primary
                # ``predicate_entailment`` kind.
                validate_decision(
                    target,
                    decision,
                    compatible_check_kinds=PROOF_BUNDLE_CHECK_KINDS,
                )
            except ValueError as exc:
                issues.append(str(exc))
        policy = self.registry.policy_for(target.predicate_iri or "", is_property=is_property)
        if proof is None:
            issues.append("missing_predicate_proof")
        else:
            if proof.target_id != target.target_id:
                issues.append("proof_target_mismatch")
            if proof.predicate_iri != target.predicate_iri:
                issues.append("proof_predicate_mismatch")
            if proof.bridge_kind not in ACCEPTED_BRIDGES:
                issues.append("retrieval_hint_is_not_predicate_proof")
            if proof.bridge_kind not in policy.allowed_bridges:
                issues.append("bridge_kind_forbidden_for_predicate")
            if not proof.predicate_support_refs:
                issues.append("missing_predicate_support")
            if not proof.subject_role_refs:
                issues.append("missing_subject_role")
            if policy.requires_object_role and not proof.object_role_refs:
                issues.append("missing_object_role")
            if policy.requires_value_role and not proof.value_role_refs:
                issues.append("missing_value_role")
            if policy.requires_predicate_step and proof.bridge_kind == "resolved_reference_chain":
                if not any(step.step_kind == "predicate_assertion" for step in proof.bridge_steps):
                    issues.append("reference_chain_has_no_predicate_assertion")
            if proof.verdict != "supported":
                issues.append("predicate_proof_not_supported")
        required = {
            "predicate_entailment",
            "applicability",
        }
        if proof_menu is not None:
            if context is not None and proof_menu != self.registry.task_menu(
                target.predicate_iri, is_property=is_property, context=context,
            ):
                issues.append("proof_menu_structure_mismatch")
            if (proof_menu.get("predicate_iri") != target.predicate_iri
                    or proof_menu.get("kind") != ("property" if is_property else "relationship")
                    or proof_menu.get("registry_version") != self.registry.version
                    or proof_menu.get("menu_hash") != evidence_hash({
                        k: v for k, v in proof_menu.items() if k != "menu_hash"
                    })):
                issues.append("proof_menu_mismatch")
            required.update({"field_role", "bridge_entailment"})
            if not is_property:
                required.add("type")
            if not target.subject_ref.is_document_root:
                required.add("local_coreference")
            if set(proof_menu.get("required_checks", [])) != required:
                issues.append("proof_menu_required_checks_mismatch")
            if proof is not None and proof.bridge_kind not in proof_menu.get("allowed_bridges", []):
                issues.append("bridge_kind_not_in_task_menu")
            kinds = [d.check_kind for d in decisions]
            if len(kinds) != len(set(kinds)):
                issues.append("duplicate_semantic_check")
            for decision in decisions:
                if decision.verdict == "supported" and not decision.support_refs:
                    issues.append(f"{decision.check_kind}_source_missing")
        by_kind = {decision.check_kind: decision for decision in decisions}
        for kind in required:
            decision = by_kind.get(kind)
            if decision is None or decision.verdict != "supported":
                issues.append(f"{kind}_not_supported")
        model_supported = bool(decisions) and all(
            decision.verdict == "supported" for decision in decisions
        )
        structural_valid = not any(
            issue
            for issue in issues
            if issue not in {"predicate_entailment_not_supported", "applicability_not_supported"}
        )
        policy_eligible = structural_valid and model_supported and proof is not None
        if independent_review == "rejected":
            policy_eligible = False
            issues.append("independent_review_rejected")
        identity = [
            target.target_id,
            proof.model_dump(mode="json") if proof else None,
            [item.model_dump(mode="json") for item in decisions],
            policy.policy_id,
            independent_review,
        ]
        return VerificationBundle(
            bundle_id=stable_id("verification-bundle", identity),
            target=target,
            decisions=decisions,
            predicate_evidence=proof,
            structural_valid=structural_valid,
            model_supported=model_supported,
            policy_eligible=policy_eligible,
            independent_review=independent_review,
            validation_issues=list(dict.fromkeys(issues)),
        )


class EligibilityPolicy:
    version = "ontology-guided-eligibility-v1"

    @staticmethod
    def seed_eligible(
        *,
        root_document_hash: str,
        frozen_document_hash: str,
        root_class_iri: str,
        requested_root_class_iri: str,
        source_role: str,
        revoked: bool = False,
        review_rejected: bool = False,
    ) -> bool:
        return (
            root_document_hash == frozen_document_hash
            and root_class_iri == requested_root_class_iri
            and source_role in {"analysis_source", "default_source"}
            and not revoked
            and not review_rejected
        )

    @staticmethod
    def expandable(
        *,
        type_supported: bool,
        incoming_bundle: VerificationBundle | None,
        polarity: str,
        conditional: bool,
        conflicted: bool,
        review_rejected: bool,
        is_document_root: bool = False,
    ) -> bool:
        if is_document_root:
            return type_supported and not review_rejected
        return (
            type_supported
            and incoming_bundle is not None
            and incoming_bundle.policy_eligible
            and polarity == "affirmed"
            and not conditional
            and not conflicted
            and not review_rejected
        )
