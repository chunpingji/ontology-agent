"""Targets retain missing slots and do not upgrade weak evidence to facts."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.schemas.document_analysis import GraphArtifactResponse
from app.services.document_analysis.target_graph import project_target_graph
from app.services.extraction.ontology_guided.contracts import (
    EdgeSpec,
    OntologyClassDefinition,
    OntologySnapshot,
    SlotSpec,
)
from app.services.extraction.ontology_guided.executor import OntologyGuidedExecutor

ROOT = "https://example.org/Report"
PRODUCT = "https://example.org/Product"
RELATION = "https://example.org/describes"
PROPERTY = "https://example.org/number"
RUN_ID = str(uuid4())
EMPTY_SCOPE = {"scope_id": "0" * 64, "members": []}


def ref(name):
    return {"id": name, "revision": 1}


def entity(name, class_iri=PRODUCT):
    return {
        "entity_id": name, "revision": 1, "class_iri": class_iri,
        "class_label": "报告" if class_iri == ROOT else "产品", "label": name,
        "seed_origin": "user_selected" if name == "root" else "recognized",
        "grounding_kind": "document_root" if name == "root" else "mention",
        "type_decision_ref": ref(f"type:{name}"),
        "referent_decision_ref": ref(f"referent:{name}"),
        "source_selection_refs": [f"source:{name}"],
    }


def relationship(name="edge", **overrides):
    return {
        "candidate_id": name, "revision": 1,
        "subject_ref": {"entity_id": "root", "revision": 1},
        "object_ref": {"entity_id": "product", "revision": 1},
        "predicate_iri": RELATION, "predicate_label": "描述",
        "structural_valid": True, "model_supported": True, "policy_eligible": True,
        "proof_ref": ref(f"proof:{name}"), "decision_refs": [ref(f"decision:{name}")],
        "source_selection_refs": {"predicate_bridge": [f"source:{name}"]},
        "modality": "asserted", "scope": EMPTY_SCOPE, **overrides,
    }


def coverage(subject="root", predicate=RELATION, **overrides):
    return {
        "subject_ref": {"entity_id": subject, "revision": 1}, "predicate_iri": predicate,
        "predicate_label": "字段", "records_planned": 2, "records_examined": 2,
        "records_incomplete": 0, "records_unattempted": 0, "scope": EMPTY_SCOPE,
        **overrides,
    }


def payload(*, pending=False):
    return {
        "recognition_run_id": RUN_ID, "run_revision": 1, "event_head": 1,
        "artifact_revision": 1, "availability": "pending" if pending else "ready",
        "projection": "all_candidates", "extraction_protocol": "ontology-tool-extraction-v1",
        "graph_snapshot": None if pending else {
            "snapshot_id": "graph", "analysis_id": "analysis", "metadata_snapshot_id": "meta",
            "ontology_snapshot_id": "onto", "root_ref": {"entity_id": "root", "revision": 1},
            "projection_policy": "test", "generated_at": "2026-09-21T00:00:00Z",
        }, "entities": [] if pending else [entity("root", ROOT), entity("product")],
    }


def ontology(multiplicity="multiple"):
    return OntologySnapshot(
        snapshot_id="onto", ontology_hash="test", created_from="frozen_fixture", classes={
            ROOT: OntologyClassDefinition(
                iri=ROOT, label="报告", source_hash="test", declared_relationships=[EdgeSpec(
                    iri=RELATION, label="描述", range_class_iris=[PRODUCT],
                    multiplicity=multiplicity,
                )], declared_properties=[
                    SlotSpec(iri=PROPERTY, label="编号", multiplicity="single"),
                ],
            ), PRODUCT: OntologyClassDefinition(
                iri=PRODUCT, label="产品", source_hash="test",
                declared_properties=[SlotSpec(iri=PROPERTY, label="编号", multiplicity="single")],
                declared_relationships=[EdgeSpec(
                    iri=RELATION, label="关联产品", range_class_iris=[PRODUCT],
                )],
            ),
        },
    )


def project(data, *, frozen=None, phase="evidence_verification"):
    return project_target_graph(
        graph=GraphArtifactResponse.model_validate(data), ontology=frozen or ontology(),
        document_hash="a" * 64, root_class_iri=ROOT, root_class_label="报告", filename="CMC.docx",
        phase=phase,
    )


def root_relation(result):
    return next(item for item in result.targets
                if item.kind == "relationship" and item.subject_class_iri == ROOT)


def test_pending_graph_has_all_root_targets_but_no_virtual_facts():
    result = project(payload(pending=True))
    execution_root = OntologyGuidedExecutor.root_node(
        recognition_run_id=RUN_ID, document_hash="a" * 64, root_class_iri=ROOT,
        root_class_label="报告", filename="CMC.docx",
    )
    assert result.root.entity_id == execution_root.entity_id
    assert len(result.targets) == 2
    assert result.graph.entities == result.graph.relationships == []
    assert result.summary.relationships.total == result.summary.properties.total == 1
    assert result.summary.relationships.percent == 0
    assert all(item.state == "pending" for item in result.targets)


@pytest.mark.parametrize("iri", [
    "https://ontology.pharma-gmp.cn/slpra/document/contentHash", "urn:custom:contentHash",
])
def test_property_targets_do_not_hide_a_hardcoded_ontology_iri(iri):
    frozen = ontology()
    frozen.classes[ROOT].declared_properties.append(SlotSpec(iri=iri, label="内容哈希"))
    result = project(payload(pending=True), frozen=frozen)
    assert iri in {item.predicate_iri for item in result.targets}
    assert result.summary.properties.total == 2


def test_unknown_multivalue_is_partial_even_after_all_candidate_records_checked():
    data = payload()
    data["relationships"] = [relationship()]
    data["coverage"] = {"subjects": [coverage()]}
    result = project(data)
    target = root_relation(result)
    assert target.supported_count == 1 and target.state == "partial"
    assert target.coverage.scope_checked and not target.completed
    assert result.summary.relationships.supported == 1
    assert result.summary.relationships.completed == 0
    assert len(result.targets) == 4  # Root and reached product, including missing properties.


def test_single_value_requires_proof_and_scope_coverage_for_completion():
    data = payload()
    data["relationships"] = [relationship()]
    assert not root_relation(project(data, frozen=ontology("single"))).completed
    data["coverage"] = {"subjects": [coverage()]}
    assert root_relation(project(data, frozen=ontology("single"))).completed
    data["coverage"] = {"subjects": [coverage(records_examined=1, records_unattempted=1)]}
    assert not root_relation(project(data, frozen=ontology("single"))).completed


@pytest.mark.parametrize("change", [
    {"proof_ref": None}, {"decision_refs": []}, {"structural_valid": False},
    {"policy_eligible": False}, {"model_supported": False}, {"invalidated": True},
    {"independent_review": "rejected"}, {"source_selection_refs": {}},
    {"object_ref": {"entity_id": "product", "revision": 2}},
    {"scope": None}, {"modality": None},
])
def test_ineligible_candidates_never_become_solid_or_expand_descendants(change):
    data = payload()
    data["relationships"] = [relationship(**change)]
    result = project(data)
    target = root_relation(result)
    assert target.supported_count == 0 and target.supported_assertion_refs == []
    assert result.summary.relationships.total == 1
    assert result.summary.properties.total == 2  # Verified isolated entity's property remains.


@pytest.mark.parametrize("invalid_ref", ["proof:edge", "decision:edge", "type:product", "edge"])
def test_exact_invalidated_proof_and_entity_references_are_not_accepted(invalid_ref):
    data = payload()
    data.update(relationships=[relationship()], invalidated_refs=[ref(invalid_ref)])
    assert root_relation(project(data)).supported_count == 0


def test_negation_and_qualified_positive_preserve_distinct_semantics():
    data = payload()
    data["relationships"] = [relationship(polarity="negated")]
    result = project(data)
    target = root_relation(result)
    assert target.state == "negated" and target.negated_count == 1
    assert target.supported_count == 0 and len(target.supported_assertion_refs) == 1
    assert result.summary.relationships.total == 1
    data["relationships"] = [relationship(
        polarity="conditional", modality="planned", conditions=[{"text": "仅加热时"}],
    )]
    result = project(data)
    assert root_relation(result).supported_count == 1
    assert result.graph.relationships[0].conditions == [{"text": "仅加热时"}]
    assert result.graph.relationships[0].modality == "planned"


def test_duplicate_proofs_do_not_inflate_member_count_or_single_value_completion():
    data = payload()
    data["relationships"] = [relationship("edge1"), relationship("edge2")]
    data["coverage"] = {"subjects": [coverage()]}
    assert root_relation(project(data, frozen=ontology("single"))).supported_count == 1
    data["entities"].append(entity("other"))
    data["relationships"].append(relationship(
        "edge3", object_ref={"entity_id": "other", "revision": 1},
    ))
    target = root_relation(project(data, frozen=ontology("single")))
    assert target.supported_count == 2 and not target.completed


def test_empty_searched_slot_is_missing_not_negative_and_denominator_is_retained():
    data = payload()
    data["coverage"] = {"subjects": [coverage()]}
    result = project(data)
    target = root_relation(result)
    assert target.state == "not_found" and target.negated_count == 0
    assert result.summary.relationships.total == 1 and result.summary.relationships.percent == 0


def test_false_boolean_property_counts_as_evidence_instead_of_missing():
    data = payload()
    prop = relationship("prop")
    del prop["object_ref"]
    prop.update(predicate_iri=PROPERTY, raw_value="否", normalized_value=False)
    data["properties"] = [prop]
    data["coverage"] = {"subjects": [coverage(predicate=PROPERTY)]}
    result = project(data)
    target = next(item for item in result.targets
                  if item.kind == "property" and item.subject_class_iri == ROOT)
    assert target.supported_count == 1 and target.completed


def test_unverified_entity_does_not_create_targets_or_complete_a_relationship():
    data = payload()
    data["entities"][1]["type_decision_ref"] = None
    data["relationships"] = [relationship()]
    result = project(data)
    assert len(result.targets) == 2
    assert root_relation(result).supported_count == 0


def test_scoped_evidence_needs_exact_proven_parent_and_cannot_borrow_other_coverage():
    data = payload()
    scope = {"scope_id": "1" * 64, "members": [{
        "relation_ref": ref("parent"), "member_ref": {"entity_id": "product", "revision": 1},
    }]}
    scoped = relationship("scoped", scope=scope,
                          subject_ref={"entity_id": "product", "revision": 1})
    data["relationships"] = [relationship("parent"), scoped]
    data["coverage"] = {"subjects": [coverage("product")]}
    result = project(data)
    scoped_target = next(item for item in result.targets if item.scope_id == scope["scope_id"])
    assert scoped_target.supported_count == 0 and not scoped_target.coverage.scope_checked
    data["scope_resolutions"] = [{"scope_id": scope["scope_id"], "steps": [{
        **scope["members"][0], "selection": None, "polarity": "affirmed",
        "modality": "asserted", "conditions": [], "applicability": [],
        "evidence_selection_ids": ["source:parent"],
    }]}]
    result = project(data)
    scoped_target = next(item for item in result.targets if item.scope_id == scope["scope_id"])
    assert scoped_target.supported_count == 1 and not scoped_target.coverage.scope_checked
    changed = deepcopy(data)
    changed["relationships"][0]["revision"] = 2
    result = project(changed)
    assert next(item for item in result.targets
                if item.scope_id == scope["scope_id"]).supported_count == 0


def test_zero_targets_is_unscored_not_one_hundred_percent():
    frozen = ontology()
    for item in frozen.classes.values():
        item.declared_properties = []
        item.declared_relationships = []
    result = project(payload(pending=True), frozen=frozen)
    assert result.summary.relationships.percent is None
    assert result.summary.properties.percent is None


def test_pending_attribute_mapping_prevents_single_property_completion():
    data = payload()
    prop = relationship("prop")
    del prop["object_ref"]
    prop.update(predicate_iri=PROPERTY, raw_value="A-1")
    data["properties"] = [prop]
    data["coverage"] = {"subjects": [coverage(predicate=PROPERTY)]}
    data["attribute_candidates"] = [{
        "candidate_id": "unmapped", "record_id": "record", "field_label": "编号",
        "raw_value": "A-2", "status": "pending", "source_selection_refs": {},
        "options": [{"subject_ref": ref("root"), "class_iri": ROOT,
                     "predicate_iri": PROPERTY}],
    }]
    result = project(data)
    target = next(item for item in result.targets
                  if item.kind == "property" and item.subject_class_iri == ROOT)
    assert target.supported_count == 1 and not target.completed and target.state == "partial"
    data["properties"] = []
    result = project(data)
    target = next(item for item in result.targets
                  if item.kind == "property" and item.subject_class_iri == ROOT)
    assert target.state == "undetermined"


def test_alternative_group_is_not_counted_as_two_affirmed_members_or_a_complete_single():
    data = payload()
    data["entities"].append(entity("other"))
    group = relationship("group")
    del group["object_ref"]
    group.update(object_refs=[{"entity_id": name, "revision": 1}
                              for name in ("product", "other")], selection="alternatives")
    data["relationship_groups"] = [group]
    data["coverage"] = {"subjects": [coverage()]}
    result = project(data, frozen=ontology("single"))
    target = root_relation(result)
    assert target.supported_count == 1 and not target.completed
    assert target.supported_assertion_refs[0].id == "group"
    assert result.graph.relationship_groups[0].selection == "alternatives"


@pytest.mark.parametrize("parent_change", [
    {"polarity": "negated"}, {"polarity": "conditional"}, {"modality": "unspecified"},
])
def test_ineligible_parent_cannot_grant_child_property_proof_or_expand_relations(parent_change):
    data = payload()
    scope = {"scope_id": "2" * 64, "members": [{
        "relation_ref": ref("parent"), "member_ref": {"entity_id": "product", "revision": 1},
    }]}
    parent = relationship("parent", **parent_change)
    prop = relationship("scoped-property", scope=scope,
                        subject_ref={"entity_id": "product", "revision": 1})
    del prop["object_ref"]
    prop.update(predicate_iri=PROPERTY, raw_value="A-1")
    data["relationships"] = [parent]
    data["properties"] = [prop]
    data["scope_resolutions"] = [{"scope_id": scope["scope_id"], "steps": [{
        **scope["members"][0], "selection": None,
        "polarity": parent.get("polarity", "affirmed"), "modality": parent["modality"],
        "conditions": [], "applicability": [], "evidence_selection_ids": ["source:parent"],
    }]}]
    result = project(data)
    child = next(item for item in result.targets if item.scope_id == scope["scope_id"])
    assert child.supported_count == 0 and child.supported_assertion_refs == []
    assert result.summary.relationships.total == 1


def test_inconsistent_record_counts_cannot_enter_target_projection():
    data = payload()
    data["coverage"] = {"subjects": [coverage(records_examined=0)]}
    with pytest.raises(ValueError, match="coverage subject record counts must be conserved"):
        project(data)


def test_candidate_phase_expands_isolated_entities_without_relationship_proof():
    data = payload()
    data["entities"].append(entity("isolated"))
    data["relationships"] = [relationship(
        "candidate", subject_ref={"entity_id": "isolated", "revision": 1},
        proof_ref=None, decision_refs=[], model_supported=False, policy_eligible=False,
    )]
    result = project(data, phase="candidate_graph")
    assert result.phase == "candidate_graph"
    assert len(result.targets) == 3
    assert all(item.kind == "relationship" for item in result.targets)
    isolated = next(item for item in result.targets if item.subject_ref.entity_id == "isolated")
    assert [item.id for item in isolated.assertion_refs] == ["candidate"]
    assert isolated.state == "undetermined"
    assert result.graph.relationships[0].proof_ref is None
    assert result.graph.relationships[0].source_selection_refs.predicate_bridge == [
        "source:candidate",
    ]
    assert result.graph.entities[-1].source_selection_refs == ["source:isolated"]
    assert all(not item.completed and item.supported_count == 0
               and not item.supported_assertion_refs for item in result.targets)
    assert result.summary.relationships.percent is None
    assert result.summary.properties.total == 0


def test_candidate_phase_never_upgrades_existing_proofs_or_invents_edges():
    data = payload()
    data["relationships"] = [relationship()]
    data["coverage"] = {"subjects": [coverage()]}
    prop = relationship("prop")
    del prop["object_ref"]
    prop.update(predicate_iri=PROPERTY, raw_value="A-1")
    data["properties"] = [prop]
    result = project(data, frozen=ontology("single"), phase="candidate_graph")
    target = root_relation(result)
    assert [item.model_dump() for item in target.assertion_refs] == [ref("edge")]
    assert not target.supported_assertion_refs and not target.completed
    assert target.supported_count == target.negated_count == 0
    assert not target.coverage.scope_checked
    assert all(item.kind == "relationship" for item in result.targets)
    assert result.summary.relationships.supported == result.summary.relationships.completed == 0
    assert result.summary.relationships.percent is None
    assert len(result.graph.relationships) == 1


def test_candidate_group_keeps_unverified_alternatives_and_source_references():
    data = payload()
    data["entities"].append(entity("other"))
    group = relationship("group", proof_ref=None, decision_refs=[], policy_eligible=False)
    del group["object_ref"]
    group.update(object_refs=[{"entity_id": name, "revision": 1}
                              for name in ("product", "other")], selection="alternatives")
    data["relationship_groups"] = [group]
    result = project(data, phase="candidate_graph")
    assert [item.model_dump() for item in root_relation(result).assertion_refs] == [ref("group")]
    assert root_relation(result).supported_assertion_refs == []
    assert result.graph.relationship_groups[0].selection == "alternatives"
    assert len(result.graph.relationship_groups[0].object_refs) == 2


def test_candidate_phase_keeps_registered_entities_without_incoming_edges():
    data = payload()
    data["entities"][1]["type_decision_ref"] = None
    result = project(data, phase="candidate_graph")
    assert {item.subject_ref.entity_id for item in result.targets} == {"root", "product"}
    assert all(item.state == "pending" and not item.assertion_refs for item in result.targets)
    assert result.graph.relationships == result.graph.relationship_groups == []


def test_evidence_review_accepts_original_property_despite_failed_shacl_and_normalization():
    data = payload()
    prop = relationship("property", subject_ref={"entity_id": "product", "revision": 1})
    del prop["object_ref"]
    prop.update(
        predicate_iri=PROPERTY, raw_value="超出常规范围", raw_unit="mg",
        normalized_value=None, normalization_available=False,
        decision_status="supported", structural_valid=False,
        model_supported=False, policy_eligible=False,
        validation_diagnostics=[{
            "check": "shacl", "status": "failed", "reason_codes": ["max_inclusive"],
            "message": "原文数值超过本体约束上限。",
        }, {
            "check": "metric", "status": "incomplete", "reason_codes": ["unparsed_value"],
            "message": "未生成规范化数值。",
        }],
    )
    data["properties"] = [prop]
    result = project(data, phase="evidence_review")
    target = next(item for item in result.targets
                  if item.kind == "property" and item.subject_ref.entity_id == "product")
    assert result.phase == "evidence_review"
    assert target.supported_count == 1
    assert [item.id for item in target.supported_assertion_refs] == ["property"]
    assert result.summary.properties.supported == 1
    assert result.graph.properties[0].validation_diagnostics[0].status == "failed"
    assert result.graph.properties[0].raw_value == "超出常规范围"
    assert result.graph.properties[0].raw_unit == "mg"
    assert not result.graph.properties[0].normalization_available


def test_evidence_review_preserves_not_accepted_candidates_and_independent_child_evidence():
    data = payload()
    data["relationships"] = [relationship(
        "parent", decision_status="unsupported", reason_code="predicate_mismatch",
        reason="原文表达的角色与此关系不符。",
    )]
    scope = {"scope_id": "3" * 64, "members": [{
        "relation_ref": ref("parent"), "member_ref": {"entity_id": "product", "revision": 1},
    }]}
    prop = relationship("child", subject_ref={"entity_id": "product", "revision": 1}, scope=scope)
    del prop["object_ref"]
    prop.update(predicate_iri=PROPERTY, raw_value="0", normalized_value=0, raw_unit="g",
                unit="mg", normalization_available=True, decision_status="supported")
    data["properties"] = [prop]
    result = project(data, phase="evidence_review")
    assert len(result.targets) == 4
    assert result.summary.relationships.total == result.summary.properties.total == 2
    assert root_relation(result).state == "rejected"
    assert [item.id for item in root_relation(result).assertion_refs] == ["parent"]
    assert root_relation(result).supported_count == 0
    assert root_relation(result).reason == "原文表达的角色与此关系不符。"
    child = next(item for item in result.targets if item.scope_id == scope["scope_id"])
    assert child.supported_count == 1
    assert result.graph.properties[0].normalized_value == 0
    assert result.summary.properties.supported == 1


@pytest.mark.parametrize("change", [
    {"decision_status": "undetermined"}, {"decision_status": "not_checked"},
    {"proof_ref": None}, {"decision_refs": []}, {"invalidated": True},
    {"independent_review": "rejected"}, {"source_selection_refs": {}},
    {"object_ref": {"entity_id": "missing", "revision": 1}},
])
def test_evidence_review_does_not_upgrade_pending_or_invalid_evidence(change):
    data = payload()
    data["relationships"] = [relationship(decision_status="supported", **{
        key: value for key, value in change.items() if key != "decision_status"
    })]
    data["relationships"][0].update(change)
    result = project(data, phase="evidence_review")
    assert root_relation(result).supported_count == 0
    assert root_relation(result).supported_assertion_refs == []
    assert len(root_relation(result).assertion_refs) == 1
    assert result.summary.relationships.total == 2


def test_evidence_review_accepted_negative_is_not_a_positive_target():
    data = payload()
    data["relationships"] = [relationship(decision_status="supported", polarity="negated")]
    result = project(data, phase="evidence_review")
    assert len(root_relation(result).supported_assertion_refs) == 1
    assert root_relation(result).negated_count == 1
    assert root_relation(result).supported_count == 0
    assert result.summary.relationships.supported == 0


def test_evidence_review_invalid_entity_identity_cannot_become_an_accepted_endpoint():
    data = payload()
    data["relationships"] = [relationship(decision_status="supported")]
    data["invalidated_refs"] = [ref("type:product")]
    result = project(data, phase="evidence_review")
    assert root_relation(result).supported_count == 0
    assert root_relation(result).supported_assertion_refs == []
    assert all(item.subject_ref.entity_id == "root" for item in result.targets)
