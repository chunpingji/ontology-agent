"""Independent source verification, target isolation and actual request costs."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from app.services.extraction.evidence_identity import evidence_hash
from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.contracts import (
    DocumentContext,
    SubjectRef,
    VerificationTarget,
    VersionedRef,
)
from app.services.extraction.ontology_guided.model_adapter import (
    ADAPTER_VERSION,
    DISCOVERY_STAGE,
    VERIFICATION_STAGE,
    LocalModelRecognitionAdapter,
    RecognitionModelFailure,
)
from app.services.extraction.ontology_guided.records import RecordIndex
from app.services.extraction.ontology_guided.scheduler import RecognitionTask
from app.services.llm.local_client import ExecutionLost
from app.services.llm.model_runtime import ModelCancelled, runtime
from app.services.llm.model_scheduler import ModelSlotLost

from .test_semantic_graph_closure import (
    API,
    DESCRIBES,
    INGREDIENT,
    PRODUCT,
    ROOT,
    _analysis,
    _ontology,
    _respond,
)


def _inputs(tmp_path):
    analysis = _analysis(tmp_path)
    index = RecordIndex(analysis.ir)
    record = next(
        record for record in index.records
        if any(unit.text == "本报告描述产品甲片。" for unit in record.source_units)
    )
    subject = SubjectRef(entity_id="root", revision=1, class_iri=ROOT, is_document_root=True)
    predicate = _ontology().classes[ROOT].declared_relationships[0]
    task = RecognitionTask.create(
        subject=subject,
        predicate_iri=DESCRIBES,
        predicate_kind="relationship",
        record_id=record.record_id,
        phase=1,
        hop=0,
        dependency_hash="independent-verification",
    )
    target = VerificationTarget.create(
        run_fingerprint="independent-verification",
        claim_ref=VersionedRef(id=task.claim_lineage_id, revision=1),
        task_id=task.task_id,
        check_kind="predicate_entailment",
        document_context=DocumentContext(
            document_hash=analysis.ir.document_hash,
            document_class_iri=ROOT,
            root_ref=VersionedRef(id="root", revision=1),
        ),
        subject_ref=subject,
        predicate_iri=DESCRIBES,
        ontology_hash=_ontology().ontology_hash,
        source_scope_hash="source",
        context_hash="context",
    )
    return task, assemble_context(target, record.record_id, index), predicate, None


def _adapter():
    return LocalModelRecognitionAdapter(object(), model_identity="independent-test")


def test_discovery_support_cannot_approve_rejected_independent_proof(tmp_path, monkeypatch):
    requests, debits = [], []
    args = _inputs(tmp_path)
    args[1].bind_model_call_hook(lambda stage, count: debits.append((stage, count)))

    def respond(_client, *, user, **kwargs):
        request = json.loads(user)
        requests.append((request, runtime.get()["stage"]))
        assert kwargs["timeout_retries"] == 0
        assert kwargs["max_attempts"] == 1
        response = _respond(request)
        if request["stage"] == "discovery":
            response["proposals"][0]["reason"] = "DISCOVERY_REASON_MUST_NOT_LEAK"
        else:
            assert "DISCOVERY_REASON_MUST_NOT_LEAK" not in user
            assert "verdict" not in user
            assert "reason" not in user
            candidate = request["candidates"][0]
            claim = deepcopy(candidate["claim"])
            original = next(fragment.anchor.evidence_id for fragment in args[1].fragments
                            if fragment.fact_eligible)
            # The immutable semantic hash uses original IDs, not wire aliases.
            assert claim["endpoint_quote"]["evidence_id"].startswith("E")
            claim["endpoint_quote"]["evidence_id"] = original
            claim["endpoint_anchor"]["evidence_id"] = original
            assert candidate["target"]["literal_hash"] == evidence_hash(claim)
            assert candidate["target"]["subject_ref"] == request["subject"]
            assert candidate["target"]["predicate_iri"] == DESCRIBES
            response["verifications"][0].update(
                predicate_verdict="unsupported", reason="独立核验拒绝该谓词。"
            )
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    outcome = _adapter().inspect(*args)
    assert [stage for _, stage in requests] == [DISCOVERY_STAGE, VERIFICATION_STAGE]
    assert debits == [(DISCOVERY_STAGE, 1), (VERIFICATION_STAGE, 2)]
    assert requests[0][0]["fragments"] == requests[1][0]["fragments"]
    assert outcome.model_calls == 2
    assert len(outcome.edges) == 1
    assert not outcome.edges[0].policy_eligible
    assert outcome.semantic_outcome == "unsupported"
    assert {item["reason"] for item in outcome.decision_payloads} == {"独立核验拒绝该谓词。"}
    assert {item["verifier_version"] for item in outcome.decision_payloads} == {ADAPTER_VERSION}


@pytest.mark.parametrize("mismatch", ["candidate", "target", "missing", "duplicate", "extra"])
def test_independent_response_must_match_exact_frozen_candidate_set(
    tmp_path, monkeypatch, mismatch,
):
    calls = []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        calls.append(request)
        response = _respond(request)
        if request["stage"] == "verification":
            items = response["verifications"]
            if mismatch == "candidate":
                items[0]["candidate_id"] = "another-candidate"
            elif mismatch == "target":
                items[0]["target_id"] = "another-target"
            elif mismatch == "missing":
                items.clear()
            elif mismatch == "duplicate":
                items.append(dict(items[0]))
            else:
                items.append({**items[0], "candidate_id": "extra-candidate"})
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(RecognitionModelFailure, match="candidate_target_mismatch") as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.model_calls == len(calls) == 2


@pytest.mark.parametrize("field", ["type", "role", "predicate", "applicability", "bridge"])
def test_independent_unsupported_check_prevents_effective_relation(tmp_path, monkeypatch, field):
    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "verification":
            response["verifications"][0][f"{field}_verdict"] = "unsupported"
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    outcome = _adapter().inspect(*_inputs(tmp_path))
    assert outcome.edges[0].decision_status == "unsupported"
    assert not outcome.edges[0].policy_eligible
    assert any(item["check_kind"] == "bridge_entailment" for item in outcome.decision_payloads)


@pytest.mark.parametrize("remaining, candidates, expected_calls, complete", [
    (0, True, 0, False), (1, True, 1, False), (1, False, 1, True), (2, True, 2, True),
])
def test_remaining_budget_never_promotes_unverified_proposals(
    tmp_path, monkeypatch, remaining, candidates, expected_calls, complete,
):
    args = _inputs(tmp_path)
    args[1].remaining_model_calls = remaining
    calls = []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        calls.append(request)
        return _respond(request) if candidates else {"proposals": []}

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    outcome = _adapter().inspect(*args)
    assert outcome.model_calls == len(calls) == expected_calls
    assert outcome.complete is complete
    if not complete:
        assert outcome.reason_code == "record_model_call_budget_exhausted"
        assert not outcome.edges and not outcome.decision_payloads and not outcome.proof_payloads


@pytest.mark.parametrize("oversized_stage", ["discovery", "verification"])
def test_each_full_schema_and_prompt_is_measured_without_truncation(
    tmp_path, monkeypatch, oversized_stage,
):
    measured, calls = [], []

    class Counter:
        def count(self, text):
            measured.append(text)
            return 101 if f'"stage":"{oversized_stage}"' in text else 1

    def respond(_client, *, user, system, schema, **_kwargs):
        calls.append(json.loads(user))
        assert measured[-1] == system + user + json.dumps(schema, ensure_ascii=False)
        return _respond(calls[-1])

    monkeypatch.setattr(model_adapter.settings, "evidence_max_input_tokens", 100)
    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = LocalModelRecognitionAdapter(object(), model_identity="test", token_counter=Counter())
    outcome = adapter.inspect(*_inputs(tmp_path))
    assert len(measured) == (1 if oversized_stage == "discovery" else 2)
    assert outcome.model_calls == len(calls) == len(measured) - 1
    assert not outcome.complete and not outcome.edges
    assert outcome.reason_code.endswith("context_budget_exceeded")
    assert "本报告描述产品甲片。" in measured[-1]
    assert '"$defs"' in measured[-1]


@pytest.mark.parametrize("failed_stage", ["discovery", "verification"])
@pytest.mark.parametrize("failure", [ValueError, ModelCancelled, ExecutionLost, ModelSlotLost])
def test_request_failures_preserve_cost_and_cancellation_identity(
    tmp_path, monkeypatch, failed_stage, failure,
):
    calls = []
    original = failure()

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        calls.append(request)
        if request["stage"] == failed_stage:
            raise original
        return _respond(request)

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    expected_type = RecognitionModelFailure if failure is ValueError else failure
    with pytest.raises(expected_type) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.model_calls == len(calls) == (1 if failed_stage == "discovery" else 2)
    if failure is not ValueError:
        assert caught.value is original


def test_persistence_callback_failure_is_never_wrapped_or_dispatched(tmp_path, monkeypatch):
    args = _inputs(tmp_path)
    original = RuntimeError("lost-before-dispatch")

    def abort(_stage, _count):
        raise original

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("the failed persistent debit must prevent dispatch")

    args[1].bind_model_call_hook(abort)
    monkeypatch.setattr(model_adapter, "chat_with_schema", unexpected_call)
    with pytest.raises(RuntimeError) as caught:
        _adapter().inspect(*args)
    assert caught.value is original


def test_endpoint_type_polarity_and_condition_have_distinct_frozen_targets(tmp_path, monkeypatch):
    args = list(_inputs(tmp_path))
    args[2] = args[2].model_copy(update={"range_class_iris": [PRODUCT, API]})
    verified_claims = []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "discovery":
            proposal = response["proposals"][0]
            response["proposals"] = [
                proposal,
                {**proposal, "object_class_iri": API},
                {**proposal, "polarity": "negated"},
                {**proposal, "condition_support": proposal["predicate_support"]},
            ]
        else:
            verified_claims.extend(request["candidates"])
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    outcome = _adapter().inspect(*args)
    assert outcome.model_calls == 2
    assert len({item["target_id"] for item in verified_claims}) == 4
    assert len({item["candidate_id"] for item in verified_claims}) == 4
    assert {item["target_id"] for item in outcome.proof_payloads} == {
        item["target_id"] for item in verified_claims
    }
    conditional = next(item for item in outcome.edges if item.polarity == "conditional")
    assert conditional.conditions == ["本报告描述产品甲片。"]


def test_discovery_schema_exposes_required_typed_endpoints_to_model():
    schema = model_adapter.ModelResponse.model_json_schema()
    branches = schema["properties"]["proposals"]["items"]["oneOf"]
    definitions = [schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]] for branch in branches]
    relationship = next(item for item in definitions
                        if item["properties"]["kind"]["const"] == "relationship")
    property_ = next(item for item in definitions
                     if item["properties"]["kind"]["const"] == "property")
    assert {"object_class_iri", "object_label", "object_quote"} <= set(relationship["required"])
    assert "value_quote" in property_["required"]
    assert relationship["properties"]["value_quote"]["type"] == "null"
    assert property_["properties"]["object_quote"]["type"] == "null"


@pytest.mark.parametrize("kind, endpoint", [
    ("relationship", {"value_quote": {"evidence_id": "record", "text": "value"}}),
    ("property", {"object_quote": {"evidence_id": "record", "text": "object"}}),
])
def test_cross_kind_endpoint_cannot_override_selected_assertion_kind(kind, endpoint):
    from pydantic import ValidationError

    proposal = {
        "kind": kind, "bridge_kind": "explicit_assertion",
        **({"value_quote": {"evidence_id": "record", "text": "value"}}
           if kind == "property" else {
               "object_quote": {"evidence_id": "record", "text": "object"},
               "object_label": "object", "object_class_iri": PRODUCT,
           }),
        **endpoint,
    }
    with pytest.raises(ValidationError):
        model_adapter.ModelResponse.model_validate({"proposals": [proposal]}, strict=True)


@pytest.mark.parametrize("stage", ["discovery", "verification"])
def test_schema_failure_exposes_only_safe_category_and_declared_location(
    tmp_path, monkeypatch, stage,
):
    secret = "private-source-and-provider-url"

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        result = _respond(request)
        if request["stage"] == stage:
            item = result["proposals" if stage == "discovery" else "verifications"][0]
            item[secret] = secret
            if stage == "discovery":
                item.pop("object_quote")
            else:
                item["predicate_verdict"] = secret
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(RecognitionModelFailure) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    failure = caught.value
    assert failure.reason_code == f"{stage}_response_invalid"
    assert failure.cause_type == "ValidationError"
    assert failure.model_calls == (1 if stage == "discovery" else 2)
    assert failure.validation_errors
    assert secret not in json.dumps(failure.__dict__)
    assert all(set(item) == {"type", "loc"} for item in failure.validation_errors)
    assert any("<unmodeled_field>" in item["loc"] for item in failure.validation_errors)


def test_structurally_incomplete_real_discovery_remains_a_paid_failure(tmp_path, monkeypatch):
    # Exact response shape observed in real frozen diagnostic-02/03: the
    # server accepted a relationship without its previously optional endpoint.
    def incomplete(_client, **_kwargs):
        return {"proposals": [{
            "kind": "relationship", "bridge_kind": "explicit_assertion",
            "polarity": "affirmed", "reason": "待核验的候选。",
        }]}

    monkeypatch.setattr(model_adapter, "chat_with_schema", incomplete)
    with pytest.raises(RecognitionModelFailure) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.reason_code == "discovery_response_invalid"
    assert caught.value.model_calls == 1
    assert {item["loc"][-1] for item in caught.value.validation_errors} == {
        "object_class_iri", "object_label", "object_quote",
    }


def test_root_response_schema_requires_empty_source_support_and_keeps_predicate_proof(
    tmp_path, monkeypatch,
):
    args = _inputs(tmp_path)
    captured = []

    def respond(_client, *, user, schema, **_kwargs):
        request = json.loads(user)
        binding = request["subject_binding"]
        assert binding["kind"] == "programmatic_document_root"
        assert binding["root_ref"] == request["target"]["document_context"]["root_ref"]
        assert not binding["source_evidence_required"]
        if request["stage"] == "verification":
            items = schema["properties"]["verifications"]["items"]
            variant = schema["$defs"][items["$ref"].rsplit("/", 1)[-1]]
            assert "subject_support" in variant["required"]
            assert variant["properties"]["subject_support"]["maxItems"] == 0
            captured.append(request)
        return _respond(request)

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    result = _adapter().inspect(*args)
    assert result.model_calls == 2 and captured
    assert result.edges[0].policy_eligible
    assert result.edges[0].subject_evidence_refs == []
    assert result.edges[0].predicate_evidence_refs
    assert "local_coreference" not in {item["check_kind"] for item in result.decision_payloads}


def test_synthetic_root_entity_id_cannot_become_source_evidence(tmp_path, monkeypatch):
    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "verification":
            response["verifications"][0]["subject_support"] = [{
                "evidence_id": request["subject"]["entity_id"],
                "text": "CMCReport",
            }]
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(RecognitionModelFailure) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.reason_code == "verification_citation_invalid"
    assert caught.value.model_calls == 2
    assert caught.value.cause_type == "source_quote_outside_scope"


@pytest.mark.parametrize("field", [
    "type_verdict", "role_verdict", "subject_binding_verdict", "predicate_verdict",
    "applicability_verdict", "counterevidence_verdict", "bridge_verdict",
])
def test_omitted_verification_facet_is_failed_request_not_semantic_undetermined(
    tmp_path, monkeypatch, field,
):
    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "verification":
            response["verifications"][0].pop(field)
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(RecognitionModelFailure) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.reason_code == "verification_response_invalid"
    assert caught.value.model_calls == 2
    assert caught.value.validation_errors == [{
        "type": "missing", "loc": ["verifications", 0, field],
    }]


@pytest.mark.parametrize("mismatch", ["id", "revision", "class", "document", "subject"])
def test_programmatic_root_binding_mismatch_stops_before_any_model_call(
    tmp_path, monkeypatch, mismatch,
):
    task, context, predicate, menu = _inputs(tmp_path)
    document_context = context.target.document_context
    if mismatch in {"id", "revision"}:
        root_ref = document_context.root_ref.model_copy(update={
            "id" if mismatch == "id" else "revision": "another-root" if mismatch == "id" else 2,
        })
        document_context = document_context.model_copy(update={"root_ref": root_ref})
    elif mismatch == "class":
        document_context = document_context.model_copy(update={"document_class_iri": PRODUCT})
    elif mismatch == "document":
        document_context = document_context.model_copy(update={"document_hash": "f" * 64})
    target = context.target.model_copy(update={"document_context": document_context})
    if mismatch == "subject":
        target = target.model_copy(update={
            "subject_ref": task.subject.model_copy(update={"is_document_root": False}),
        })
    context = context.model_copy(update={"target": target})

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("inconsistent root binding must not dispatch a model request")

    monkeypatch.setattr(model_adapter, "chat_with_schema", unexpected_call)
    result = _adapter().inspect(task, context, predicate, menu)
    assert not result.complete and result.model_calls == 0
    assert result.reason_code == "document_root_binding_mismatch"
    assert not result.edges


@pytest.mark.parametrize("forged_owner", [False, True])
def test_non_root_subject_still_needs_original_local_owner_source(
    tmp_path, monkeypatch, forged_owner,
):
    analysis = _analysis(tmp_path)
    index = RecordIndex(analysis.ir)
    record = next(record for record in index.records
                  if any("活性成分为" in unit.text for unit in record.source_units))
    subject = SubjectRef(entity_id="product", revision=1, class_iri=PRODUCT)
    task = RecognitionTask.create(
        subject=subject, predicate_iri=INGREDIENT, predicate_kind="relationship",
        record_id=record.record_id, phase=1, hop=1, dependency_hash="local-owner",
    )
    _, root_context, _, _ = _inputs(tmp_path)
    target = root_context.target.model_copy(update={
        "subject_ref": subject, "task_id": task.task_id, "predicate_iri": INGREDIENT,
    })
    context = assemble_context(target, record.record_id, index)
    predicate = _ontology().classes[PRODUCT].declared_relationships[0]

    def respond(_client, *, user, schema, **_kwargs):
        request = json.loads(user)
        assert request["subject_binding"]["kind"] == "source_local_coreference"
        assert request["subject_binding"]["source_evidence_required"]
        response = _respond(request)
        if request["stage"] == "verification":
            variant = schema["$defs"]["ModelVerification"]
            assert "maxItems" not in variant["properties"]["subject_support"]
            assert response["verifications"][0]["subject_support"]
            if forged_owner:
                response["verifications"][0]["subject_support"] = [{
                    "evidence_id": subject.entity_id, "text": "产品甲片",
                }]
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    if forged_owner:
        with pytest.raises(RecognitionModelFailure, match="verification_citation_invalid") as error:
            _adapter().inspect(task, context, predicate, None)
        assert error.value.cause_type == "source_quote_outside_scope"
        assert error.value.model_calls == 2
    else:
        result = _adapter().inspect(task, context, predicate, None)
        assert result.edges[0].policy_eligible
        assert result.edges[0].subject_evidence_refs
        assert any(item["check_kind"] == "local_coreference"
                   for item in result.decision_payloads)


@pytest.mark.parametrize("support_field", [
    "type_support", "predicate_support", "subject_support", "condition_support",
    "counterevidence_support",
])
def test_each_verification_support_array_must_be_explicit(tmp_path, monkeypatch, support_field):
    def respond(_client, *, user, schema, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "verification":
            items = schema["properties"]["verifications"]["items"]
            variant = schema["$defs"][items["$ref"].rsplit("/", 1)[-1]]
            assert {
                "predicate_support", "subject_support", "condition_support",
                "counterevidence_support",
            } <= set(variant["required"])
            response["verifications"][0].pop(support_field)
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(RecognitionModelFailure) as caught:
        _adapter().inspect(*_inputs(tmp_path))
    assert caught.value.reason_code == "verification_response_invalid"
    assert caught.value.model_calls == 2
    assert caught.value.validation_errors == [{
        "type": "missing", "loc": ["verifications", 0, support_field],
    }]


def test_explicit_empty_support_cannot_turn_supported_verdicts_into_an_effective_edge(
    tmp_path, monkeypatch,
):
    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        response = _respond(request)
        if request["stage"] == "verification":
            for verification in response["verifications"]:
                for key in list(verification):
                    if key.endswith("_verdict"):
                        verification[key] = "supported"
                    elif key.endswith("_support"):
                        verification[key] = []
        return response

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    outcome = _adapter().inspect(*_inputs(tmp_path))
    assert outcome.model_calls == 2
    assert outcome.edges
    assert all(not edge.policy_eligible for edge in outcome.edges)
    assert all(not edge.structural_valid for edge in outcome.edges)
    assert all(edge.predicate_evidence_refs == [] for edge in outcome.edges)
    assert outcome.semantic_outcome == "undetermined"
