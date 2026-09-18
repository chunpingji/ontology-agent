"""Source roles close on the stated relation even if every model facet says yes."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.schemas.evidence import EvidenceAnchor
from app.services.extraction.ontology_guided.claim_protocol import (
    ClaimCheckResult,
    EntityProposal,
    PropertyProposal,
    Quote,
    ReferenceBindingProposal,
    RelationProposal,
    VerificationTargetSpec,
    claim_content_hash,
)
from app.services.extraction.ontology_guided.context import ContextFragment
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    GraphNode,
    SemanticDecision,
    SubjectRef,
    TraversalScope,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.field_bindings import FieldBinding
from app.services.extraction.ontology_guided.source_assertions import (
    validate_reference_binding,
    validate_source_assertion,
)
from app.services.extraction.ontology_guided.source_citations import resolve_fragment_quote
from app.services.extraction.ontology_guided.verification import ProofGate, build_generic_proof_menu


def relation_case(
    texts, entities, *, subject, objects, subject_quote, object_quotes, predicate_quotes,
    bridge="explicit_assertion", root=None,
):
    fragments = [ContextFragment(
        anchor=EvidenceAnchor(
            document_hash="a" * 64, parser_version="parser", structure_hash="b" * 64,
            evidence_id=identity, section_node_id="section", block_id=identity,
            span_start=0, span_end=len(text),
        ), text=text, purpose="target", fact_eligible=True,
    ) for identity, text in texts.items()]

    def quote(value):
        identity, text = value
        return Quote(evidence_id=identity, text=text, context_text=None)

    def anchor(value):
        item = quote(value)
        return resolve_fragment_quote(item.evidence_id, item.text, fragments)[0]

    refs = {identity: VersionedRef(id=f"entity-{identity}", revision=1) for identity in entities}
    targets = [NS(
        target_kind="entity", claim_ref=refs[identity],
        payload=EntityProposal(
            local_id=identity, class_iri="urn:generic:Object", representation="mention",
            mentions=[quote(source)], record_components=[], identifier_claims=[],
        ),
    ) for identity, source in entities.items()]
    root_ref = refs.get(root, VersionedRef(id="document-root", revision=1))
    dependencies = []
    if root:
        targets = [target for target in targets if target.claim_ref != root_ref]
        dependencies = [NS(
            entity_ref=root_ref, grounding_kind="document_root", root_origin="user_specified",
            proposal=None, source_refs=[anchor(entities[root])],
        )]
    source_assertion = NS(
        subject_support=[quote(value) for value in subject_quote],
        object_support=[NS(object_id=identity, support=[quote(value) for value in sources])
                        for identity, sources in object_quotes.items()],
        predicate_support=[quote(value) for value in predicate_quotes], binding_ids=[],
    )
    payload = NS(
        subject_id=subject, object_ids=objects, source_assertion=source_assertion,
        bridge_kind=bridge, predicate_iri="urn:generic:contains",
    )
    scope = TraversalScope.create()
    claim = NS(payload=payload, dependency_refs=list(refs.values()), scope=scope)
    context = NS(
        fragments=fragments, field_bindings=[],
        target=NS(document_context=NS(root_ref=root_ref), context_hash="context",
                  source_scope_hash="scope"),
    )
    verification_input = NS(targets=targets, entity_dependencies=dependencies, local_ref_map=refs)
    decisions = [NS(
        check_kind=name, verdict="supported", decision_id=f"decision-{name}",
        support_refs=[fragment.anchor for fragment in fragments],
    ) for name in ("subject_binding", "object_binding", "predicate", "bridge")]
    return NS(
        claim=claim, context=context, verification_input=verification_input,
        decisions=decisions, quote=quote, anchor=anchor, proofs={}, refs=refs,
    )


def evaluate(case):
    return validate_source_assertion(
        case.claim, context=case.context, verification_input=case.verification_input,
        decisions=case.decisions, reference_proofs=case.proofs,
    )


def parallel_case():
    return relation_case(
        {"thermal": "热降解：60℃，10天。", "photo": "光降解：4500Lx，10天。"},
        {"thermal": ("thermal", "热降解"), "photo": ("photo", "光降解")},
        subject="thermal", objects=["photo"], subject_quote=[("photo", "光降解")],
        object_quotes={"photo": [("photo", "光降解")]},
        predicate_quotes=[("photo", "光降解：4500Lx，10天。")],
    )


def test_object_description_cannot_impersonate_thermal_subject_even_when_model_supports_it():
    result = evaluate(parallel_case())
    assert "source_assertion_subject_endpoint_unbound" in result.issues
    assert not result.steps


def test_adding_real_subject_from_parallel_record_does_not_close_contains_assertion():
    case = parallel_case()
    case.claim.payload.source_assertion.subject_support = [case.quote(("thermal", "热降解"))]
    result = evaluate(case)
    assert "source_assertion_endpoint_chain_not_closed" in result.issues


@pytest.mark.parametrize("names", [("父途径", "热降解", "光降解"), ("机组", "泵甲", "泵乙")])
def test_explicit_parent_with_two_children_is_not_blocked_by_type_or_vocabulary(names):
    parent, first, second = names
    text = f"{parent}包含{first}和{second}。"
    case = relation_case(
        {"source": text}, {"p": ("source", parent), "a": ("source", first),
                           "b": ("source", second)},
        subject="p", objects=["a", "b"], subject_quote=[("source", parent)],
        object_quotes={"a": [("source", first)], "b": [("source", second)]},
        predicate_quotes=[("source", text)],
    )
    result = evaluate(case)
    assert result.issues == []
    assert result.steps[-1].input_refs == [case.refs[key] for key in ("p", "a", "b")]


def test_anchor_overlap_does_not_bind_long_entity_to_short_subject():
    case = relation_case(
        {"source": "成品记录包含包装要求。"},
        {"p": ("source", "成品记录"), "o": ("source", "包装要求")},
        subject="p", objects=["o"], subject_quote=[("source", "成品")],
        object_quotes={"o": [("source", "包装要求")]},
        predicate_quotes=[("source", "成品记录包含包装要求。")],
    )
    assert "source_assertion_subject_endpoint_unbound" in evaluate(case).issues


def test_supported_predicate_facet_must_review_the_frozen_assertion():
    case = relation_case(
        {"source": "父项包含子项。"},
        {"p": ("source", "父项"), "o": ("source", "子项")},
        subject="p", objects=["o"], subject_quote=[("source", "父项")],
        object_quotes={"o": [("source", "子项")]},
        predicate_quotes=[("source", "父项包含子项。")],
    )
    case.decisions[2].support_refs = [case.anchor(("source", "子项"))]
    assert "source_assertion_predicate_not_reviewed" in evaluate(case).issues


def test_structural_roles_do_not_override_independent_predicate_rejection():
    case = relation_case(
        {"source": "热降解与光降解为并列试验。"},
        {"p": ("source", "热降解"), "o": ("source", "光降解")},
        subject="p", objects=["o"], subject_quote=[("source", "热降解")],
        object_quotes={"o": [("source", "光降解")]},
        predicate_quotes=[("source", "热降解与光降解为并列试验。")],
    )
    case.decisions[2].verdict = "unsupported"
    assert "source_assertion_predicate_not_reviewed" in evaluate(case).issues


def test_document_description_can_use_configured_root_without_fabricated_name():
    case = relation_case(
        {"title": "CMC报告", "photo": "光降解：4500Lx，10天。"},
        {"report": ("title", "CMC报告"), "photo": ("photo", "光降解")},
        subject="report", objects=["photo"], subject_quote=[],
        object_quotes={"photo": [("photo", "光降解")]},
        predicate_quotes=[("photo", "光降解：4500Lx，10天。")],
        bridge="document_subject_description", root="report",
    )
    assert evaluate(case).issues == []
    case.claim.payload.subject_id = "photo"
    assert "source_assertion_subject_missing" in evaluate(case).issues


@pytest.mark.parametrize("correct_row", [True, False])
def test_table_relation_requires_exact_owner_value_and_header_mapping(correct_row):
    case = relation_case(
        {"owner": "组装件甲", "other": "组装件乙", "header": "组成对象", "value": "零件乙"},
        {"p": ("owner", "组装件甲"), "o": ("value", "零件乙")},
        subject="p", objects=["o"], subject_quote=[("owner", "组装件甲")],
        object_quotes={"o": [("value", "零件乙")]},
        predicate_quotes=[("header", "组成对象")], bridge="role_mapped_table",
    )
    case.context.field_bindings = [FieldBinding(
        field_binding_id="row-1", record_view_ref="record-1", kind="table",
        owner_candidate_refs=[case.anchor(("owner", "组装件甲") if correct_row
                                          else ("other", "组装件乙"))],
        target_value_refs=[case.anchor(("value", "零件乙"))],
        label_refs=[case.anchor(("header", "组成对象"))],
    )]
    result = evaluate(case)
    assert (not result.issues) is correct_row
    if not correct_row:
        assert "source_assertion_endpoint_chain_not_closed" in result.issues


def reference_case():
    case = relation_case(
        {"first": "装置甲完成安装。", "next": "该装置包含零件乙。"},
        {"p": ("first", "装置甲"), "pronoun": ("next", "该装置"),
         "o": ("next", "零件乙")},
        subject="p", objects=["o"], subject_quote=[("next", "该装置")],
        object_quotes={"o": [("next", "零件乙")]},
        predicate_quotes=[("next", "该装置包含零件乙。")], bridge="resolved_reference_chain",
    )
    binding_ref = VersionedRef(id="reference-1", revision=1)
    binding = NS(
        target_kind="reference_binding", claim_ref=binding_ref, target_id="binding-target",
        content_hash="binding-content",
        scope=case.claim.scope, required_facets=["reference_identity", "reference_scope",
                                               "counterevidence"],
        payload=NS(local_id="binding", source_id="pronoun", target_id="p",
                   support=[case.quote(("first", "装置甲")), case.quote(("next", "该装置"))]),
    )
    case.verification_input.targets.append(binding)
    case.claim.dependency_refs.append(binding_ref)
    case.claim.payload.source_assertion.binding_ids = ["binding"]
    case.proofs[binding_ref.id, binding_ref.revision] = NS(
        policy_eligible=True, structural_valid=True, model_supported=True,
        independent_review="unreviewed",
        target=NS(claim_ref=binding_ref, target_id=binding.target_id,
                  literal_hash=binding.content_hash, context_hash="context",
                  source_scope_hash="scope",
                  document_context=case.context.target.document_context),
        decisions=[NS(verdict="supported", check_kind=name, decision_id=f"binding-{name}")
                   for name in binding.required_facets],
    )
    return case


def test_cross_sentence_pronoun_uses_independently_verified_binding():
    result = evaluate(reference_case())
    assert result.issues == []
    assert [step.step_kind for step in result.steps] == ["same_referent", "predicate_assertion"]


def test_reference_chain_names_the_identity_decision_regardless_of_serialization_order():
    case = reference_case()
    proof = next(iter(case.proofs.values()))
    proof.decisions.reverse()
    result = evaluate(case)
    assert result.issues == []
    assert result.steps[0].decision_ref.id == "binding-reference_identity"


@pytest.mark.parametrize("invalid", ["unverified", "revision", "scope", "missing_dependency",
                                     "hash", "context", "source_scope", "rejected"])
def test_reference_candidate_cannot_bypass_exact_proof_dependencies(invalid):
    case = reference_case()
    binding = case.verification_input.targets[-1]
    if invalid == "unverified":
        case.proofs.clear()
    elif invalid == "revision":
        case.proofs[binding.claim_ref.id, binding.claim_ref.revision].target.claim_ref = (
            binding.claim_ref.model_copy(update={"revision": 2})
        )
    elif invalid == "scope":
        binding.scope = NS(scope_id="another-scope")
    elif invalid == "missing_dependency":
        case.claim.dependency_refs.remove(binding.claim_ref)
    else:
        proof = case.proofs[binding.claim_ref.id, binding.claim_ref.revision]
        if invalid == "rejected":
            proof.independent_review = "rejected"
        else:
            field = {"hash": "literal_hash", "context": "context_hash",
                     "source_scope": "source_scope_hash"}[invalid]
            setattr(proof.target, field, "different")
    result = evaluate(case)
    assert "source_assertion_reference_not_verified" in result.issues
    assert "source_assertion_subject_endpoint_unbound" in result.issues
    assert not result.steps


def test_all_group_objects_need_exact_source_roles():
    case = parallel_case()
    case.claim.payload.object_ids.append("thermal")
    assert "source_assertion_object_set_mismatch" in evaluate(case).issues


def test_legacy_relationship_keeps_its_original_contract():
    case = parallel_case()
    case.claim.payload.source_assertion = None
    assert evaluate(case).issues == []


def binding_case():
    case = reference_case()
    prior = next(target for target in case.verification_input.targets
                 if target.target_kind == "entity" and target.claim_ref == case.refs["p"])
    case.verification_input.targets.remove(prior)
    case.verification_input.entity_dependencies = [NS(
        entity_ref=prior.claim_ref, proposal=prior.payload, class_iri=prior.payload.class_iri,
        grounding_kind="mention",
    )]
    claim = case.verification_input.targets[-1]
    claim.payload = ReferenceBindingProposal(
        local_id="binding", source_id="pronoun", target_id="p", binding_kind="anaphora",
        support=[case.quote(("first", "装置甲")), case.quote(("next", "该装置"))],
    )
    case.claim = claim
    case.decisions = [NS(
        check_kind=name, verdict="supported", support_refs=[fragment.anchor
                                                           for fragment in case.context.fragments],
    ) for name in ("reference_identity", "reference_scope", "counterevidence")]
    return case


def evaluate_binding(case):
    return validate_reference_binding(
        case.claim, context=case.context, verification_input=case.verification_input,
        decisions=case.decisions,
    )


def test_reference_binding_checks_both_source_mentions_and_independent_identity():
    assert evaluate_binding(binding_case()) == []


@pytest.mark.parametrize("invalid", ["type", "root", "no_prior", "new_entity_missing"])
def test_reference_binding_rejects_invalid_endpoint_identity(invalid):
    case = binding_case()
    prior = case.verification_input.entity_dependencies[0]
    if invalid == "type":
        prior.class_iri = "urn:generic:OtherGranularity"
    elif invalid == "root":
        prior.grounding_kind = "document_root"
    elif invalid == "no_prior":
        case.verification_input.entity_dependencies.clear()
    else:
        case.verification_input.targets = [case.claim]
    assert evaluate_binding(case)


@pytest.mark.parametrize("invalid", ["omitted_target", "identity_unreviewed", "scope_unresolved"])
def test_reference_binding_requires_positive_evidence_for_both_mentions(invalid):
    case = binding_case()
    if invalid == "omitted_target":
        case.claim.payload.support = [case.quote(("next", "该装置"))]
        expected = "reference_binding_target_unbound"
    elif invalid == "identity_unreviewed":
        case.decisions[0].support_refs = [case.anchor(("next", "该装置"))]
        expected = "reference_identity_support_not_reviewed"
    else:
        case.decisions[1].verdict = "undetermined"
        expected = "reference_scope_support_not_reviewed"
    assert expected in evaluate_binding(case)


def test_two_plausible_antecedents_cannot_both_rebind_one_source():
    case = binding_case()
    case.verification_input.targets.append(NS(
        target_kind="reference_binding",
        payload=case.claim.payload.model_copy(update={"local_id": "other", "target_id": "o"}),
    ))
    assert "reference_binding_conflicting_targets" in evaluate_binding(case)


def _gate(case, *, proof_class=None, property_verdict=None):
    source = case.claim.payload.source_assertion
    payload = RelationProposal(
        local_id="relation", subject_id=case.claim.payload.subject_id,
        predicate_iri=case.claim.payload.predicate_iri, object_ids=case.claim.payload.object_ids,
        selection="all", bridge_support=source.predicate_support, selection_support=[],
        qualifiers=dict(polarity="affirmed", modality="asserted", condition_support=[],
                        scope_qualifiers=[]),
        bridge_kind=case.claim.payload.bridge_kind, bridge_ref_ids=[],
        source_assertion=dict(
            subject_support=source.subject_support,
            object_support=[dict(object_id=item.object_id, support=item.support)
                            for item in source.object_support],
            predicate_support=source.predicate_support, binding_ids=source.binding_ids,
        ),
    )
    required = ["subject_binding", "object_binding", "predicate", "bridge", "qualifiers",
                "counterevidence"]
    kind = "relation"
    if property_verdict is not None:
        kind = "property"
        payload = PropertyProposal(
            local_id="property", subject_id=payload.subject_id,
            predicate_iri=payload.predicate_iri,
            value_quote=source.object_support[0].support[0],
            field_support=source.predicate_support, unit_support=[],
            qualifiers=payload.qualifiers, bridge_kind="explicit_assertion", bridge_ref_ids=[],
        )
        required = ["subject_binding", "field_role", "predicate", "bridge", "value",
                    "qualifiers", "counterevidence"]
    claim_ref = VersionedRef(id="relation", revision=1)
    claim = VerificationTargetSpec(
        target_id="target-relation", target_kind=kind, claim_ref=claim_ref,
        content_hash=claim_content_hash(kind, payload, case.claim.scope,
                                        case.claim.dependency_refs),
        required_facets=required, payload=payload, scope=case.claim.scope,
        dependency_refs=case.claim.dependency_refs,
    )
    subject = case.refs[payload.subject_id]
    case.context.target = VerificationTarget.create(
        run_fingerprint="run", claim_ref=claim_ref, task_id="task",
        check_kind="predicate_entailment",
        document_context=DocumentContext(
            document_hash="a" * 64, document_class_iri="urn:Document",
            root_ref=case.context.target.document_context.root_ref,
        ),
        subject_ref=SubjectRef(entity_id=subject.id, revision=subject.revision,
                               class_iri="urn:generic:Object"),
        ontology_hash="ontology", source_scope_hash="scope", context_hash="context",
    )
    case.context.proof_dependencies = []
    decisions = [SemanticDecision(
        decision_id=f"decision-{name}", target_id=claim.target_id, check_kind=name,
        verdict=property_verdict if name == "subject_binding" and property_verdict else "supported",
        reason_code="verified", reason="独立核验当前原文维度。",
        support_refs=[fragment.anchor for fragment in case.context.fragments],
        searched_context_refs=[fragment.anchor for fragment in case.context.fragments],
        verifier_version="test", attempt_id="test-attempt",
    ) for name in required]
    proofs = {}
    for key, ref in case.refs.items():
        type_ref = VersionedRef(id=f"type-{key}", revision=1)
        referent_ref = VersionedRef(id=f"referent-{key}", revision=1)
        proofs[ref.id, ref.revision] = GraphNode(
            entity_id=ref.id, revision=ref.revision, class_iri=proof_class or "urn:generic:Object",
            class_label="对象", label=key, referent_ref=referent_ref,
            type_decision_ref=type_ref, referent_decision_ref=referent_ref,
            dependency_refs=[type_ref, referent_ref],
        )
    card = NS(schema_card_id="card", quantity_policies=[], identity_keys=[],
              unsupported_constraints=[])
    return ProofGate().evaluate_frozen_claim(
        claim, decisions, entity_proofs=proofs,
        proof_menu=build_generic_proof_menu(card, case.context, required),
        checks=ClaimCheckResult(checks={"binding": True, "relation_graph": True,
                                       "metric": True, "shacl": True}),
        context=case.context, verification_input=case.verification_input,
    )


def test_actual_proof_gate_rejects_wrong_source_roles_despite_all_supported_facets():
    result = _gate(parallel_case())
    assert result.model_supported
    assert not result.structural_valid
    assert not result.policy_eligible
    assert "source_assertion_subject_endpoint_unbound" in result.validation_issues


def test_actual_proof_gate_preserves_explicit_parent_child_relation():
    case = relation_case(
        {"source": "父项包含子项。"},
        {"p": ("source", "父项"), "o": ("source", "子项")},
        subject="p", objects=["o"], subject_quote=[("source", "父项")],
        object_quotes={"o": [("source", "子项")]},
        predicate_quotes=[("source", "父项包含子项。")],
    )
    result = _gate(case)
    assert result.policy_eligible, result.validation_issues
    assert result.predicate_evidence.bridge_steps[0].step_kind == "predicate_assertion"


def test_type_proof_cannot_be_reused_with_a_different_declared_endpoint_class():
    result = _gate(parallel_case(), proof_class="urn:OtherType")
    assert "entity_dependency_not_verified" in result.validation_issues
    assert not result.policy_eligible


def test_irrelevant_entity_candidate_does_not_contaminate_source_role_resolution():
    case = relation_case(
        {"source": "父项包含子项。"},
        {"p": ("source", "父项"), "o": ("source", "子项")},
        subject="p", objects=["o"], subject_quote=[("source", "父项")],
        object_quotes={"o": [("source", "子项")]},
        predicate_quotes=[("source", "父项包含子项。")],
    )
    case.verification_input.targets.append(NS(
        target_kind="entity", claim_ref=VersionedRef(id="irrelevant", revision=1),
        payload=EntityProposal(
            local_id="irrelevant", class_iri="urn:generic:Object", representation="mention",
            mentions=[Quote(evidence_id="not-selected", text="irrelevant", context_text=None)],
            record_components=[], identifier_claims=[],
        ),
    ))
    assert evaluate(case).issues == []


@pytest.mark.parametrize("verdict", ["unsupported", "undetermined"])
def test_unresolved_pronoun_cannot_supply_property_owner_despite_mechanical_checks(verdict):
    text = "装置甲与装置乙分别运行。其温度为20℃。"
    case = relation_case(
        {"source": text}, {"p": ("source", "装置甲"), "value": ("source", "20")},
        subject="p", objects=["value"], subject_quote=[("source", "装置甲")],
        object_quotes={"value": [("source", "20")]}, predicate_quotes=[("source", text)],
    )
    case.claim.payload.predicate_iri = "urn:temperature"
    result = _gate(case, property_verdict=verdict)
    assert not result.policy_eligible
    assert "subject_binding_not_supported" in result.validation_issues
