"""Compact the model's read view after authorization, without changing proof objects."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from app.services.extraction.evidence_identity import canonical_json

MODEL_CONTEXT_INSTRUCTIONS = (
    "source_catalog按section_ref读取顶层shared_context.source_sections的标题和摘要；"
    "它们仅用于定位，不能作为事实证据或扩大本成员授权。"
    "schema_card未重复subject_ref时使用本任务subject_ref；"
    "关系range_classes列出完整允许范围，未另列range_class_iris时从各项iri读取。"
    "registered_refs未另列时，登记引用见本成员registered_entities的entity_ref。"
    "verification_input.entity_dependency_refs和reference_dependency_refs按完整id/revision"
    "分别引用本成员registered_entities.entity_ref和reference_dependencies.binding_ref；"
    "与核验输入内仍完整列出的依赖共同构成本次核验依赖，不能引用其他成员。"
    "核验阶段schema_card仅展示冻结targets及其依赖涉及的类型、谓词和身份键约束；"
    "核验实体类型时保留该类型完整属性定义及约束供判别和反证。"
    "不表示被省略的本体定义无效，也不能新增声明。"
    "class_cards.properties中的property_ref读取本成员schema_card.property_definitions"
    "中的完整属性定义；仅该类型明确列出的属性适用，不继承其他类型或成员的属性。"
)


def _schema_references(value, classes: set[str], predicates: set[str]) -> None:
    """Read the validated verifier's semantic fields, including bridge/scope dependencies."""
    if isinstance(value, list):
        for item in value:
            _schema_references(item, classes, predicates)
    elif isinstance(value, dict):
        if isinstance(value.get("class_iri"), str):
            classes.add(value["class_iri"])
        if isinstance(value.get("predicate_iri"), str):
            predicates.add(value["predicate_iri"])
        for item in value.values():
            if isinstance(item, (dict, list)):
                _schema_references(item, classes, predicates)


def _property_definition(card: dict, value: dict) -> dict:
    return card.get("property_definitions", {}).get(value.get("property_ref"), value)


def _narrow_predicates(card: dict, owner: dict, field: str, predicates: set[str]) -> None:
    selected = set(predicates)
    # Complete composite identity keys remain meaningful even when only one key
    # component was asserted. Never rewrite namespace/scope/declaration metadata.
    selected.update(iri for key in owner.get("identity_keys", [])
                    for iri in key.get("property_iris", []))
    definitions = [_property_definition(card, item) for item in owner.get(field, [])]
    declared = {item.get("iri") for item in definitions}
    owner[field] = [item for item in owner.get(field, [])
                    if _property_definition(card, item).get("iri") in selected]
    for name in ("quantity_policies", "unsupported_constraints"):
        if name in owner:
            owner[name] = [item for item in owner[name]
                           if item.get("predicate_iri") in selected
                           or item.get("predicate_iri") not in declared]


def _record_owners_known(verification: dict, dependencies: list[dict]) -> bool:
    entities = {target["payload"].get("local_id"): target["payload"].get("class_iri")
                for target in verification["targets"] if target.get("target_kind") == "entity"}
    local_refs = verification.get("local_ref_map", {})
    for target in verification["targets"]:
        if target.get("target_kind") == "entity":
            continue
        payload = target.get("payload", {})
        subject = payload.get("source_id" if target.get("target_kind") == "reference_binding"
                              else "subject_id")
        if subject is not None and entities.get(subject):
            continue
        reference = local_refs.get(subject)
        matches = [item for item in dependencies if (
            item.get("entity_ref") == reference if reference is not None
            else subject is not None and item.get("entity_ref", {}).get("id") == subject
        )]
        if not matches or any(not item.get("class_iri") for item in matches):
            return False
    return True


