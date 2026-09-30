"""Source acceptance is independent of typed normalization and SHACL diagnostics."""

import json
from copy import deepcopy

import pytest
from docx import Document

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    SlotSpec,
    SubjectRef,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.record_discovery import RecordDiscoveryPolicy
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.extraction.word_analysis import analyze_word_core
from tests.test_extraction.test_tool_engine_adapter import setup_adapter

pytest_plugins = ["tests.test_extraction.test_tool_engine_freeze"]


def property_source(tmp_path, raw="5 mg", *, unit="mg", target_unit="g", datatype="decimal"):
    document = Document()
    document.add_paragraph(f"数量：{raw}")
    path = tmp_path / "review.docx"
    document.save(path)
    index = RecordIndex(analyze_word_core(path).ir)
    record = next(r for r in index.records if "数量" in r.text)
    source_unit = record.source_units[0]
    subject = SubjectRef(entity_id="subject-server-id", revision=3,
                         class_iri="urn:Source", is_document_root=True)
    predicate = SlotSpec(
        iri="urn:quantity", label="数量", declared_by=[subject.class_iri],
        datatype_iris=[f"http://www.w3.org/2001/XMLSchema#{datatype}"],
        canonical_unit=target_unit,
    )

    def task_context(slot):
        task = RecognitionTask.create(
            subject=subject, predicate_iri=slot.iri, predicate_kind=slot.kind,
            record_id=record.record_id, phase=1, hop=0, dependency_hash="frozen-dependency",
        )
        seed = VerificationTarget.create(
            run_fingerprint="test-run",
            claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1),
            task_id=task.task_id, check_kind="predicate_entailment",
            document_context=DocumentContext(
                document_hash=index.ir.document_hash, document_class_iri=subject.class_iri,
                root_ref=VersionedRef(id=subject.entity_id, revision=subject.revision),
            ),
            subject_ref=subject, predicate_iri=slot.iri, ontology_hash=evidence_hash("ontology"),
            source_scope_hash=evidence_hash(record.record_id), context_hash=evidence_hash("seed"),
        )
        context = assemble_context(seed, record.record_id, index, subject_evidence_refs=[],
                                   subject_label="报告", predicate=slot)
        return dict(task=task, index=index, context=context)

    def quote(text):
        return dict(evidence_id=source_unit.evidence_id, text=text, context_text=None)

    return dict(
        index=index, options=task_context(predicate), task_context=task_context,
        predicate=predicate, quote=quote, source_unit=source_unit,
        proposal=dict(entities=[], relations=[], external_links=[], observations=[],
                      properties=[dict(
            local_id="quantity", subject_id=subject.entity_id, predicate_iri=predicate.iri,
            value_quote=quote(raw), field_support=[quote("数量")],
            unit_support=[quote(unit)] if unit else [],
            qualifiers=dict(polarity="affirmed", modality="asserted",
                            condition_support=[], scope_qualifiers=[]),
            bridge_kind="explicit_assertion", bridge_ref_ids=[],
        )]),
    )


def review_adapter(source, monkeypatch, **options):
    values = setup_adapter(source, monkeypatch, **options)
    adapter, _task, context, *_ = values
    adapter.record_discovery = RecordDiscoveryPolicy(graph_phase="evidence_review")
    adapter.max_output_tokens = 16384
    context.tool_inputs["graph_phase"] = "evidence_review"
    return values


@pytest.mark.parametrize("raw,unit,target_unit,available", [
    ("5 mg", "mg", "g", True),
    ("5", None, "g", False),
    ("1至5 mg", "mg", "g", False),
    ("5 mg", "mg", "mL", False),
])
def test_source_value_survives_missing_units_ranges_and_incompatible_normalization(
    tmp_path, monkeypatch, raw, unit, target_unit, available,
):
    source = property_source(tmp_path, raw, unit=unit, target_unit=target_unit)
    adapter, task, context, predicate, menu, stored, requests = review_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    prop, = outcome.properties
    assert outcome.complete and prop.decision_status == "supported"
    assert prop.structural_valid and prop.model_supported and prop.policy_eligible
    assert prop.proof_ref and prop.decision_refs and prop.raw_value == raw
    assert prop.raw_unit == unit and prop.normalization_available is available
    assert (prop.normalized_value is not None) is available
    assert prop.revision == 2
    assert len(requests) == 2
    assert {d.check for d in prop.validation_diagnostics} == {"metric", "shacl"}
    proof, = outcome.proof_payloads
    assert proof["proof_policy_version"] == "ontology-evidence-review-v1"
    if unit is None:
        verified = [row["value"] for row in stored["results"].values()
                    if row["field"] == "verification"][-1]
        assert all(d["check_kind"] != "unit" for t in verified["targets"] for d in t["decisions"])
        assert "unit_source_missing" in next(d for d in prop.validation_diagnostics
                                             if d.check == "metric").reason_codes


