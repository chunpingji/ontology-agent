"""Source-scoped conditions must survive discovery, independent review and recovery."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from tests.test_extraction.test_evidence_repair import _ontology, repaired_response
from tests.test_extraction.test_joint_evidence_validation import _fixture


def scope_fixture(tmp_path):
    _analysis, index, task, target, binding = _fixture(
        tmp_path, extra_text="仅适用于批次B01。",
    )
    restriction = next(u for u in index.ir.evidence_units if u.text == "仅适用于批次B01。")
    context = assemble_context(
        target, task.record_id, index, repair_enabled=True,
        required_context_refs=[binding, index.ir.anchor(restriction.evidence_id)],
    )
    menu = compile_local_menu(_ontology(), task.subject, engine=object())
    predicate = next(p for p in menu.relationships if p.iri == task.predicate_iri)
    return task, context, predicate, menu


@pytest.mark.parametrize("mode", ["unconditional", "real_condition", "identity_only"])
def test_scoped_conditions_are_cited_and_independently_checked(tmp_path, monkeypatch, mode):
    task, context, predicate, menu = scope_fixture(tmp_path)
    requests = []

    def respond(_client, *, user, schema, **_kwargs):
        request = json.loads(user)
        requests.append(request)
        result = repaired_response(request)
        if request["stage"] == "discovery":
            fields = schema["$defs"]["RelationshipAssertion"]["properties"]
            assert "applicability" not in fields
            assert "scope_qualifiers" in fields
            for proposal in result["proposals"]:
                if mode == "unconditional":
                    proposal["scope_qualifiers"] = []
                else:
                    quote = (proposal["object_quote"] if mode == "identity_only" else {
                        "evidence_id": next(f["evidence_id"] for f in request["fragments"]
                                            if f["text"] == "仅适用于批次B01。"),
                        "text": "仅适用于批次B01。",
                    })
                    proposal["scope_qualifiers"] = [{
                        "dimension": "batch", "condition_quote": quote,
                    }]
        else:
            for review, candidate in zip(
                result["verifications"], request["candidates"], strict=True,
            ):
                review["condition_support"] = deepcopy(candidate["claim"]["conditions"])
                review["applicability_verdict"] = "supported"
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = EvidenceRepairAdapter(object(), model_identity="scope-fixture")
    outcome = adapter.inspect(task, context, predicate, menu)
    assert len(requests) == 2
    edge = outcome.edges[0]
    if mode == "identity_only":
        assert not edge.policy_eligible
        assert edge.reason_code == "applicability_not_supported"
    else:
        assert edge.policy_eligible
        if mode == "real_condition":
            assert edge.polarity == "conditional"
            assert edge.applicability == {"batch": "仅适用于批次B01。"}
            assert edge.conditions == ["仅适用于批次B01。"]
            assert edge.condition_evidence_refs
        else:
            assert edge.polarity == "affirmed" and not edge.conditions and not edge.applicability
    restored = adapter.verify_existing(task, context, predicate, menu, context.protocol_state)
    assert restored.model_calls == 0 and len(requests) == 2


@pytest.mark.parametrize("bad_scope", ["invented_iri", "foreign_source", "duplicate_dimension"])
def test_invalid_scope_cannot_reach_verification(tmp_path, monkeypatch, bad_scope):
    task, context, predicate, menu = scope_fixture(tmp_path)
    stages = []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        stages.append(request["stage"])
        result = repaired_response(request)
        f = next(f for f in request["fragments"] if f["text"] == "仅适用于批次B01。")
        quote = {"evidence_id": f["evidence_id"], "text": f["text"]}
        if bad_scope == "invented_iri":
            quote["text"] = "https://example.org/Equipment"
        elif bad_scope == "foreign_source":
            quote["evidence_id"] = "foreign-run-source"
        scope = {"dimension": "equipment", "condition_quote": quote}
        for proposal in result["proposals"]:
            proposal["scope_qualifiers"] = (
                [scope] * (2 if bad_scope == "duplicate_dimension" else 1)
            )
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    with pytest.raises(model_adapter.RecognitionModelFailure, match="discovery_citation_invalid"):
        EvidenceRepairAdapter(object(), model_identity="scope-fixture").inspect(
            task, context, predicate, menu,
        )
    assert stages == ["discovery"]


@pytest.mark.parametrize("field,reason", [
    ("scope_protocol_version", "scope_policy_mismatch"),
    ("literal_quote_version", "literal_policy_mismatch"),
])
def test_old_scope_policy_cannot_reuse_frozen_outcome(tmp_path, monkeypatch, field, reason):
    task, context, predicate, menu = scope_fixture(tmp_path)
    calls = []

    def respond(_client, *, user, **_kwargs):
        calls.append(json.loads(user))
        return repaired_response(calls[-1])

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = EvidenceRepairAdapter(object(), model_identity="scope-fixture")
    adapter.inspect(task, context, predicate, menu)
    context.protocol_state.pop(field, None)
    with pytest.raises(ValueError, match=reason):
        adapter.inspect(task, context, predicate, menu)
    assert len(calls) == 2


@pytest.mark.parametrize("missing", [
    "scope_protocol", "owner_binding", "evidence_work", "literal_quotes",
])
def test_old_run_policy_does_not_construct_a_new_adapter(monkeypatch, missing):
    from app.services.document_analysis import execution

    calls = []
    monkeypatch.setattr(execution, "configured_model_adapter", lambda **kw: calls.append(kw))
    policy = {"evidence_repair": "evidence-repair-v1", "scope_protocol": "source-quoted-scope-v1",
              "owner_binding": "source-owned-binding-v2", "evidence_work": "evidence-work-v2",
              "literal_quotes": "source-integer-quotes-v2"}
    old = dict(policy)
    old.pop(missing)
    with pytest.raises(execution.CheckpointMismatch, match="policy version mismatch"):
        execution._configured_recognition_adapter(old)
    assert not calls
    execution._configured_recognition_adapter(policy)
    execution._configured_recognition_adapter({})
    assert calls == [{"protocol_version": "evidence-repair-v1"}, {}]
    with pytest.raises(execution.CheckpointMismatch, match="unknown evidence repair"):
        execution._configured_recognition_adapter({"evidence_repair": "unknown-policy"})
    assert len(calls) == 2