def _narrow_verification_schema(member: dict, card: dict) -> None:
    verification = member.get("verification_input")
    if (member.get("stage") != "verification" or not isinstance(verification, dict)
            or not verification.get("targets")):
        return
    classes, predicates = set(), set()
    _schema_references(verification, classes, predicates)
    # Dependencies may already use member-local references after a first projection.
    entity_refs = verification.get("entity_dependency_refs", [])
    reference_refs = verification.get("reference_dependency_refs", [])
    dependencies = [*verification.get("entity_dependencies", []), *[
        item for item in member.get("registered_entities", [])
        if item.get("entity_ref") in entity_refs
    ]]
    _schema_references([
        *dependencies,
        *[item for item in member.get("reference_dependencies", [])
          if item.get("binding_ref") in reference_refs],
    ], classes, predicates)
    entity_classes = {target["payload"].get("class_iri") for target in verification["targets"]
                      if target.get("target_kind") == "entity"}
    if "class_cards" in card:
        selected = [item for item in card["class_cards"] if item.get("class_iri") in classes]
        # Partial owner resolution must not let one known target erase another's card.
        if not selected or not _record_owners_known(verification, dependencies):
            return
        card["class_cards"] = selected
        for owner in selected:
            if owner.get("class_iri") not in entity_classes:
                _narrow_predicates(card, owner, "properties", predicates)
        definitions = card.get("property_definitions")
        if definitions is not None:
            refs = {item["property_ref"] for owner in selected
                    for item in owner.get("properties", []) if "property_ref" in item}
            card["property_definitions"] = {ref: value for ref, value in definitions.items()
                                            if ref in refs}
    else:
        # Entity records carry field/value quotes without predicate IRIs. Their
        # type/role verification needs the full card, including negative constraints.
        if entity_classes:
            return
        # Range descriptions define allowed dependency types. Retain the whole
        # relation and its full range instead of selecting only its observed type.
        predicates.update(item["iri"] for item in card.get("predicates", [])
                          if classes.intersection(item.get("range_class_iris", []))
                          or classes.intersection(
                              row.get("iri") for row in item.get("range_classes", [])))
        _narrow_predicates(card, card, "predicates", predicates)


def _share_record_properties(card: dict) -> None:
    owners = card.get("class_cards", [])
    properties = [item for owner in owners for item in owner.get("properties", [])
                  if "property_ref" not in item]
    counts = Counter(canonical_json(item) for item in properties)
    definitions = card.get("property_definitions", {})
    references = {canonical_json(value): ref for ref, value in definitions.items()}
    for owner in owners:
        for position, item in enumerate(owner.get("properties", [])):
            if "property_ref" in item:
                continue
            key = canonical_json(item)
            if counts[key] < 2:
                continue
            ref = references.get(key)
            if ref is None:
                ref = f"property_{len(definitions) + 1}"
                definitions[ref] = item
                references[key] = ref
            owner["properties"][position] = {"property_ref": ref}
    if definitions:
        card["property_definitions"] = definitions


def _compact_schema(member: dict) -> None:
    card = member.get("schema_card")
    if not isinstance(card, dict):
        return
    _narrow_verification_schema(member, card)
    _share_record_properties(card)
    if ("subject_ref" in member and card.get("subject_ref") == member["subject_ref"]):
        card.pop("subject_ref", None)
    for predicate in card.get("predicates", []):
        classes = predicate.get("range_classes")
        iris = predicate.get("range_class_iris")
        # Do not narrow an incomplete range description or reinterpret OWL constraints.
        if (isinstance(classes, list) and isinstance(iris, list)
                and iris == [item.get("iri") for item in classes]):
            predicate.pop("range_class_iris")


def _compact_dependencies(member: dict) -> None:
    entities = member.get("registered_entities", [])
    registered = member.get("registered_refs")
    if registered is not None and registered == [e["entity_ref"] for e in entities]:
        member.pop("registered_refs")
    verification = member.get("verification_input")
    if not isinstance(verification, dict):
        return
    for field, source, identity, ref_field in (
        ("entity_dependencies", entities, "entity_ref", "entity_dependency_refs"),
        ("reference_dependencies", member.get("reference_dependencies", []),
         "binding_ref", "reference_dependency_refs"),
    ):
        if field not in verification:
            continue
        refs, remaining = [], []
        for dependency in verification[field]:
            # Matching an ID/revision alone is insufficient: evidence and scope must match too.
            if identity in dependency and dependency in source:
                refs.append(deepcopy(dependency[identity]))
            else:
                remaining.append(dependency)
        if refs:
            verification[ref_field] = refs
            if remaining:
                verification[field] = remaining
            else:
                verification.pop(field)


def compact_model_context(value: dict) -> dict:
    """Project a scalar, record or batch request; preserve member-local source permissions.

    Local section references share descriptions, never records, evidence, or authority.
    This function is called only when constructing new stage input; saved requests and
    server-side SchemaCard/VerificationInput objects are not rewritten.
    """
    result = deepcopy(value)
    shared = result.setdefault("shared_context", {})
    sections = shared.setdefault("source_sections", {})
    section_keys = {canonical_json(description): ref for ref, description in sections.items()}
    members = result.get("members", [result])
    for member in members:
        for entry in member.get("source_catalog", []):
            if "section_ref" in entry:
                continue
            description = {key: entry.get(key) for key in (
                "section_id", "title", "summary", "summary_source",
            )}
            key = canonical_json(description)
            ref = section_keys.get(key)
            if ref is None:
                ref = f"section_{len(sections) + 1}"
                sections[ref] = description
                section_keys[key] = ref
            for name in description:
                entry.pop(name, None)
            entry["section_ref"] = ref
        _compact_schema(member)
        _compact_dependencies(member)
    if not sections:
        shared.pop("source_sections")
    if not shared:
        result.pop("shared_context")
    return result
