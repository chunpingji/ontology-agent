"""Reproposal and one new-evidence review have separate, durable limits."""

import json
from copy import deepcopy

import pytest

from app.services.extraction.ontology_guided import model_adapter
from app.services.extraction.ontology_guided.context import assemble_context
from app.services.extraction.ontology_guided.evidence_work import EvidenceWorkQueue
from app.services.extraction.ontology_guided.ontology_plan import compile_local_menu
from app.services.extraction.ontology_guided.repair_adapter import EvidenceRepairAdapter
from tests.test_extraction.test_evidence_repair import _ontology, repaired_response
from tests.test_extraction.test_joint_evidence_validation import _fixture


@pytest.mark.parametrize("reproposal_first", [False, True])
@pytest.mark.parametrize("final_supported", [False, True])
def test_reproposal_and_supplement_continue_once_in_either_order(
    tmp_path, monkeypatch, reproposal_first, final_supported,
):
    _analysis, index, task, target, _binding = _fixture(tmp_path)
    menu = compile_local_menu(_ontology(), task.subject, engine=object())
    predicate = next(p for p in menu.relationships if p.iri == task.predicate_iri)
    calls = []

    def respond(_client, *, user, **_kwargs):
        request = json.loads(user)
        calls.append(request)
        result = repaired_response(request)
        if reproposal_first and len(calls) == 1:
            for proposal in result["proposals"]:
                proposal["scope_qualifiers"] = [{
                    "dimension": "product", "condition_quote": proposal["object_quote"],
                }]
        if request["stage"] == "verification":
            second = sum(r["stage"] == "verification" for r in calls) == 2
            for review in result["verifications"]:
                if (second and not reproposal_first) or (len(calls) == 5 and not final_supported):
                    review["bridge_verdict"] = "unsupported"
        return result

    monkeypatch.setattr(model_adapter, "chat_with_schema", respond)
    adapter = EvidenceRepairAdapter(object(), model_identity="work-continuation-fixture")
    queue = EvidenceWorkQueue(index)
    current = task
    context = assemble_context(target, task.record_id, index, repair_enabled=True)
    initial = adapter.inspect(current, context, predicate, menu)
    assert not initial.edges[0].policy_eligible
    state = deepcopy(context.protocol_state)
    next_task = queue.observe(current, initial, context, predicate, state)
    first_kind = "rediscovery:" if reproposal_first else "positive_evidence:"
    assert next_task and next_task.retry_kind.startswith(first_kind)
    for expected_kind in (first_kind, "positive_evidence:" if reproposal_first else "rediscovery:"):
        # Pause/recover the work queue at each transition before the next dispatch.
        recovered = EvidenceWorkQueue(index)
        recovered.restore(queue.snapshot())
        queue = recovered
        current = next_task
        assert current and current.retry_kind.startswith(expected_kind)
        context = assemble_context(
            target, task.record_id, index, repair_enabled=True,
            required_context_refs=queue.source_refs(current),
        )
        outcome = adapter.verify_existing(current, context, predicate, menu, state)
        state = deepcopy(context.protocol_state)
        next_task = queue.observe(current, outcome, context, predicate, state)
    assert outcome.edges[0].policy_eligible is final_supported
    assert next_task is None
    assert len(calls) == 5  # two discoveries, three independent verifications
    assert sum(r["stage"] == "discovery" for r in calls) == 2
    work = queue.items[task.claim_lineage_id]
    assert work["positive_rechecks"] == work["reproposals"] == 1
    assert work["status"] == ("resolved" if final_supported else "awaiting_evidence")
    assert all(r["target"]["claim_ref"]["id"] == task.claim_lineage_id for r in calls)
    assert state["completed_attempts"] == [1, 2, 3, 4, 5]
    assert adapter.verify_existing(current, context, predicate, menu, state).model_calls == 0
    assert len(calls) == 5
