"""Record answer corrections keep source gates and the original paid-call budget."""

import json
from copy import deepcopy

import pytest

from tests.test_extraction.test_record_model_adapter import setup_record
from tests.test_extraction.test_record_model_adapter import source as source

pytest_plugins = ["tests.test_extraction.test_record_model_adapter"]


def view(request):
    return json.loads(request["input_items"][0]["content"][0]["text"])


def test_field_referent_correction_preserves_exact_paid_answer_before_added_observations(
    source, monkeypatch,
):
    def bad_field_name(answer, context, number):
        if context["stage"] == "discovery" and "answer_correction" not in context:
            answer["entities"][0]["mentions"] = [source["quote"]("数量为5 mg")]

    adapter, task, context, card, stored, requests, _ = setup_record(
        source, monkeypatch, transform=bad_field_name,
    )
    unit = source["source_unit"]
    def anchor(text):
        start = unit.text.index(text)
        return source["index"].ir.anchor(unit.evidence_id, start, start + len(text)).model_dump(
            mode="json",
        )
    context.tool_inputs["property_fields"] = [{
        "field_id": "amount-field", "record_id": task.record_id,
        "label": "数量", "value": "5 mg", "label_refs": [anchor("数量")],
        "value_refs": [anchor("5 mg")], "exclusive_record": False,
    }]
    outcome = adapter.inspect_record(task, context, card)
    assert len(requests) == 3 and len(outcome.nodes) == 2
    correction = view(requests[1])["answer_correction"]
    assert any(issue["reason_code"] == "entity_field_as_name" for issue in correction["issues"])
    assert correction["previous_answer"]["observations"] == []
    frozen = stored["results"][stored["protocol"]["discovery_ref"]]["value"]
    assert any(o["quote"]["text"] == "5 mg" for o in frozen["observations"])


def incomplete_support(answer, context, *, composition=False):
    target = next(target for target in context["verification_input"]["targets"]
                  if target["payload"]["local_id"] == ("b" if composition else "pb"))
    result = next(result for result in answer["verifications"]
                  if result["target_id"] == target["target_id"])
    facet = next(facet for facet in result["facets"]
                 if facet["name"] == ("referent" if composition else "qualifiers"))
    if composition:
        facet["support"] = [target["payload"]["record_components"][0]["quote"]]
    else:
        facet["support"] = []


def test_source_mismatch_is_in_same_single_correction_without_rewriting_paid_answer(
    source, monkeypatch,
):
    def wrong_source(answer, context, number):
        if context["stage"] == "discovery" and "answer_correction" not in context:
            answer["entities"][0]["mentions"][0]["text"] = "原文中不存在的对象"

    adapter, task, context, card, _, requests, _ = setup_record(
        source, monkeypatch, transform=wrong_source,
    )
    outcome = adapter.inspect_record(task, context, card)
    assert len(requests) == 3 and len(outcome.nodes) == 2
    correction = view(requests[1])["answer_correction"]
    assert any(issue["reason_code"] == "source_excerpt_mismatch"
               for issue in correction["issues"])
    assert correction["previous_answer"]["entities"][0]["mentions"][0]["text"] == (
        "原文中不存在的对象"
    )


@pytest.mark.parametrize("composition", [False, True])
def test_discovery_corrects_once_and_verification_gap_is_downgraded_locally(
    source, monkeypatch, composition,
):
    def mistakes(answer, context, number):
        if "answer_correction" in context:
            return
        if context["stage"] == "discovery":
            answer["properties"][0]["bridge_ref_ids"] = [source["source_unit"].evidence_id]
        else:
            incomplete_support(answer, context, composition=composition)

    adapter, task, context, card, stored, requests, proposal = setup_record(
        source, monkeypatch, budget=4, transform=mistakes,
    )
    if composition:
        proposal["entities"][0].update(representation="record", mentions=[], record_components=[
            {"role": role, "quote": source["quote"](text)}
            for role, text in (("subject", "对象乙"), ("field", "数量"), ("value", "5"))
        ])
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete
    assert len(outcome.nodes) == (1 if composition else 2)
    assert outcome.model_calls == len(requests) == len(stored["reservations"]) == 3
    discovery, verification = view(requests[1]), view(requests[2])
    assert "bridge_reference_missing" in json.dumps(discovery["answer_correction"])
    reason = ("record_composition_source_coverage_missing" if composition
              else "qualifiers:support_missing")
    assert reason in outcome.reason
    assert "answer_correction" not in verification
    assert len(discovery["answer_correction"]["previous_answer"]["entities"]) == 2
    assert len(discovery["answer_correction"]["previous_answer"]["properties"]) == 2
    for field in ("evidence_units", "schema_card", "context_hash"):
        assert discovery[field] == view(requests[0])[field]
    assert not requests[1].get("tools")
    for request in requests[:2]:
        assert request["text_format"]["schema"]["$defs"]["PropertyProposal"][
            "properties"]["bridge_ref_ids"]["maxItems"] == 0
        assert "resolved_reference_chain" not in request["text_format"]["schema"]["$defs"][
            "PropertyProposal"
        ]["properties"]["bridge_kind"]["enum"]
    assert "不得使用record_id、evidence_id" in requests[0]["instructions"]
    assert "不要调用inspect_evidence重复读取" in requests[0]["instructions"]


