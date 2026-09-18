"""CMC report subject recognition stops at DrugProduct, with independent proof."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import (
    CMC_DESCRIBES_IRI,
    CMC_DESCRIBES_SCOPE_VERSION,
    CMC_REPORT_IRI,
    DRUG_PRODUCT_IRI,
    compile_local_menu,
    scope_cmc_describes,
)
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from tests.test_extraction import test_joint_evidence_validation as joint
from tests.test_extraction import test_semantic_graph_closure as closure
from tests.test_extraction.test_evidence_repair import repaired_response
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_source_object_recognition import build


def snapshot():
    raw = closure._ontology().model_dump_json()
    for old, new in ((closure.ROOT, CMC_REPORT_IRI), (closure.PRODUCT, DRUG_PRODUCT_IRI),
                     (closure.DESCRIBES, CMC_DESCRIBES_IRI)):
        raw = raw.replace(old, new)
    ontology = OntologySnapshot.model_validate_json(raw)
    product = ontology.classes[DRUG_PRODUCT_IRI]
    product.description = "A finished dosage form drug product that bears a clinical drug role."
    for number in range(11):
        iri = f"urn:drug-subtype:{number}"
        ontology.classes[iri] = product.model_copy(update={
            "iri": iri, "parent_iris": [DRUG_PRODUCT_IRI],
            "declared_relationships": [], "declared_properties": [],
        })
    return ontology


def test_menu_narrows_only_cmc_describes_without_mutating_ontology():
    ontology = snapshot()
    before = ontology.model_dump_json()
    root = SubjectRef(entity_id="root", revision=1, class_iri=CMC_REPORT_IRI,
                      is_document_root=True)
    old = compile_local_menu(ontology, root)
    new = compile_local_menu(ontology, root, cmc_describes_type_scope=True)
    assert len(old.relationships[0].range_class_iris) == 12
    assert new.relationships[0].range_class_iris == [DRUG_PRODUCT_IRI]
    assert [c.iri for c in new.relationships[0].range_classes] == [DRUG_PRODUCT_IRI]
    assert new.menu_id != old.menu_id
    assert ontology.model_dump_json() == before
    product = root.model_copy(update={"class_iri": DRUG_PRODUCT_IRI})
    assert compile_local_menu(ontology, product) == compile_local_menu(
        ontology, product, cmc_describes_type_scope=True,
    )
    # A similarly named relation, another domain or an unresolved/narrower
    # formal range must never be widened by this task policy.
    edge = old.relationships[0]
    for changed in (edge.model_copy(update={"iri": "urn:other:describes"}),
                    edge.model_copy(update={"constraint_status": "constraint_unresolved"}),
                    edge.model_copy(update={"range_class_iris": ["urn:drug-subtype:0"]})):
        assert scope_cmc_describes(changed, root) is changed
    assert scope_cmc_describes(edge, product) is edge


def protocol_fixture(tmp_path, monkeypatch, *, scope=True, change=None):
    for field, value in {"ROOT": CMC_REPORT_IRI, "PRODUCT": DRUG_PRODUCT_IRI,
                         "DESCRIBES": CMC_DESCRIBES_IRI,
                         "NAME": "项目名称：RX-01",
                         "BINDING": "本报告记录的药品由项目名称字段确定，用于临床备样生产。",
                         }.items():
        monkeypatch.setattr(joint, field, value)
    _analysis, index, task, target, binding = joint._fixture(tmp_path)
    context = assemble_context(target, task.record_id, index, required_context_refs=[binding],
                               repair_enabled=True)
    context.compact_recognition = True
    context.incremental_performance = True
    context.cmc_describes_type_scope = scope
    menu = compile_local_menu(snapshot(), task.subject, cmc_describes_type_scope=scope)
    requests = []

    def respond(_client, *, user, schema, system, **_kwargs):
        request = json.loads(user)
        requests.append((request, schema, system))
        response = repaired_response(request)
        if request["stage"] == "discovery":
            response["proposals"][0]["object_label"] = "RX-01"
            response["proposals"][0]["object_quote"]["text"] = "RX-01"
        else:
            drug_role = next(f for f in request["fragments"] if f["text"] == joint.BINDING)
            response["verifications"][0]["type_support"] = [
                {"evidence_id": drug_role["evidence_id"]},
            ]
        if change:
            change(request, response)
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = EvidenceRepairAdapter(object(), model_identity="cmc-scope-test")
    return adapter, task, context, menu, requests


def test_drug_subject_needs_no_formulation_subtype_and_retains_two_stage_proof(
    tmp_path, monkeypatch,
):
    adapter, task, context, menu, requests = protocol_fixture(tmp_path, monkeypatch)
    result = adapter.inspect(task, context, menu.relationships[0], menu)
    assert result.model_calls == 2
    assert result.nodes[0].class_iri == DRUG_PRODUCT_IRI
    assert result.edges[0].policy_eligible
    assert context.protocol_state["cmc_describes_type_scope"] == CMC_DESCRIBES_SCOPE_VERSION
    for request, schema, system in requests:
        assert request["allowed_object_classes"] == [DRUG_PRODUCT_IRI]
        assert "不要求区分原料药或制剂" in system
        assert "生产物料与计划制剂应分别判断" not in system
        assert request["cmc_describes_type_scope"] == CMC_DESCRIBES_SCOPE_VERSION
        for definition in schema["$defs"].values():
            field = definition.get("properties", {}).get("object_class_iri")
            if field:
                assert field["enum"] == [DRUG_PRODUCT_IRI]
    frozen = deepcopy(context.protocol_state)
    repeated = adapter.verify_existing(task, context, menu.relationships[0], menu, frozen)
    assert repeated.model_calls == 0 and len(requests) == 2
    context.cmc_describes_type_scope = False
    with pytest.raises(ValueError, match="type_scope_mismatch"):
        adapter.verify_existing(task, context, menu.relationships[0], menu, frozen)


@pytest.mark.parametrize("facet", ["type", "role", "predicate"])
def test_single_type_menu_does_not_bypass_missing_drug_or_report_role(
    tmp_path, monkeypatch, facet,
):
    def change(request, response):
        if request["stage"] == "verification":
            response["verifications"][0][facet + "_verdict"] = "undetermined"
            if facet != "role":
                response["verifications"][0][facet + "_support"] = []

    adapter, task, context, menu, requests = protocol_fixture(tmp_path, monkeypatch, change=change)
    result = adapter.inspect(task, context, menu.relationships[0], menu)
    assert len(requests) == 2
    assert not any(edge.policy_eligible for edge in result.edges)


@pytest.mark.parametrize("illegal_type", ["urn:drug-subtype:0", closure.API])
def test_server_rejects_out_of_scope_types_even_if_model_ignores_schema(
    tmp_path, monkeypatch, illegal_type,
):
    def change(request, response):
        if request["stage"] == "discovery":
            response["proposals"][0]["object_class_iri"] = illegal_type

    adapter, task, context, menu, requests = protocol_fixture(tmp_path, monkeypatch, change=change)
    result = adapter.inspect(task, context, menu.relationships[0], menu)
    assert not result.edges and not result.nodes
    assert len(requests) == 1


def test_project_name_alone_cannot_be_accepted_as_drug_role_proof(tmp_path, monkeypatch):
    def change(request, response):
        if request["stage"] == "verification":
            endpoint = request["candidates"][0]["claim"]["endpoint_quote"]
            response["verifications"][0]["type_support"] = [endpoint]

    adapter, task, context, menu, requests = protocol_fixture(tmp_path, monkeypatch, change=change)
    result = adapter.inspect(task, context, menu.relationships[0], menu)
    assert len(requests) == 2
    assert not any(edge.policy_eligible for edge in result.edges)
    assert context.protocol_state["verification"]["verifications"][0][
        "type_verdict"
    ] == "undetermined"


@pytest.mark.parametrize("current", [False, True])
@pytest.mark.parametrize("scope", [False, True])
def test_executor_restores_frozen_type_granularity(tmp_path, current, scope):
    from tests.test_extraction.test_current_work_resume import merge
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args = arguments(tmp_path, ["药品：RX-01", "药品：RX-02"])
    args["root_class_iri"] = CMC_REPORT_IRI
    batches, rows, boundaries, observed = [], {}, [], []

    class InspectScope:
        def inspect(self, task, context, predicate, menu):
            from app.services.extraction.ontology_guided.executor import TaskOutcome

            observed.append((context.cmc_describes_type_scope, len(predicate.range_class_iris)))
            return TaskOutcome(semantic_outcome="not_checked", reason_code="no_candidate_observed")

    def commit(batch):
        batches.append(batch)
        if current:
            merge(rows, batch.work_changes)
            boundaries.append(deepcopy(rows))

    original = build(snapshot(), InspectScope(), current_state=current,
                     cmc_describes_type_scope=scope).run(
        **args, batch_hook=commit, work_hook=lambda changes: merge(rows, changes),
    )
    assert observed and set(observed) == {(scope, 1 if scope else 12)}
    if current:
        state = json.loads(json.dumps(boundaries[0]))
        control = state["control"]["current"]
        resume = {"resume_state": {"work_state": state, "frontier": control["frontier_policy"],
                                   "diagnostics": control["diagnostics"]}}
    else:
        resume = resume_arguments(batches[0])
    observed.clear()
    restored = build(snapshot(), InspectScope(), current_state=current,
                     cmc_describes_type_scope=not scope).run(**args, **resume)
    assert restored.graph == original.graph
    assert observed and set(observed) == {(scope, 1 if scope else 12)}
