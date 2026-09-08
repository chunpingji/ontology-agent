"""Resolve technical dependencies without choosing missing business semantics."""

from app.services.extraction.evidence_identity import evidence_hash
from app.services.ontology_model_context import ModelContext
from app.services.reasoning.rule_service import check_plan
from app.services.reporting.template_v2 import CalculationCheck, TemplateV2

STYLE_REF = "urn:report:style:standard:1"
POLICY_REF = "urn:report:policy:qa-review:1"


def profile(ref, kind, title, definition):
    return {
        "contract_id": ref,
        "family_id": title,
        "kind": kind,
        "revision_no": 1,
        "status": "published",
        "is_disabled": False,
        "definition": definition,
        "definition_hash": evidence_hash(definition),
        "origin": "system_profile",
        "decision_refs": [],
    }


def builtin_profile(ref):
    if ref == STYLE_REF:
        return profile(
            ref, "style", "标准报告版式", {"font": "Arial", "font_size_pt": 11, "assets": []}
        )
    if ref == POLICY_REF:
        return profile(
            ref,
            "policy",
            "QA 审核流程",
            {
                "require_review": True,
                "review_roles": ["qa"],
                "signature_slots": [],
                "workflow_contract_ref": None,
                "require_ready_for_submission": True,
            },
        )
    return None


def template_style(template):
    if template.style is None:
        return None
    definition = template.style.model_dump(mode="json")
    return profile(
        "urn:template-style:" + evidence_hash(definition), "style", "模板版式", definition
    )


def prepare_template(db, schema, *, classes=None, document_class=None):
    template = TemplateV2.model_validate(schema).model_copy(deep=True)
    if document_class:
        documents = [s for s in template.source_slots if s.kind == "document"]
        if len(documents) == 1 and documents[0].class_iri != document_class:
            from app.services.reporting.template_v2 import ReportingError

            raise ReportingError(
                "DOCUMENT_TYPE_SOURCE_MISMATCH",
                "关联文档类型与主要来源不一致，请在新修订中统一修改",
            )
        if not template.source_slots:
            from app.services.reporting.template_v2 import SourceSlot

            template.source_slots = [
                SourceSlot(source_slot_id="source", kind="document", class_iri=document_class)
            ]
    old_release = template.ontology_release_ref
    if old_release in {"auto:ontology", "unresolved:ontology"}:
        model = ModelContext(db, classes).resolve(old_release)
        template.ontology_release_ref = model["contract_id"]
    for binding in template.definitions.bindings.values():
        if binding.kind == "facts" and binding.contract_ref.release_ref in {
            old_release,
            "unresolved:ontology",
            "auto:ontology",
        }:
            binding.contract_ref.release_ref = "template.ontology_release_ref"
    for binding in template.definitions.bindings.values():
        if binding.kind != "facts" or binding.scope.root.kind != "source_root":
            continue
        slot = next(
            (s for s in template.source_slots if s.source_slot_id == binding.scope.source_slot),
            None,
        )
        if slot and binding.contract_ref.root_class_iri == slot.class_iri:
            binding.contract_ref.root_class_iri = "auto:class"
            if (
                not binding.scope.predicate_path
                and binding.contract_ref.result_class_iri == slot.class_iri
            ):
                binding.contract_ref.result_class_iri = "auto:class"
    style = template_style(template)
    if style:
        template.style_profile_ref = style["contract_id"]
    elif template.style_profile_ref in {"auto:style", "unresolved:style"}:
        template.style_profile_ref = STYLE_REF
    if template.publication_policy_ref in {"auto:policy", "unresolved:policy"}:
        template.publication_policy_ref = POLICY_REF
    template.calculation_checks = [
        CalculationCheck.model_validate(c)
        for c in check_plan(
            [s.model_dump(mode="json") for s in template.source_slots],
            [c.model_dump(mode="json") for c in template.calculation_checks],
        )
    ]
    return template
