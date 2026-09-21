"""Frozen ontology ranges remain intact across namespaces, repair and resume."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import OntologySnapshot, SubjectRef
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from tests.fixtures.report_ontology_constants import CMC_REPORT_IRI, DRUG_PRODUCT_IRI
from tests.test_extraction import test_joint_evidence_validation as joint
from tests.test_extraction import test_semantic_graph_closure as closure
from tests.test_extraction.test_evidence_repair import repaired_response
from tests.test_extraction.test_layered_recognition import arguments
from tests.test_extraction.test_source_object_recognition import build

CMC_DESCRIBES_IRI = "https://ontology.pharma-gmp.cn/slpra/drug-development/describes"


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


@pytest.mark.parametrize("namespace", ["domain", "generic"])
def test_menu_retains_full_frozen_range_independent_of_namespace(namespace):
    ontology = snapshot()
    root_iri = CMC_REPORT_IRI
    if namespace == "generic":
        ontology = OntologySnapshot.model_validate_json(
            ontology.model_dump_json().replace(
                "https://ontology.pharma-gmp.cn/slpra/", "urn:generic:",
            )
        )
        root_iri = root_iri.replace("https://ontology.pharma-gmp.cn/slpra/", "urn:generic:")
    before = ontology.model_dump_json()
    root = SubjectRef(entity_id="root", revision=1, class_iri=root_iri, is_document_root=True)
    menu = compile_local_menu(ontology, root)
    assert len(menu.relationships[0].range_class_iris) == 12
    assert len(menu.relationships[0].range_classes) == 12
    assert ontology.model_dump_json() == before
    assert menu == compile_local_menu(ontology, root)


def protocol_fixture(tmp_path, monkeypatch, *, change=None):
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
    menu = compile_local_menu(snapshot(), task.subject)
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


def test_repair_uses_all_ontology_types_and_restores_paid_result(tmp_path, monkeypatch):
    adapter, task, context, menu, requests = protocol_fixture(tmp_path, monkeypatch)
    result = adapter.inspect(task, context, menu.relationships[0], menu)
    assert result.model_calls == 2
    assert result.nodes[0].class_iri == DRUG_PRODUCT_IRI
    assert result.edges[0].policy_eligible
    for request, schema, system in requests:
        assert set(request["allowed_object_classes"]) == set(menu.relationships[0].range_class_iris)
        assert "不要求区分原料药或制剂" not in system
        assert "cmc_describes_type_scope" not in request
        for definition in schema["$defs"].values():
            field = definition.get("properties", {}).get("object_class_iri")
            if field:
                assert len(field["enum"]) == 12
    frozen = deepcopy(context.protocol_state)
    restored = adapter.verify_existing(task, context, menu.relationships[0], menu, frozen)
    assert restored.model_calls == 0
    assert len(requests) == 2


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


@pytest.mark.parametrize("illegal_type", ["urn:unmodeled-type", closure.API])
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


@pytest.mark.parametrize("current", [False, True])
def test_executor_restores_complete_ontology_range(tmp_path, current):
    from tests.test_extraction.test_current_work_resume import merge
    from tests.test_extraction.test_sparse_candidate_planning import resume_arguments

    args = arguments(tmp_path, ["药品：RX-01", "药品：RX-02"])
    args["root_class_iri"] = CMC_REPORT_IRI
    batches, rows, boundaries, observed = [], {}, [], []

    class InspectScope:
        def inspect(self, task, context, predicate, menu):
            from app.services.extraction.ontology_guided.executor import TaskOutcome

            observed.append(len(predicate.range_class_iris))
            return TaskOutcome(semantic_outcome="not_checked", reason_code="no_candidate_observed")

    def commit(batch):
        batches.append(batch)
        if current:
            merge(rows, batch.work_changes)
            boundaries.append(deepcopy(rows))

    original = build(snapshot(), InspectScope(), current_state=current).run(
        **args, batch_hook=commit, work_hook=lambda changes: merge(rows, changes),
    )
    assert observed and set(observed) == {12}
    if current:
        state = json.loads(json.dumps(boundaries[0]))
        control = state["control"]["current"]
        resume = {"resume_state": {"work_state": state, "frontier": control["frontier_policy"],
                                   "diagnostics": control["diagnostics"]}}
    else:
        resume = resume_arguments(batches[0])
    observed.clear()
    restored = build(snapshot(), InspectScope(), current_state=current).run(**args, **resume)
    assert restored.graph == original.graph
    assert observed and set(observed) == {12}