@pytest.mark.parametrize("stage,budget,calls", [
    ("discovery", 2, 2), ("discovery", 4, 3),
    ("verification", 2, 2), ("verification", 4, 2),
])
def test_uncorrected_claims_stay_rejected_and_keep_independent_claims(
    source, monkeypatch, stage, budget, calls,
):
    def repeat_error(answer, context, number):
        if context["stage"] != stage:
            return
        if stage == "discovery":
            answer["properties"][0]["bridge_ref_ids"] = [source["record_id"]]
        else:
            incomplete_support(answer, context)

    adapter, task, context, card, stored, requests, _ = setup_record(
        source, monkeypatch, budget=budget, transform=repeat_error,
    )
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and len(outcome.nodes) == 2 and len(outcome.properties) == 1
    reason = "bridge_reference_missing" if stage == "discovery" else "qualifiers:support_missing"
    assert reason in outcome.reason
    assert outcome.model_calls == len(requests) == len(stored["reservations"]) == calls
    corrections = [request for request in requests if "answer_correction" in view(request)]
    assert len(corrections) == (1 if stage == "discovery" and budget == 4 else 0)


@pytest.mark.parametrize("pause_after_answer", [False, True])
def test_correction_cold_resume_does_not_repeat_paid_answer(
    source, monkeypatch, pause_after_answer,
):
    stage = "discovery"
    def mistake(answer, context, number):
        if context["stage"] != stage or "answer_correction" in context:
            return
        answer["properties"][0]["bridge_ref_ids"] = [source["source_unit"].evidence_id]

    adapter, task, context, card, saved, paid, _ = setup_record(
        source, monkeypatch, budget=4, transform=mistake,
    )
    checkpoint = context._protocol_hook

    def pause(value):
        checkpoint(value)
        items = value["stage_input_items"]
        correcting = bool(items and "answer_correction" in json.loads(
            items[0]["content"][0]["text"],
        ))
        if value["stage"] == stage and correcting:
            changes = value.get("result_changes", {})
            if (not pause_after_answer or any(row["field"] == "model_turn"
                                              for row in changes.values())):
                raise RuntimeError("pause with correction")

    context.bind_protocol_hook(pause)
    with pytest.raises(RuntimeError, match="pause with correction"):
        adapter.inspect_record(task, context, card)
    paid_count = len(paid)
    saved = deepcopy(saved)
    assert saved["protocol"][f"{stage}_ref"] is None
    adapter, task, context, card, stored, resumed, _ = setup_record(
        source, monkeypatch, budget=4 - paid_count, transform=mistake,
    )
    stored.update(deepcopy(saved))
    context.protocol_state = deepcopy(saved["protocol"])
    context.protocol_results = deepcopy(saved["results"])
    outcome = adapter.inspect_record(task, context, card)
    assert outcome.complete and len(outcome.nodes) == len(outcome.properties) == 2
    assert paid_count + len(resumed) == len(stored["reservations"]) == 3
    assert stored["protocol"]["request_attempt"] == 3
    assert all(stored["results"][key] == value for key, value in saved["results"].items())
    if not pause_after_answer:
        assert "answer_correction" in view(resumed[0])


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("denied_by_verifier", [False, True])
def test_empty_correction_rechecks_original_candidates_without_accepting_them(
    source, monkeypatch, resume, denied_by_verifier,
):
    def empty_correction(answer, context, number):
        if context["stage"] == "discovery":
            if "answer_correction" in context:
                for field in answer:
                    answer[field] = []
            else:
                answer["properties"][0]["bridge_ref_ids"] = [source["source_unit"].evidence_id]
        elif denied_by_verifier:
            for result in answer["verifications"]:
                for facet in result["facets"]:
                    facet["verdict"] = "unsupported"

    adapter, task, context, card, stored, requests, _ = setup_record(
        source, monkeypatch, budget=4, transform=empty_correction,
    )
    paid_count = 0
    if resume:
        checkpoint = context._protocol_hook

        def pause(value):
            checkpoint(value)
            if value["request_attempt"] == 2 and any(
                row["field"] == "model_turn" for row in value.get("result_changes", {}).values()
            ):
                raise RuntimeError("pause after empty correction")

        context.bind_protocol_hook(pause)
        with pytest.raises(RuntimeError, match="pause after empty correction"):
            adapter.inspect_record(task, context, card)
        saved, paid_count = deepcopy(stored), len(requests)
        adapter, task, context, card, stored, requests, _ = setup_record(
            source, monkeypatch, budget=4 - paid_count, transform=empty_correction,
        )
        stored.update(deepcopy(saved))
        context.protocol_state = deepcopy(saved["protocol"])
        context.protocol_results = deepcopy(saved["results"])

    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and "bridge_reference_missing" in outcome.reason
    assert len(outcome.nodes) == (0 if denied_by_verifier else 2)
    assert len(outcome.properties) == (0 if denied_by_verifier else 1)
    assert paid_count + len(requests) == len(stored["reservations"]) == 3
    verification = view(requests[-1])["verification_input"]
    assert {target["payload"]["local_id"] for target in verification["targets"]} == {"b", "c", "pc"}
    frozen = stored["results"][stored["protocol"]["discovery_ref"]]["value"]
    assert len(frozen["entities"]) == len(frozen["properties"]) == 2
    assert frozen["claim_issues"] == {"pb": ["bridge_reference_missing"]}


