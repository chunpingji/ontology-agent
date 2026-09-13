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


class ProofGate:
    """Structural/policy gate; semantic support remains an independent decision."""

    def __init__(self, registry: PredicatePolicyRegistry | None = None):
        self.registry = registry or PredicatePolicyRegistry()

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