def test_shacl_failure_is_diagnostic_and_does_not_remove_available_normalization(
    tmp_path, monkeypatch,
):
    source = property_source(tmp_path)
    adapter, task, context, predicate, menu, _, requests = review_adapter(source, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("isolated SHACL unavailable")

    monkeypatch.setattr("pyshacl.validate", unavailable)
    outcome = adapter.inspect(task, context, predicate, menu)
    prop, = outcome.properties
    assert outcome.complete and prop.policy_eligible
    assert prop.normalization_available and prop.normalized_value == "0.005"
    assert next(d for d in prop.validation_diagnostics if d.check == "shacl").status != "passed"
    assert len(requests) == 2


@pytest.mark.parametrize("verdict", ["unsupported", "undetermined"])
def test_unaccepted_property_keeps_source_and_its_own_semantic_reason(
    tmp_path, monkeypatch, verdict,
):
    from app.services.llm import local_client

    source = property_source(tmp_path)
    adapter, task, context, predicate, menu, _, requests = review_adapter(source, monkeypatch)
    transport = local_client.responses_create

    def rejected(client, **kwargs):
        turn = transport(client, **kwargs)
        part = turn.output_items[0]["content"][0]
        answer = json.loads(part["text"])
        for result in answer.get("verifications", []):
            for facet in result["facets"]:
                if facet["name"] == "predicate":
                    facet.update(verdict=verdict, reason="原文中的数量不属于该属性角色")
        part["text"] = json.dumps(answer)
        return turn

    monkeypatch.setattr(local_client, "responses_create", rejected)
    outcome = adapter.inspect(task, context, predicate, menu)
    prop, = outcome.properties
    assert prop.decision_status == verdict and not prop.policy_eligible
    assert prop.proof_ref is None and not outcome.proof_payloads
    assert prop.raw_value == "5 mg" and prop.raw_unit == "mg" and prop.evidence_refs
    assert prop.normalized_value is None and not prop.normalization_available
    assert "predicate_not_supported" in prop.reason
    assert "原文中的数量不属于该属性角色" in prop.reason
    assert len(requests) == 2


@pytest.mark.parametrize("value,datatype", [("0", "integer"), ("false", "boolean")])
def test_zero_and_false_remain_real_values(tmp_path, monkeypatch, value, datatype):
    source = property_source(tmp_path, value, unit=None, target_unit=None, datatype=datatype)
    adapter, task, context, predicate, menu, _, _ = review_adapter(source, monkeypatch)
    prop, = adapter.inspect(task, context, predicate, menu).properties
    assert prop.policy_eligible and prop.normalization_available and prop.raw_value == value


def test_missing_marker_remains_observation(tmp_path, monkeypatch):
    source = property_source(tmp_path, "N/A", unit=None)
    adapter, task, context, predicate, menu, stored, requests = review_adapter(source, monkeypatch)
    outcome = adapter.inspect(task, context, predicate, menu)
    assert not outcome.properties
    frozen = stored["results"][stored["protocol"]["discovery_ref"]]["value"]
    assert any(o["kind"] == "missing" and o["quote"]["text"] == "N/A"
               for o in frozen["observations"])
    assert len(requests) == 1


def test_wrong_declared_unit_is_not_excused_as_missing_normalization(tmp_path, monkeypatch):
    source = property_source(tmp_path)
    source["proposal"]["properties"][0]["unit_support"] = [source["quote"]("数量")]
    adapter, task, context, predicate, menu, _, _ = review_adapter(source, monkeypatch)
    prop, = adapter.inspect(task, context, predicate, menu).properties
    assert prop.decision_status != "supported" and not prop.policy_eligible
    assert prop.proof_ref is None and "unit_not_bound_to_value" in prop.reason


@pytest.mark.parametrize("finalize_crash", [False, True])
def test_pending_single_resumes_frozen_even_target_without_rediscovery(
    tmp_path, monkeypatch, finalize_crash,
):
    from app.services.llm import local_client

    source = property_source(tmp_path)
    adapter, task, context, predicate, menu, stored, requests = review_adapter(
        source, monkeypatch,
    )
    transport = local_client.responses_create

    def fail_verification(client, **kwargs):
        turn = transport(client, **kwargs)
        adapter.max_input_tokens = 1  # The next stage fails before making a reservation.
        return turn

    monkeypatch.setattr(local_client, "responses_create", fail_verification)
    pending = adapter.inspect(task, context, predicate, menu)
    candidate, = pending.properties
    assert not pending.complete and candidate.decision_status == "not_checked"
    assert candidate.revision == 1 and candidate.proof_ref is None
    assert candidate.reason_code == "context_budget_exceeded"
    frozen = deepcopy(stored["results"][stored["protocol"]["discovery_ref"]]["value"])
    assert frozen["local_ref_map"]["quantity"]["revision"] == 2
    saved = deepcopy(stored)
    adapter, task, context, predicate, menu, stored, requests = review_adapter(
        source, monkeypatch, stop_at="verification" if finalize_crash else None,
    )
    stored.update(saved)
    context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    if finalize_crash:
        with pytest.raises(RuntimeError, match="pause after durable commit"):
            adapter.inspect(task, context, predicate, menu)
        assert len(requests) == 1
        saved = deepcopy(stored)
        adapter, task, context, predicate, menu, stored, requests = review_adapter(
            source, monkeypatch, budget=0,
        )
        stored.update(saved)
        context.protocol_state, context.protocol_results = saved["protocol"], saved["results"]
    outcome = adapter.inspect(task, context, predicate, menu)
    accepted, = outcome.properties
    assert accepted.candidate_id == candidate.candidate_id and accepted.revision == 2
    assert accepted.policy_eligible and accepted.proof_ref
    assert len(requests) == (0 if finalize_crash else 1)
    if requests:
        request = json.loads(requests[0]["input_items"][0]["content"][0]["text"])
        assert request["stage"] == "verification"
    assert stored["results"][stored["protocol"]["discovery_ref"]]["value"] == frozen


def test_unknown_paid_request_does_not_publish_pending_outcome(tmp_path, monkeypatch):
    from app.services.extraction.ontology_guided.model_adapter import RecognitionModelFailure
    from app.services.llm import local_client
    from app.services.llm.local_client import StructuredModelError

    source = property_source(tmp_path)
    adapter, task, context, predicate, menu, stored, _ = review_adapter(source, monkeypatch)
    transport = local_client.responses_create

    def unknown(client, **kwargs):
        if json.loads(kwargs["input_items"][0]["content"][0]["text"])["stage"] == "verification":
            raise StructuredModelError("model_request_failed")
        return transport(client, **kwargs)

    monkeypatch.setattr(local_client, "responses_create", unknown)
    with pytest.raises(RecognitionModelFailure, match="model_request_failed"):
        adapter.inspect(task, context, predicate, menu)
    assert stored["protocol"]["pending_request"] is not None
    assert stored["protocol"]["discovery_ref"] and not stored["protocol"]["outcome_ref"]


def test_batch_pending_member_resumes_only_verification(tmp_path, monkeypatch):
    from tests.test_extraction.test_batch_model_adapter import batch_setup

    source = property_source(tmp_path)
    adapter, unit, context, menu, stored, requests = batch_setup(source, monkeypatch, size=1)
    adapter.record_discovery = RecordDiscoveryPolicy(graph_phase="evidence_review")
    for member in context.members:
        member.context.tool_inputs["graph_phase"] = "evidence_review"
    original = adapter._project_request

    def capacity(request):
        if request["text"]["format"]["name"] == "verification":
            from app.services.llm.local_client import StructuredModelError

            raise StructuredModelError("context_budget_exceeded")
        return original(request)

    monkeypatch.setattr(adapter, "_project_request", capacity)
    reviewed = adapter.inspect_work_unit(unit, context, menu)
    task = unit.members[0]
    assert reviewed.member_errors[task.task_id] == "context_budget_exceeded"
    member = context.members[0]
    member.context.tool_inputs["review_failure_reason"] = reviewed.member_errors[task.task_id]
    nodes = [member.context.tool_inputs["subject_node"]]
    pending = adapter.finalize_reviewed_member(
        task, context, current_entities=nodes, current_resolutions=[],
    )
    candidate, = pending.properties
    assert candidate.revision == 1 and not pending.complete and candidate.proof_ref is None
    before = deepcopy(stored["results"][stored["protocol"]["discovery_refs"][task.task_id]])
    resumed = context.model_copy(deep=True)
    resumed.protocol_state, resumed.protocol_results = deepcopy(stored["protocol"]), deepcopy(
        stored["results"],
    )
    monkeypatch.setattr(adapter, "_project_request", original)
    reviewed = adapter.inspect_work_unit(unit, resumed, menu)
    assert not reviewed.member_errors
    outcome = adapter.finalize_reviewed_member(
        task, resumed, current_entities=nodes, current_resolutions=[],
    )
    prop, = outcome.properties
    assert prop.candidate_id == candidate.candidate_id and prop.revision == 2
    assert prop.policy_eligible and prop.proof_ref and outcome.complete
    assert len(requests) == 2
    assert stored["results"][stored["protocol"]["discovery_refs"][task.task_id]] == before


@pytest.mark.parametrize("failure", ["semantic", "graph_diagnostic"])
def test_relation_group_retains_rejection_but_diagnostic_failure_does_not_block_source(
    tool_source, monkeypatch, failure,
):
    from app.services.extraction.ontology_guided import tool_runtime
    from app.services.llm import local_client
    from tests.test_extraction.test_candidate_relation_adapter import configure

    source = deepcopy(tool_source)
    source["registered_proposals"] = source["proposal"]["entities"]
    source["proposal"]["entities"] = []
    relation = source["proposal"]["relations"][0]
    quote = source["quote"]
    relation.update(bridge_kind="document_subject_description", source_assertion=dict(
        subject_support=[], object_support=[
            dict(object_id=identity, support=[quote(label)])
            for identity, label in [("b", "对象乙"), ("c", "对象丙")]
        ], predicate_support=[quote(source["source_unit"].text)], binding_ids=[],
    ))
    adapter, task, context, predicate, menu, _, requests = setup_adapter(source, monkeypatch)
    nodes = configure(adapter, [context], source)
    for node in nodes.values():
        if not node.root:
            node.dependency_refs = [node.type_decision_ref, node.referent_decision_ref]
    context.tool_inputs["entity_nodes"] = [n.model_dump(mode="json") for n in nodes.values()]
    context.tool_inputs["graph_phase"] = "evidence_review"
    adapter.record_discovery = RecordDiscoveryPolicy(graph_phase="evidence_review")
    adapter.max_output_tokens = 16384
    if failure == "semantic":
        transport = local_client.responses_create

        def reject(client, **kwargs):
            turn = transport(client, **kwargs)
            part = turn.output_items[0]["content"][0]
            answer = json.loads(part["text"])
            for result in answer.get("verifications", []):
                for facet in result["facets"]:
                    if facet["name"] == "predicate":
                        facet.update(verdict="unsupported", reason="原文不能证明此关系方向")
            part["text"] = json.dumps(answer)
            return turn

        monkeypatch.setattr(local_client, "responses_create", reject)
    else:
        dispatch = tool_runtime.dispatch_tool

        def unavailable(call, ctx, **kwargs):
            if call.name == "validate_graph":
                return tool_runtime._error("diagnostic_unavailable")
            return dispatch(call, ctx, **kwargs)

        monkeypatch.setattr(tool_runtime, "dispatch_tool", unavailable)
    outcome = adapter.inspect(task, context, predicate, menu)
    group, = outcome.relationship_groups
    assert group.selection == "one_of" and len(group.object_refs) == 2
    assert group.evidence_refs and group.revision == 2 and len(requests) == 2
    if failure == "semantic":
        assert group.decision_status == "unsupported" and not group.policy_eligible
        assert group.proof_ref is None and not outcome.proof_payloads
        assert "原文不能证明此关系方向" in group.reason
    else:
        assert group.decision_status == "supported" and group.policy_eligible
        assert group.proof_ref and outcome.complete
        diagnostic, = group.validation_diagnostics
        assert diagnostic.check == "relation_graph" and diagnostic.status == "incomplete"
        assert "diagnostic_unavailable" in diagnostic.reason_codes