@pytest.mark.parametrize("capacity", ["input", "context"])
def test_oversized_correction_keeps_original_claims_for_normal_gates(
    source, monkeypatch, capacity,
):
    stage = "discovery"
    def mistake(answer, context, number):
        if context["stage"] != stage or "answer_correction" in context:
            return
        answer["properties"][0]["bridge_ref_ids"] = [source["record_id"]]
        # Keep the oversized-answer boundary after schema/context compression.
        answer["observations"].append({
            "subject_id": None, "predicate_iri": None,
            "quote": deepcopy(answer["properties"][0]["value_quote"]),
            "kind": "unknown", "reason": "核查原文归属及字段。" * 1000,
        })

    def setup():
        configured = setup_record(source, monkeypatch, budget=4, transform=mistake)
        adapter = configured[0]
        adapter.max_output_tokens = 16384
        measured = []

        def counter(value):
            request = json.loads(value)
            if isinstance(request, dict) and "model" in request and "input" in request:
                payload = json.loads(request["input"][0]["content"][0]["text"])
                measured.append(("answer_correction" in payload, len(value)))
            return len(value)

        adapter.token_counter = counter
        return (*configured, measured)

    adapter, task, context, card, _, _, _, measured = setup()
    assert adapter.inspect_record(task, context, card).complete
    original_size = max(size for correcting, size in measured if not correcting)
    correction_size = min(size for correcting, size in measured if correcting)
    assert correction_size > original_size
    limit = (original_size + correction_size) // 2

    adapter, task, context, card, stored, requests, _, measured = setup()
    if capacity == "input":
        adapter.max_input_tokens = limit
    else:
        adapter.max_context_tokens = limit + adapter.max_output_tokens
    outcome = adapter.inspect_record(task, context, card)
    assert not outcome.complete and len(outcome.nodes) == 2 and len(outcome.properties) == 1
    assert "bridge_reference_missing" in outcome.reason
    assert outcome.model_calls == len(requests) == len(stored["reservations"]) == 2
    assert any(correcting and size > limit for correcting, size in measured)
    assert all("answer_correction" not in view(request) for request in requests)
    assert stored["protocol"]["discovery_ref"] and stored["protocol"]["verification_ref"]
